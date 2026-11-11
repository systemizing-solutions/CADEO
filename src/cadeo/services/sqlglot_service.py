"""The sole SQL output service: SQLGlot renders every artifact for every dialect."""

from functools import lru_cache

from cadeo.models.specification import Specification, SpecificationValidationError
from cadeo.services.sql_service import SqlTemplateService
from cadeo.services.sqlfluff_service import SqlFluffService


class SqlGlotSqlService:
    """Compile an IR-derived SQL template into target SQL and validate its grammar."""

    def __init__(self, dialect: str, build_id: str | None = None) -> None:
        self.dialect = dialect
        self.build_id = build_id
        self.templates = SqlTemplateService(build_id=build_id)
        self.syntax = SqlFluffService()

    @staticmethod
    @lru_cache(maxsize=64)
    def _supports_safe_cast(dialect: str) -> bool:
        from sqlglot import exp, parse_one
        probe = parse_one("TRY_CAST('invalid' AS INT)", read="spark")
        rendered = probe.sql(dialect=dialect)
        return parse_one(rendered, read=dialect).find(exp.TryCast) is not None

    def _translate(self, script: str, artifact: str) -> str:
        if not script.strip() or not any(
            line.strip() and not line.lstrip().startswith("--") for line in script.splitlines()
        ):
            # External-preservation contract is documentation, not executable SQL.
            return script
        try:
            from sqlglot import ErrorLevel, exp, parse, transpile
            from sqlglot.errors import ParseError, UnsupportedError
        except ImportError as error:
            raise SpecificationValidationError("SQLGlot is required for SQL generation; install package dependencies") from error
        try:
            safe_casts = any(
                node.args.get("safe")
                for statement in parse(script, read="spark") if statement is not None
                for node in statement.find_all(exp.TryCast)
            )
            if safe_casts and not self._supports_safe_cast(self.dialect):
                raise SpecificationValidationError(
                    f"Native safe conversion is unavailable for '{self.dialect}'; refusing to replace TRY_CAST with CAST")
            statements = transpile(
                script,
                read="spark",
                write=self.dialect,
                pretty=True,
                indent=2,
                unsupported_level=ErrorLevel.RAISE,
            )
        except (ParseError, UnsupportedError, ValueError) as error:
            raise SpecificationValidationError(
                f"SQLGlot could not generate {artifact} for '{self.dialect}': {error}"
            ) from error
        if not statements or any(not statement.strip() for statement in statements):
            raise SpecificationValidationError(
                f"SQLGlot generated empty SQL for {artifact} ({self.dialect})."
            )
        result = ";\n\n".join(s.rstrip("; \n") for s in statements) + ";\n"
        self.syntax.validate(result, dialect=self.dialect, artifact=artifact)
        return result

    def generate(self, spec: Specification) -> str:
        return self._translate(self.templates.generate(spec), "canonical view")

    def generate_checks(self, spec: Specification) -> str:
        return self._translate(self.templates.generate_checks(spec), "quality checks")

    def standalone_checks(self, spec: Specification) -> tuple[tuple[str, str], ...]:
        return tuple((identifier, self._translate(statement, f"standalone check {identifier}"))
                     for identifier, statement in self.templates.check_statements(spec, spec.product.target))

    def generate_audit(self, spec: Specification) -> str:
        return self._translate(self.templates.generate_audit(spec), "reconciliation audit")

    def generate_preservation(self, spec: Specification) -> str:
        return self._translate(self.templates.generate_preservation(spec), "raw preservation")

    def generate_publication(self, spec: Specification) -> str:
        return self._translate(self.templates.generate_publication(spec, replace=self.dialect in ("postgres", "duckdb") and self.build_id is not None), "publication view")
