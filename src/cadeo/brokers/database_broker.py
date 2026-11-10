"""One read-only SQLAlchemy inspection path for live database metadata and profiling.

SQLAlchemy dialect drivers are selected by the connection URL. This broker does
not execute DDL or DML, print credentials, or return source row values.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy import case, column, create_engine, func, inspect, select, table
from sqlalchemy.engine import make_url
from sqlalchemy.sql import sqltypes


class DatabaseInspectionError(Exception):
    """A live database cannot be reached or its metadata cannot be inspected."""


@dataclass(frozen=True)
class LiveColumn:
    name: str
    type: str
    nullable: bool


@dataclass(frozen=True)
class LiveTable:
    columns: tuple[LiveColumn, ...]
    row_count: int | None = None
    null_counts: dict[str, int] | None = None
    duplicate_key_groups: int | None = None
    primary_key_columns: tuple[str, ...] = ()


class DatabaseBrokerContract(Protocol):
    def inspect_table(self, url: str, qualified_table: str, expected_columns: tuple[str, ...],
                      key: tuple[str, ...], profile: bool) -> LiveTable: ...


def _type_family(value: sqltypes.TypeEngine) -> str:
    """Map reflected SQLAlchemy types to the six canonical contract families."""
    if isinstance(value, sqltypes.Boolean):
        return "boolean"
    if isinstance(value, (sqltypes.Integer, sqltypes.BigInteger, sqltypes.SmallInteger)):
        return "integer"
    if isinstance(value, (sqltypes.Float, sqltypes.Numeric)):
        return "float"
    if isinstance(value, sqltypes.DateTime):
        return "timestamp"
    if isinstance(value, sqltypes.Date):
        return "date"
    if isinstance(value, (sqltypes.String, sqltypes.Text, sqltypes.Unicode)):
        return "string"
    return "unknown"


class DatabaseBroker:
    """Connect, inspect and optionally profile with backend-safe SQLAlchemy expressions."""

    @staticmethod
    def _table_name(qualified_table: str) -> tuple[str | None, str]:
        # The SQLAlchemy dialect owns the interpretation of the schema prefix.
        # A dot in an SQL identifier is not accepted by the specification schema.
        pieces = qualified_table.rsplit(".", 1)
        return (pieces[0], pieces[1]) if len(pieces) == 2 else (None, pieces[0])

    @staticmethod
    def _guard_sqlite_database(url: str) -> None:
        """Do not silently create a missing SQLite file during discovery."""
        try:
            parsed = make_url(url)
        except Exception as error:
            raise DatabaseInspectionError("Invalid SQLAlchemy connection URL") from error
        if parsed.get_backend_name() != "sqlite":
            return
        database = parsed.database
        if not database or database == ":memory:":
            return
        if database.startswith("file:"):
            # SQLAlchemy URI mode is handled by the SQLite driver. Caller should
            # configure mode=ro for strict OS-level read-only access.
            return
        if not Path(database).is_file():
            raise DatabaseInspectionError("SQLite database file does not exist")

    def inspect_table(self, url: str, qualified_table: str, expected_columns: tuple[str, ...],
                      key: tuple[str, ...], profile: bool) -> LiveTable:
        self._guard_sqlite_database(url)
        engine = None
        try:
            engine = create_engine(url, pool_pre_ping=True)
            schema, name = self._table_name(qualified_table)
            with engine.connect() as connection:
                inspector = inspect(connection)
                if not inspector.has_table(name, schema=schema):
                    raise DatabaseInspectionError(f"Table or view not found: {qualified_table}")
                reflected = inspector.get_columns(name, schema=schema)
                columns = tuple(LiveColumn(col["name"], _type_family(col["type"]),
                                           bool(col.get("nullable", True))) for col in reflected)
                primary_key = tuple(inspector.get_pk_constraint(name, schema=schema)
                                    .get("constrained_columns") or ())
                columns = tuple(LiveColumn(col.name, col.type,
                                           False if col.name in primary_key else col.nullable)
                                for col in columns)
                available = {col.name for col in columns}
                if not profile or not set(expected_columns).issubset(available):
                    return LiveTable(columns, primary_key_columns=primary_key)

                # No interpolation of table/column identifiers in SQL text.
                source_table = table(name, *(column(c) for c in expected_columns), schema=schema)
                source_columns = source_table.c
                null_expressions = [
                    func.sum(case((source_columns[c].is_(None), 1), else_=0)).label(c)
                    for c in expected_columns
                ]
                counts = connection.execute(
                    select(func.count().label("rows"), *null_expressions).select_from(source_table)
                ).one()
                null_counts = {key_name: int(counts[index + 1] or 0)
                               for index, key_name in enumerate(expected_columns)}
                duplicated = (
                    select(*(source_columns[c] for c in key))
                    .select_from(source_table)
                    .group_by(*(source_columns[c] for c in key))
                    .having(func.count() > 1)
                    .subquery()
                )
                groups = int(connection.execute(select(func.count()).select_from(duplicated)).scalar_one())
                return LiveTable(columns, int(counts[0]), null_counts, groups, primary_key)
        except DatabaseInspectionError:
            raise
        except Exception as error:
            # Database-driver errors can echo hostnames and credentials. Never return
            # arbitrary exception messages to the CLI or JSON reports.
            raise DatabaseInspectionError(
                f"Cannot inspect {qualified_table} ({type(error).__name__}); "
                "check connection, driver, permissions and schema"
            ) from error
        finally:
            if engine is not None:
                engine.dispose()
