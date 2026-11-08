"""v0.7.6: catalogue entries are NOT synonymous with usable drivers or safe builds."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from sqlalchemy import inspect

from cade.brokers.execution_broker import ExecutionBroker
from cade.exposers.cli import main
from cade.models.specification import SpecificationValidationError
from cade.services.engine_catalog_service import EngineCatalogService
from tests.support import sqlalchemy_connection

ROOT = Path(__file__).resolve().parents[1]


class EngineCatalogTests(TestCase):
    def test_all_official_included_and_external_dialects_are_catalogued(self):
        items = EngineCatalogService.catalog()
        self.assertEqual(52, len(items))
        self.assertEqual(6, sum(x['kind'] == 'included' for x in items))
        self.assertEqual(46, sum(x['kind'] == 'external' for x in items))
        self.assertEqual(len(items), len({item['id'] for item in items}))
        for name in ('sqlite','postgresql','mysql','mariadb','oracle','mssql',
                     'athena','databricks','bigquery','snowflake','redshift',
                     'elasticsearch','netezza','redis','yugabyte'):
            self.assertIn(name, {item['id'] for item in items})

    def test_every_optional_driver_is_a_separate_install_extra(self):
        import tomllib
        with (ROOT / 'pyproject.toml').open('rb') as handle:
            metadata = tomllib.load(handle)
        options = metadata['project']['optional-dependencies']
        for item in EngineCatalogService.catalog():
            if item['install']:
                self.assertEqual([item['install']], options[item['id']])
            else:
                self.assertNotIn(item['id'], options)

    def test_package_installed_is_not_enough_driver_must_load(self):
        # Registration could exist while the DBAPI's native dependencies are missing.
        with mock.patch.object(EngineCatalogService, 'entry_points', return_value={
            'snowflakesqlalchemy': ['snowflake']}), mock.patch(
            'cade.services.engine_catalog_service.metadata.version', return_value='1.0'
        ), mock.patch.object(EngineCatalogService, '_driver_loads', return_value=(False, 'driver_not_loadable:ImportError')):
            rows = EngineCatalogService().available()
        snowflake = next(x for x in rows if x['id']=='snowflake')
        self.assertTrue(snowflake['package_installed'])
        self.assertFalse(snowflake['driver_loaded'])
        self.assertFalse(snowflake['build_ready'])
        self.assertEqual('driver_not_loadable:ImportError',snowflake['status'])

    def test_driver_loaded_does_not_override_safe_publication_policy(self):
        with mock.patch.object(EngineCatalogService, 'entry_points', return_value={
            'snowflakesqlalchemy': ['snowflake']}), mock.patch(
            'cade.services.engine_catalog_service.metadata.version', return_value='1.0'
        ), mock.patch.object(EngineCatalogService, '_driver_loads', return_value=(True,'driver_loaded')):
            rows = EngineCatalogService().available(installed_only=True)
        snowflake = next(x for x in rows if x['id']=='snowflake')
        self.assertTrue(snowflake['driver_loaded'])
        self.assertFalse(snowflake['build_ready'])
        self.assertIsNone(snowflake['publication_strategy'])
        with mock.patch('cade.brokers.execution_broker.create_engine') as engine:
            with self.assertRaisesRegex(SpecificationValidationError, 'Safe publication'):
                with ExecutionBroker().transaction('snowflake://user:password@example.invalid/test'):
                    pass
            engine.assert_not_called()

    def test_sqlite_driver_is_loadable_through_sqlalchemy_and_connects(self):
        sample = EngineCatalogService.inspect_url('sqlite:///:memory:', connect=True)
        self.assertTrue(sample['driver_loaded'])
        self.assertTrue(sample['connected'])
        self.assertEqual('transactional_ddl', sample['publication_strategy'])
        self.assertEqual('sqlite', sample['sql_dialect'])
        with sqlalchemy_connection(':memory:') as conn:
            conn.exec_driver_sql('CREATE TABLE device(id TEXT)')
            self.assertTrue(inspect(conn).has_table('device'))

    def test_bad_driver_error_does_not_expose_password(self):
        value = EngineCatalogService.inspect_url(
            'postgresql+unknown_driver://alice:extremely_secret@localhost/database')
        self.assertFalse(value['driver_loaded'])
        self.assertIn('error', value)
        self.assertNotIn('extremely_secret', json.dumps(value))
        self.assertNotIn('localhost', json.dumps(value))

    def test_cli_engines_json_and_selected_url_do_not_show_secrets(self):
        import contextlib
        from io import StringIO
        output = StringIO()
        with mock.patch.dict(os.environ, {'TEST_DB_URL': 'sqlite:///:memory:'}), contextlib.redirect_stdout(output):
            code = main(['engines','--installed','--url-env','TEST_DB_URL','--connect','--json'])
        self.assertEqual(0, code)
        payload = json.loads(output.getvalue())
        self.assertTrue(any(row['id'] == 'sqlite' for row in payload['engines']))
        self.assertTrue(payload['selected']['driver_loaded'])
        self.assertTrue(payload['selected']['connected'])
        self.assertNotIn('sqlite:///',output.getvalue())

    def test_cli_live_connection_requires_explicit_environment_variable(self):
        import contextlib
        from io import StringIO
        with mock.patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(StringIO()) as stream:
            self.assertEqual(1, main(['engines','--url-env','ABSENT_DB_URL']))
        self.assertIn('ABSENT_DB_URL',stream.getvalue())

    def test_sqlite_safety_still_preserves_a_previous_view(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'db.sqlite'
            with sqlalchemy_connection(path) as conn:
                conn.exec_driver_sql('CREATE VIEW published AS SELECT 42 AS value')
            with self.assertRaisesRegex(RuntimeError,'failure'):
                with ExecutionBroker().transaction(f'sqlite:///{path}') as conn:
                    ExecutionBroker.prepare_publication(conn,'published')
                    conn.exec_driver_sql('CREATE VIEW published AS SELECT 99 AS value')
                    raise RuntimeError('failure')
            with sqlalchemy_connection(path) as conn:
                self.assertEqual(42,conn.exec_driver_sql('SELECT value FROM published').scalar_one())
