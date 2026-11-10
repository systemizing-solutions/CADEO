"""Compare registered source contracts with live database metadata and optional profiles."""

from dataclasses import dataclass
import os

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from cadeo.brokers.database_broker import DatabaseBrokerContract, DatabaseInspectionError, LiveTable
from cadeo.models.specification import Source, Specification


@dataclass(frozen=True)
class DiscoveryResult:
    source: str
    table: str
    connection: str | None
    errors: tuple[str, ...]
    observed_columns: dict[str, dict[str, object]]
    extra_columns: tuple[str, ...] = ()
    row_count: int | None = None
    null_counts: dict[str, int] | None = None
    duplicate_key_groups: int | None = None
    skipped_reason: str | None = None

    @property
    def passed(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "source": self.source, "table": self.table, "connection": self.connection,
            "passed": self.passed, "errors": list(self.errors),
            "observed_columns": self.observed_columns, "extra_columns": list(self.extra_columns),
            "row_count": self.row_count, "null_counts": self.null_counts,
            "duplicate_key_groups": self.duplicate_key_groups,
            "skipped_reason": self.skipped_reason,
        }


class DiscoveryService:
    """Live discovery uses the authoritative registered sources; never edits specs."""

    def __init__(self, broker: DatabaseBrokerContract):
        self.broker = broker

    def discover(self, specification: Specification, profile: bool = False) -> tuple[DiscoveryResult, ...]:
        registry = {connection.id: connection.url_env for connection in specification.connections}
        return tuple(self._discover_source(source, registry, profile)
                     if source.product_ref is None else
                     DiscoveryResult(source.id, source.table, None, (), {},
                                     skipped_reason=f"Published output of product '{source.product_ref}' "
                                                    "(verified by project dependency planning, not live database inspection)")
                     for source in specification.sources)

    def _discover_source(self, source: Source, registry: dict[str, str], profile: bool) -> DiscoveryResult:
        if source.connection is None:
            return self._failed(source, "Missing connection; configure sources.<id>.connection and connections")
        variable = registry.get(source.connection)
        if variable is None:
            return self._failed(source, f"Unknown connection: {source.connection}")
        url = os.environ.get(variable)
        if not url:
            return self._failed(source, f"Environment variable {variable} is not set")
        try:
            table = source.table
            if make_url(url).get_backend_name() == 'sqlite' and table.startswith(source.connection + '.'):
                table = table.split('.', 1)[1]
            actual = self.broker.inspect_table(url, table, source.columns, source.key, profile)
        except (ValueError, ArgumentError):
            return self._failed(source, 'Invalid SQLAlchemy source connection URL')
        except DatabaseInspectionError as error:
            return self._failed(source, str(error))
        return self._compare(source, actual, profile)

    @staticmethod
    def _failed(source: Source, message: str) -> DiscoveryResult:
        return DiscoveryResult(source.id, source.table, source.connection, (message,), {})

    @staticmethod
    def _compare(source: Source, actual: LiveTable, profile: bool) -> DiscoveryResult:
        observed = {col.name: {"type": col.type, "nullable": col.nullable} for col in actual.columns}
        defined = {col.name: col for col in source.column_contracts}
        errors = []
        for name, contract in defined.items():
            found = observed.get(name)
            if found is None:
                errors.append(f"Missing configured column: {name}")
                continue
            if found["type"] != contract.type:
                errors.append(f"Column {name}: expected type {contract.type}, observed {found['type']}")
            if not contract.nullable and found["nullable"]:
                errors.append(f"Column {name}: expected NOT NULL, reflected nullable")
        if profile and actual.null_counts is not None:
            for name, contract in defined.items():
                if not contract.nullable and actual.null_counts.get(name, 0):
                    errors.append(f"Column {name}: {actual.null_counts[name]} null records")
            if actual.duplicate_key_groups:
                errors.append(f"Source key {list(source.key)}: "
                              f"{actual.duplicate_key_groups} duplicate key groups")
        return DiscoveryResult(source.id, source.table, source.connection, tuple(errors), observed,
                               tuple(sorted(set(observed) - set(defined))), actual.row_count,
                               actual.null_counts, actual.duplicate_key_groups)
