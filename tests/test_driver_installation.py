"""v0.7.6: UI installs only catalogue-defined dependencies via pip."""

import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from cade.models.specification import SpecificationValidationError
from cade.services.driver_installation_service import DriverInstallationService


class FakeCatalog:
    def __init__(self):
        self.driver_loaded = False
        self.entries = [
            {'id': 'postgresql', 'install': 'psycopg[binary]', 'label': 'PostgreSQL'},
            {'id': 'sqlite', 'install': None, 'label': 'SQLite'},
        ]

    def catalog(self):
        return self.entries

    def available(self):
        return [{'id': item['id'], 'driver_loaded': self.driver_loaded
                 if item['id'] == 'postgresql' else True} for item in self.entries]


class DriverInstallationTests(TestCase):
    def setUp(self):
        self.catalog = FakeCatalog()
        self.service = DriverInstallationService(self.catalog)

    def await_job(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = self.service.current()
            if job and job['status'] != 'installing':
                return job
            time.sleep(0.01)
        self.fail('Timed out waiting for mock installer')

    def test_unknown_package_ids_and_unconfirmed_requests_cannot_invoke_pip(self):
        with mock.patch('cade.services.driver_installation_service.subprocess.run') as run:
            for value in ('custom-package', 'postgresql; --index-url https://evil.example',
                          '../data.txt', '', 1, None):
                with self.assertRaises(SpecificationValidationError):
                    self.service.start(value, confirmed=True)
            with self.assertRaises(SpecificationValidationError):
                self.service.start('sqlite', confirmed=True)
            with self.assertRaises(SpecificationValidationError):
                self.service.start('postgresql', confirmed=False)
            run.assert_not_called()

    def test_success_invokes_pip_in_active_python_without_shell_or_arbitrary_flags(self):
        def pip_run(arguments, **options):
            self.assertEqual(arguments, [sys.executable, '-m', 'pip', 'install',
                                        '--disable-pip-version-check', '--no-input', 'psycopg[binary]'])
            self.assertIs(options['stdin'], subprocess.DEVNULL)
            self.assertIs(options['stdout'], subprocess.DEVNULL)
            self.assertIs(options['stderr'], subprocess.DEVNULL)
            self.assertFalse(options['check'])
            self.assertNotIn('shell', options)
            self.catalog.driver_loaded = True
            return subprocess.CompletedProcess(arguments, 0)

        with mock.patch('cade.services.driver_installation_service.subprocess.run', side_effect=pip_run) as run:
            result = self.service.start('postgresql', confirmed=True)
            self.assertEqual(result['status'], 'installing')
            final = self.await_job()
            self.assertEqual(final['status'], 'succeeded')
            self.assertTrue(final['driver_loaded'])
            self.assertEqual(final['id'], result['id'])
            run.assert_called_once()
        with mock.patch('cade.services.driver_installation_service.subprocess.run') as run:
            self.assertEqual(self.service.start('postgresql', confirmed=True)['status'], 'already_installed')
            run.assert_not_called()

    def test_pip_failure_reports_safe_error_without_driver_success(self):
        with mock.patch('cade.services.driver_installation_service.subprocess.run',
                        return_value=subprocess.CompletedProcess([], 2)):
            self.service.start('postgresql', confirmed=True)
            job = self.await_job()
        self.assertEqual(job['status'], 'failed')
        self.assertFalse(job['driver_loaded'])
        self.assertIn('code 2', job['message'])
        self.assertNotIn('postgresql://', str(job))

    def test_pip_success_without_loadable_dbapi_is_not_success(self):
        with mock.patch('cade.services.driver_installation_service.subprocess.run',
                        return_value=subprocess.CompletedProcess([], 0)):
            self.service.start('postgresql', confirmed=True)
            job = self.await_job()
        self.assertEqual(job['status'], 'installed_unavailable')
        self.assertFalse(job['driver_loaded'])

    def test_concurrent_install_rejected_and_runner_timeout_safe(self):
        from threading import Event
        gate = Event()

        def slow_run(*args, **kwargs):
            gate.wait(2)
            raise subprocess.TimeoutExpired(args[0], 300, output='secret-url')

        with mock.patch('cade.services.driver_installation_service.subprocess.run', side_effect=slow_run):
            self.service.start('postgresql', confirmed=True)
            with self.assertRaisesRegex(SpecificationValidationError, 'already running'):
                self.service.start('postgresql', confirmed=True)
            gate.set()
            job = self.await_job()
        self.assertEqual(job['status'], 'failed')
        self.assertNotIn('secret-url', str(job))

    def test_catalogue_includes_all_install_choices_not_freeform_packages(self):
        from cade.services.engine_catalog_service import EngineCatalogService
        choices = EngineCatalogService.catalog()
        self.assertEqual(52, len(choices))
        self.assertEqual(len(choices), len({row['id'] for row in choices}))
        self.assertIsNone(next(row for row in choices if row['id'] == 'sqlite')['install'])
        self.assertEqual('snowflake-sqlalchemy', next(row for row in choices
                         if row['id'] == 'snowflake')['install'])


class FlaskDriverInstallationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib.util
        if importlib.util.find_spec('flask') is None:
            raise __import__('unittest').SkipTest('Flask not installed')

    def setUp(self):
        from cade.exposers.web import create_app
        self.folder = TemporaryDirectory()
        self.client = create_app(Path(self.folder.name)).test_client()
        self.headers = {'X-Editor-Token': self.client.get('/api/bootstrap').json['token']}

    def tearDown(self):
        self.folder.cleanup()

    def test_endpoints_require_confirmation_and_token_and_reject_arbitrary_packages(self):
        self.assertEqual(self.client.get('/api/engines/installation').status_code, 200)
        self.assertEqual(self.client.post('/api/engines/install', json={
            'engine_id': 'postgresql', 'confirm': True}).status_code, 403)
        self.assertEqual(self.client.post('/api/engines/install', json={
            'engine_id': 'unsupported-package', 'confirm': True}, headers=self.headers).status_code, 422)
        self.assertEqual(self.client.post('/api/engines/install', json={
            'engine_id': 'postgresql', 'confirm': False}, headers=self.headers).status_code, 422)
        self.assertEqual(self.client.post('/api/engines/install', json={
            'engine_id': 'sqlite', 'confirm': True}, headers=self.headers).status_code, 422)

    def test_known_driver_invokes_shared_service_not_untrusted_command(self):
        with mock.patch.object(DriverInstallationService, 'start', return_value={
            'id':'job1','status':'installing','engine_id':'mysql'}) as start:
            response = self.client.post('/api/engines/install', json={
                'engine_id': 'mysql', 'confirm': True}, headers=self.headers)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json['status'], 'installing')
        start.assert_called_once_with('mysql', confirmed=True)
