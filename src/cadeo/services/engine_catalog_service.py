"""SQLAlchemy engine catalogue and *runtime* driver capability checks.

The catalogue lists projects from the SQLAlchemy documentation, not supported
execution engines. Every availability decision is made in the CURRENT Python
environment. SQLAlchemy entry points and their DBAPI drivers must actually load.
Safe publication is a separate, intentionally narrower capability.
"""

from __future__ import annotations

from importlib import metadata, resources
import json
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.dialects import registry
from sqlalchemy.engine import make_url

from cadeo.models.specification import SpecificationValidationError


# These transaction strategies have been explicitly implemented in ExecutionBroker.
# Adding a SQLAlchemy dialect or DBAPI does NOT expand this set automatically.
PUBLICATION_STRATEGIES = {
    "sqlite": "transactional_ddl",
    "postgresql": "transactional_ddl",
    "duckdb": "transactional_ddl_probed",
}
# Schema reflection is independently supported by these SQLAlchemy backends.
# This capability does not certify SQL compilation or safe publication.
SCHEMA_INTROSPECTION_BACKENDS = {
    "sqlite", "postgresql", "mysql", "mariadb", "oracle", "mssql", "duckdb",
}
# Only map equivalent dialect names; no generic ANSI fallback. Actual support
# still requires SQLGlot AND SQLFluff to recognise the output dialect.
SQLGLOT_FOR_BACKEND = {
    "sqlite": "sqlite", "postgresql": "postgres", "duckdb": "duckdb", "mysql": "mysql",
    "oracle": "oracle", "mssql": "tsql", "athena": "athena",
    "bigquery": "bigquery", "clickhouse": "clickhouse",
    "databricks": "databricks", "redshift": "redshift",
    "snowflake": "snowflake", "hive": "hive",
    "teradata": "teradata", "exa": "exasol",
}


def _package_key(value: str) -> str:
    return ''.join(c for c in value.lower() if c.isalnum())


