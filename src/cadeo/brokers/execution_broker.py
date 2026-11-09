"""One SQLAlchemy execution broker; SQL is supplied by SQLGlot, never authored here.

Transactional publication is deliberately limited to engines where the compiler
has an explicit safety strategy. A SQLAlchemy driver alone does not guarantee
that a warehouse supports rollback of view changes.
"""

from contextlib import contextmanager
from pathlib import Path
from secrets import token_hex
from typing import Iterator

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Connection, make_url

from cadeo.brokers.database_broker import _type_family
from cadeo.models.specification import Source, SpecificationValidationError
from cadeo.services.engine_catalog_service import EngineCatalogService


class ExecutionBroker:
    @contextmanager
    def transaction(self, url: str) -> Iterator[Connection]:
        try:
            parsed = make_url(url)
        except Exception as exc:
            raise SpecificationValidationError("Invalid SQLAlchemy execution URL") from exc
        backend = parsed.get_backend_name()
        EngineCatalogService.require_safe_backend(backend)
        if backend == "duckdb":
            EngineCatalogService.require_duckdb_driver()
        if backend == "sqlite" and parsed.database not in (None, "", ":memory:"):
            if not Path(parsed.database).is_file():
                raise SpecificationValidationError("Execution SQLite database does not exist")
        engine = None
        try:
            try:
                engine = create_engine(url)
            except Exception as exc:
                # Do not echo credential-bearing DBAPI exception messages.
                raise SpecificationValidationError(
                    f"SQLAlchemy driver for backend {backend!r} could not be loaded "
                    f"({type(exc).__name__}). Install the matching optional driver; "
                    "run cadeo engines to inspect installed drivers."
                ) from exc
            if backend == "sqlite":
                # sqlite3's legacy transaction control does not begin for DDL.
                # Explicit BEGIN is essential for atomic DROP VIEW / CREATE VIEW.
                @event.listens_for(engine, "connect")
                def _sqlite_connect(dbapi_connection, connection_record):
                    dbapi_connection.isolation_level = None

                @event.listens_for(engine, "begin")
                def _sqlite_begin(connection):
                    connection.exec_driver_sql("BEGIN")
            if backend == "duckdb":
                self._duckdb_publication_probe(engine)
            with engine.begin() as connection:
                yield connection
        finally:
            if engine is not None:
                engine.dispose()

    @staticmethod
    def _duckdb_publication_probe(engine) -> None:
        """Verify CREATE/REPLACE/ROLLBACK using the exact installed SQLAlchemy driver.

        A committed canary view emulates an existing published product. Replacing
        it in a failed transaction must restore its previous definition. All DDL
        uses SQLAlchemy's connection and transactions, not a DuckDB DBAPI path.
        """
        relation = "__cadeo_ddl_probe_" + token_hex(10)
        quote = engine.dialect.identifier_preparer.quote(relation)
        try:
            with engine.connect() as connection:
                created = False
                try:
                    with connection.begin():
                        connection.exec_driver_sql(f"CREATE VIEW {quote} AS SELECT 1 AS value")
                    created = True
                    transaction = connection.begin()
                    try:
                        connection.exec_driver_sql(f"CREATE OR REPLACE VIEW {quote} AS SELECT 2 AS value")
                        if connection.exec_driver_sql(f"SELECT value FROM {quote}").scalar_one() != 2:
                            raise SpecificationValidationError("DuckDB publication probe did not replace the view")
                    finally:
                        transaction.rollback()
                    with connection.begin():
                        if connection.exec_driver_sql(f"SELECT value FROM {quote}").scalar_one() != 1:
                            raise SpecificationValidationError(
                                "DuckDB publication disabled: replacing a view was not rolled back")
                finally:
                    if created:
                        # Ensure the disposable canary does not persist between runs.
                        with connection.begin():
                            connection.exec_driver_sql(f"DROP VIEW IF EXISTS {quote}")
        except SpecificationValidationError:
            raise
        except Exception as exc:
            # Errors may contain connection details: never surface DBAPI messages.
            raise SpecificationValidationError(
                f"DuckDB publication safety probe failed ({type(exc).__name__}); "
                "build refused before publication") from exc

    @staticmethod
    def source_ready(connection: Connection, source: Source) -> dict:
        """Check a physical/source view exists and contains its declared columns.

        Upstream product refs are already type-checked against the canonical IR.
        Views often lose physical NOT NULL/type metadata, so only check their
        presence and field names here.
        """
        parts = source.table.rsplit(".", 1)
        schema, name = (parts[0], parts[1]) if len(parts) == 2 else (None, parts[0])
        inspector = inspect(connection)
        if not inspector.has_table(name, schema=schema):
            raise SpecificationValidationError(
                f"Source {source.id}: missing table or view {source.table}")
        reflected = {col["name"]: col for col in inspector.get_columns(name, schema=schema)}
        required = {attr.name: attr for attr in source.column_contracts}
        missing = sorted(set(required) - set(reflected))
        if missing:
            raise SpecificationValidationError(
                f"Source {source.id}: {source.table} missing columns {missing}")
        if source.product_ref is None:
            for col_name, attribute in required.items():
                actual = reflected[col_name]
                family = _type_family(actual["type"])
                if family != "unknown" and family != attribute.type:
                    raise SpecificationValidationError(
                        f"Source {source.id}.{col_name}: expected {attribute.type}, found {family}")
                if not attribute.nullable and actual.get("nullable", True):
                    raise SpecificationValidationError(
                        f"Source {source.id}.{col_name}: database column is nullable")
        return {"table": source.table, "columns_checked": len(required),
                "product_ref": source.product_ref}

    @staticmethod
    def prepare_publication(connection: Connection, target: str) -> None:
        """SQLite only: transactional view swap. PostgreSQL uses OR REPLACE SQLGlot SQL."""
        if connection.dialect.name != "sqlite":
            return
        parts = target.rsplit(".", 1)
        schema, name = (parts[0], parts[1]) if len(parts) == 2 else (None, parts[0])
        inspector = inspect(connection)
        if name in inspector.get_table_names(schema=schema):
            raise SpecificationValidationError(
                f"Refusing to replace table {target}; the published target must be a view")
        if name in inspector.get_view_names(schema=schema):
            quote = connection.dialect.identifier_preparer.quote
            relation = ".".join(quote(part) for part in target.split("."))
            connection.exec_driver_sql(f"DROP VIEW {relation}")

    @staticmethod
    def verify_publication(connection: Connection, target: str) -> None:
        """Resolve the new view before committing; SQLite defers some view errors."""
        quote = connection.dialect.identifier_preparer.quote
        relation = ".".join(quote(part) for part in target.split("."))
        connection.exec_driver_sql(f"SELECT * FROM {relation} WHERE 1 = 0").close()

    def check_failures(self, connection: Connection, sql: str, dialect: str,
                       check_ids: tuple[str, ...]) -> Iterator[tuple[str, int]]:
        """Yield validated check results as each query finishes; stop at first failure.

        The caller records each yielded result immediately. Subsequent SQL is never
        executed after a nonzero failure, including when the iterator is exhausted.
        The statement count is checked before executing any database query.
        """
        statements = self.statements(sql, dialect)
        if len(statements) != len(check_ids):
            raise SpecificationValidationError(
                f"Expected {len(check_ids)} quality checks but received {len(statements)} SQL statements")
        for check_id, statement in zip(check_ids, statements):
            result = connection.exec_driver_sql(statement)
            try:
                rows = result.fetchmany(2)
            finally:
                result.close()
            if len(rows) != 1 or len(rows[0]) != 1:
                raise SpecificationValidationError(
                    f"Quality check {check_id} must return exactly one scalar")
            value = rows[0][0]
            if type(value) is not int or value < 0:
                raise SpecificationValidationError(
                    f"Quality check {check_id} must return a nonnegative integer")
            yield check_id, value
            if value:
                return

    @staticmethod
    def statements(sql: str, dialect: str) -> tuple[str, ...]:
        """Use SQLGlot (the same SQL AST engine) to split generated SQL safely."""
        from sqlglot import parse
        expressions = parse(sql, read=dialect)
        return tuple(expression.sql(dialect=dialect) for expression in expressions if expression)
