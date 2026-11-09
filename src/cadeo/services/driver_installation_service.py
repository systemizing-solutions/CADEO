"""Explicit, catalogue-restricted pip installation for the local editor.

Installing packages is a local administrative action, never a compiler or SQL
execution mechanism. The web API accepts ONLY a known catalogue ID. It cannot
accept pip arguments, package URLs, indexes, or arbitrary package names.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from threading import Lock, Thread
from typing import Any
from uuid import uuid4

from cadeo.models.specification import SpecificationValidationError
from cadeo.services.engine_catalog_service import EngineCatalogService


class DriverInstallationService:
    """Install at most one approved driver package at a time in this interpreter.

    A non-blocking job lets the UI poll for completion; jobs are ephemeral and
    deliberately scoped to the local editor process. We do not expose raw pip
    logs, as configured package indexes may contain credentials.
    """

    TIMEOUT_SECONDS = 300

    def __init__(self, catalog: EngineCatalogService | None = None):
        self.catalog = catalog or EngineCatalogService()
        self._lock = Lock()
        self._job: dict[str, Any] | None = None

    @staticmethod
    def interpreter() -> str:
        return sys.executable

    @staticmethod
    def virtual_environment() -> bool:
        return sys.prefix != sys.base_prefix

    def current(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._job) if self._job else None

    def start(self, engine_id: str, *, confirmed: bool) -> dict[str, Any]:
        if confirmed is not True:
            raise SpecificationValidationError('Confirm the driver installation before proceeding')
        if not isinstance(engine_id, str):
            raise SpecificationValidationError('A catalogue driver ID is required')

        # Strict allow-list. Do not interpolate an HTTP-provided string into pip.
        entry = next((item for item in self.catalog.catalog() if item['id'] == engine_id), None)
        if entry is None:
            raise SpecificationValidationError('Unknown SQLAlchemy driver ID')
        package = entry.get('install')
        if not package:
            raise SpecificationValidationError('This driver is bundled with Python; no installation is needed')

        with self._lock:
            if self._job is not None and self._job['status'] == 'installing':
                raise SpecificationValidationError('Another driver installation is already running')
            # Never return success based on pip's exit code alone.
            available = next(item for item in self.catalog.available() if item['id'] == engine_id)
            if available['driver_loaded']:
                return {'status': 'already_installed', 'engine_id': engine_id,
                        'driver_loaded': True, 'interpreter': self.interpreter()}
            self._job = {
                'id': uuid4().hex,
                'engine_id': engine_id,
                'package': package,
                'status': 'installing',
                'interpreter': self.interpreter(),
                'driver_loaded': False,
                'message': 'Installing the selected catalogue driver',
            }
            job = dict(self._job)
            Thread(target=self._install, args=(job['id'], engine_id, package), daemon=True).start()
            return job

    def _install(self, job_id: str, engine_id: str, package: str) -> None:
        # subprocess is used ONLY to invoke pip; all database work continues
        # through the existing SQLAlchemy execution broker and services.
        command = [sys.executable, '-m', 'pip', 'install',
                   '--disable-pip-version-check', '--no-input', package]
        env = os.environ.copy()
        env['PIP_NO_INPUT'] = '1'
        env['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
        env['PIP_PROGRESS_BAR'] = 'off'
        result_status = 'failed'
        loaded = False
        message = 'Driver installation failed'
        try:
            completed = subprocess.run(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=self.TIMEOUT_SECONDS,
                check=False, env=env,
            )
            if completed.returncode == 0:
                importlib.invalidate_caches()
                # Query actual SQLAlchemy registration and DBAPI import in the
                # current interpreter; a successfully installed package may still
                # lack shared libraries or a compatible SQLAlchemy entry point.
                row = next((row for row in self.catalog.available()
                            if row['id'] == engine_id), None)
                loaded = bool(row and row['driver_loaded'])
                if loaded:
                    result_status = 'succeeded'
                    message = 'Driver installed and loaded by SQLAlchemy'
                else:
                    result_status = 'installed_unavailable'
                    message = ('pip completed, but SQLAlchemy could not load the dialect/DBAPI. '
                               'Restart the editor and check driver requirements.')
            else:
                message = (f'pip exited with code {completed.returncode}. '
                           'Check the package compatibility and rerun the install command in a terminal for details.')
        except subprocess.TimeoutExpired:
            message = 'Driver installation timed out; inspect your environment before retrying'
        except Exception as error:
            # Raw exception messages can include package-index credentials.
            message = f'Unable to install driver ({type(error).__name__})'
        finally:
            with self._lock:
                if self._job and self._job['id'] == job_id:
                    self._job.update(status=result_status, driver_loaded=loaded, message=message)

    def is_installing(self) -> bool:
        with self._lock:
            return bool(self._job and self._job['status'] == 'installing')
