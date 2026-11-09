"""Only SQLAlchemy drives verification, quality checks and in-memory SQLite tests."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from sqlalchemy import inspect
from cadeo.brokers.execution_broker import ExecutionBroker
from cadeo.models.specification import SpecificationValidationError
from tests.support import sqlalchemy_connection, split_controlled_test_sql


class UnifiedExecutionBrokerTests(TestCase):
    def test_in_memory_sqlite_via_sqlalchemy(self):
        with sqlalchemy_connection(':memory:') as conn:
            conn.exec_driver_sql('CREATE TABLE entries (id TEXT NOT NULL)')
            conn.exec_driver_sql('INSERT INTO entries VALUES (?)', ('a',))
            self.assertEqual(conn.exec_driver_sql('SELECT id FROM entries').scalar_one(), 'a')
            self.assertIn('entries', inspect(conn).get_table_names())

    def test_quality_check_is_executed_through_sqlalchemy(self):
        broker = ExecutionBroker()
        with sqlalchemy_connection(':memory:') as conn, mock.patch.object(
            ExecutionBroker, 'statements', staticmethod(split_controlled_test_sql)):
            checks = 'SELECT 0 AS failures;\nSELECT 2 AS failures;\n'
            self.assertEqual(list(broker.check_failures(conn, checks, 'sqlite', ('ok','bad'))), [('ok',0),('bad',2)])

    def test_first_failing_check_prevents_executing_invalid_later_sql(self):
        """Regression: the second query must never mask the first business failure."""
        broker = ExecutionBroker()
        with sqlalchemy_connection(':memory:') as conn, mock.patch.object(
                ExecutionBroker, 'statements', staticmethod(split_controlled_test_sql)):
            sql = ('SELECT 3 AS failures;\n'
                   'SELECT COUNT(*) FROM nonexistent_later_check;\n')
            results = list(broker.check_failures(conn, sql, 'sqlite', ('invalid_status', 'should_not_run')))
            self.assertEqual(results, [('invalid_status', 3)])

    def test_success_executes_every_check_in_order(self):
        with sqlalchemy_connection(':memory:') as conn, mock.patch.object(
                ExecutionBroker, 'statements', staticmethod(split_controlled_test_sql)):
            results = list(ExecutionBroker().check_failures(
                conn, 'SELECT 0 AS failures;\nSELECT 0 AS failures;\n', 'sqlite', ('first', 'second')))
            self.assertEqual(results, [('first', 0), ('second', 0)])

    def test_wrong_result_shape_fails_closed(self):
        broker = ExecutionBroker()
        with sqlalchemy_connection(':memory:') as conn, mock.patch.object(
            ExecutionBroker, 'statements', staticmethod(split_controlled_test_sql)):
            with self.assertRaisesRegex(SpecificationValidationError, 'exactly one scalar'):
                list(broker.check_failures(conn, 'SELECT 1, 2;', 'sqlite', ('bad',)))
            with self.assertRaisesRegex(SpecificationValidationError, 'nonnegative integer'):
                list(broker.check_failures(conn, 'SELECT -1;', 'sqlite', ('bad',)))
            with self.assertRaisesRegex(SpecificationValidationError, 'Expected 2'):
                list(broker.check_failures(conn, 'SELECT 0;', 'sqlite', ('one', 'two')))

    def test_missing_sqlite_database_rejected_without_creation(self):
        with TemporaryDirectory() as folder:
            database = Path(folder) / 'missing.sqlite'
            with self.assertRaisesRegex(SpecificationValidationError, 'does not exist'):
                with ExecutionBroker().transaction(f'sqlite:///{database}'):
                    pass
            self.assertFalse(database.exists())

    def test_rollback_on_error_preserves_prior_view(self):
        with TemporaryDirectory() as folder:
            database = Path(folder) / 'warehouse.sqlite'
            with sqlalchemy_connection(database) as conn:
                conn.exec_driver_sql('CREATE TABLE version_one (value TEXT)')
                conn.exec_driver_sql("INSERT INTO version_one VALUES ('old')")
                conn.exec_driver_sql('CREATE VIEW published AS SELECT * FROM version_one')
            with self.assertRaisesRegex(RuntimeError, 'failed gate'):
                with sqlalchemy_connection(database) as conn:
                    ExecutionBroker.prepare_publication(conn, 'published')
                    conn.exec_driver_sql('CREATE VIEW published AS SELECT \'new\' AS value')
                    raise RuntimeError('failed gate')
            with sqlalchemy_connection(database) as conn:
                self.assertEqual(conn.exec_driver_sql('SELECT value FROM published').scalar_one(), 'old')