class EngineCatalogService:
    """Advertise install choices, inspect plugins and test real URL availability."""

    @staticmethod
    def catalog() -> list[dict[str, Any]]:
        text = resources.files('cadeo').joinpath('schemas/sqlalchemy_engines.json').read_text(encoding='utf-8')
        return json.loads(text)['engines']

    @staticmethod
    def entry_points() -> dict[str, list[str]]:
        """Installed dialect entry points keyed by their owning distribution."""
        result: dict[str, list[str]] = {}
        for point in metadata.entry_points(group='sqlalchemy.dialects'):
            dist = getattr(point, 'dist', None)
            package = dist.metadata.get('Name', '') if dist is not None else ''
            result.setdefault(_package_key(package), []).append(point.name)
        return result

    @classmethod
    def duckdb_driver_status(cls) -> tuple[bool, str]:
        """Require the selected DuckDB SQLAlchemy distribution, not a competing plugin."""
        try:
            metadata.version('duckdb-sqlalchemy')
        except metadata.PackageNotFoundError:
            return False, 'duckdb_sqlalchemy_not_installed'
        try:
            metadata.version('duckdb-engine')
        except metadata.PackageNotFoundError:
            pass
        else:
            return False, 'conflicting_duckdb_dialects'
        if 'duckdb' not in cls.entry_points().get(_package_key('duckdb-sqlalchemy'), []):
            return False, 'duckdb_sqlalchemy_dialect_not_registered'
        return True, 'duckdb_sqlalchemy_registered'

    @classmethod
    def require_duckdb_driver(cls) -> None:
        ok, reason = cls.duckdb_driver_status()
        if not ok:
            raise SpecificationValidationError(
                f"DuckDB native build requires the duckdb-sqlalchemy package ({reason}); "
                "do not install duckdb-engine alongside it")

    @staticmethod
    def _driver_loads(name: str) -> tuple[bool, str]:
        """Load both the SQLAlchemy dialect and its actual DBAPI without connecting."""
        try:
            dialect_class = registry.load(name)
        except Exception as exc:
            return False, f'dialect_not_loadable:{type(exc).__name__}'
        try:
            dialect_class.import_dbapi()
        except Exception as exc:
            return False, f'driver_not_loadable:{type(exc).__name__}'
        return True, 'driver_loaded'

    def available(self, *, installed_only: bool = False) -> list[dict[str, Any]]:
        entry_points = self.entry_points()
        # The presence of SQLGlot/SQLFluff, and their mutual dialect support, is
        # checked once for the whole catalogue, not inferred from database drivers.
        try:
            from cadeo.services.dialect_service import DialectService
            compile_dialects = set(DialectService.available())
        except SpecificationValidationError:
            compile_dialects = set()
        items: list[dict[str, Any]] = []
        for entry in self.catalog():
            package = entry['install']
            package_name = package.split('[', 1)[0] if package else None
            package_installed = False
            if package_name is not None:
                try:
                    metadata.version(package_name)
                    package_installed = True
                except metadata.PackageNotFoundError:
                    pass
            if entry['kind'] == 'included':
                drivers = [entry['url_driver']]
            else:
                # An external package may expose multiple SQLAlchemy URL names.
                # Read the *actual installed entry points*, not guessed URLs.
                drivers = entry_points.get(_package_key(package_name or ''), []) if package_installed else []
            statuses = [(name, self._driver_loads(name)) for name in drivers]
            usable = next((name for name, (ok, _) in statuses if ok), None)
            duckdb_error = None
            if entry['id'] == 'duckdb':
                compatible, reason = self.duckdb_driver_status()
                if not compatible:
                    usable, duckdb_error = None, reason
            error = None if usable else (duckdb_error or (statuses[0][1][1] if statuses else 'dialect_not_installed'))
            backend = entry['url_driver'].split('+', 1)[0]
            sql_dialect = SQLGLOT_FOR_BACKEND.get(backend)
            publication_strategy = PUBLICATION_STRATEGIES.get(backend)
            result = {
                'id': entry['id'], 'name': entry['label'], 'kind': entry['kind'],
                'package': package, 'install_extra': entry['id'] if package else None,
                'install_command': f'python -m pip install "cadeo[{entry["id"]}]"' if package else None,
                'package_installed': package_installed if package else True,
                'url_driver': usable or entry['url_driver'],
                'driver_loaded': usable is not None,
                'status': 'driver_loaded' if usable else error,
                'publication_strategy': publication_strategy,
                'schema_introspection_capable': bool(usable and backend in SCHEMA_INTROSPECTION_BACKENDS),
                'sql_dialect': sql_dialect,
                'compiler_ready': bool(sql_dialect and sql_dialect in compile_dialects),
                'build_ready': bool(usable and publication_strategy and sql_dialect in compile_dialects),
                'note': entry.get('note'),
                'runtime_publication_probe': backend == 'duckdb',
            }
            if installed_only and not result['driver_loaded']:
                continue
            items.append(result)
        return items

    @staticmethod
    def inspect_url(url: str, *, connect: bool = False) -> dict[str, Any]:
        """Instantiate the exact chosen driver; optionally attempt a live connection.

        Never include URL contents, driver messages or credentials in the report.
        """
        try:
            parsed = make_url(url)
        except Exception as exc:
            raise SpecificationValidationError('Invalid SQLAlchemy URL') from exc
        backend = parsed.get_backend_name()
        drivername = parsed.drivername
        result: dict[str, Any] = {
            'backend': backend, 'driver': drivername,
            'driver_loaded': False, 'connected': None,
            'publication_strategy': PUBLICATION_STRATEGIES.get(backend),
            'schema_introspection_capable': False,
            'build_capable': False, 'compiler_ready': False,
            'sql_dialect': SQLGLOT_FOR_BACKEND.get(backend), 'error': None,
            'runtime_publication_probe': backend == 'duckdb',
        }
        engine = None
        try:
            engine = create_engine(url)  # Loads BOTH dialect plugin and DBAPI.
            result['driver_loaded'] = True
            result['schema_introspection_capable'] = backend in SCHEMA_INTROSPECTION_BACKENDS
            if connect:
                with engine.connect():
                    result['connected'] = True
            sql_dialect = SQLGLOT_FOR_BACKEND.get(backend)
            try:
                from cadeo.services.dialect_service import DialectService
                compiler_ready = bool(sql_dialect and sql_dialect in DialectService.available())
            except SpecificationValidationError:
                compiler_ready = False
            result['sql_dialect'] = sql_dialect
            result['compiler_ready'] = compiler_ready
            result['build_capable'] = backend in PUBLICATION_STRATEGIES and compiler_ready
            if backend == 'duckdb':
                compatible, reason = EngineCatalogService.duckdb_driver_status()
                if not compatible:
                    result['build_capable'] = False
                    result['error'] = reason
        except Exception as exc:
            result['error'] = f'{type(exc).__name__}'  # No secret-bearing messages.
            if connect and result['driver_loaded']:
                result['connected'] = False
        finally:
            if engine is not None:
                engine.dispose()
        return result

    @staticmethod
    def require_safe_backend(backend: str) -> None:
        if backend not in PUBLICATION_STRATEGIES:
            raise SpecificationValidationError(
                f"Safe publication is not verified for SQLAlchemy backend '{backend}'. "
                'A loadable database driver allows discovery, but is not proof of transactional DDL. '
                'Native build remains restricted to independently verified publication strategies.'
            )

    @staticmethod
    def expected_output_dialect(backend: str) -> str:
        EngineCatalogService.require_safe_backend(backend)
        return SQLGLOT_FOR_BACKEND[backend]
