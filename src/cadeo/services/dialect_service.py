"""Select an installed SQLGlot output dialect; there is one compilation path."""

from cadeo.models.specification import SpecificationValidationError
from cadeo.services.sqlglot_service import SqlGlotSqlService
from cadeo.services.sqlfluff_service import SqlFluffService, SQLFLUFF_NAMES


class DialectService:
    """A config selector, not a renderer factory with special-case dialects."""

    @staticmethod
    def _sqlglot():
        try:
            from sqlglot.dialects import Dialect, Dialects
        except ImportError as error:
            raise SpecificationValidationError(
                "SQLGlot must be installed to compile SQL; install the project dependencies."
            ) from error
        return Dialect, Dialects

    @classmethod
    def validate(cls, dialect: str) -> None:
        Dialect, _ = cls._sqlglot()
        try:
            Dialect.get_or_raise(dialect)
        except (ValueError, KeyError) as error:
            raise SpecificationValidationError(
                f"Unknown SQLGlot dialect '{dialect}'. Run 'cadeo dialects'."
            ) from error
        SqlFluffService().require_dialect(dialect)

    @classmethod
    def available(cls) -> list[str]:
        _, Dialects = cls._sqlglot()
        supported = SqlFluffService.supported_names()
        return sorted(item.value for item in Dialects if item.value
                      and SQLFLUFF_NAMES.get(item.value, item.value) in supported)

    @classmethod
    def create(cls, dialect: str, build_id: str | None = None) -> SqlGlotSqlService:
        cls.validate(dialect)
        return SqlGlotSqlService(dialect, build_id=build_id)
