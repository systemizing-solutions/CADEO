"""SQLFluff grammar gate. Parsing is not database execution or schema validation."""

from cade.models.specification import SpecificationValidationError


# SQLGlot and SQLFluff have different *names* for Spark; their grammars are
# independent. Resolve names only; never fall back to a different dialect.
SQLFLUFF_NAMES = {"spark": "sparksql"}


class SqlFluffService:
    """Validate generated SQL with SQLFluff's parser for the actual target dialect."""

    @staticmethod
    def supported_names() -> set[str]:
        try:
            import sqlfluff
        except ImportError as error:
            raise SpecificationValidationError(
                "SQLFluff must be installed to compile SQL; install the project dependencies."
            ) from error
        return {item.label for item in sqlfluff.list_dialects()}

    def require_dialect(self, dialect: str) -> str:
        label = SQLFLUFF_NAMES.get(dialect, dialect)
        if label not in self.supported_names():
            raise SpecificationValidationError(
                f"SQLFluff has no grammar for '{dialect}'. "
                "Choose a dialect supported by both SQLGlot and SQLFluff; "
                "the compiler will not silently fall back to another grammar."
            )
        return label

    def validate(self, sql: str, *, dialect: str, artifact: str) -> None:
        try:
            import sqlfluff
            from sqlfluff.api.simple import APIParsingError
        except ImportError as error:
            raise SpecificationValidationError(
                "SQLFluff must be installed to compile SQL; install the project dependencies."
            ) from error

        label = self.require_dialect(dialect)
        try:
            sqlfluff.parse(sql, dialect=label)
        except APIParsingError as error:
            raise SpecificationValidationError(
                f"SQLFluff rejected {artifact} for '{dialect}': {error}"
            ) from error
