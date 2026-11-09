"""Validate a small, explicit expression/predicate DSL; no raw SQL passthrough."""

import math
import re

from cadeo.models.specification import SpecificationValidationError


class ExpressionService:
    @classmethod
    def validate_value(cls, expression: dict, available: dict[str, tuple[str, ...]],
                       context: str) -> None:
        operation, value = next(iter(expression.items()))
        if operation == "column":
            cls._validate_column(value, available, context)
        elif operation == "literal":
            if type(value) is float and not math.isfinite(value):
                raise SpecificationValidationError(f"{context}: literal must be finite")
        elif operation == "call":
            name = value["name"]
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name):
                raise SpecificationValidationError(f"{context}.call.name: invalid SQL function name")
            for index, item in enumerate(value["args"]):
                cls.validate_value(item, available, f"{context}.call.args[{index}]")
        elif operation in ("concat", "coalesce"):
            for index, item in enumerate(value):
                cls.validate_value(item, available, f"{context}.{operation}[{index}]")
        elif operation == "case":
            for index, item in enumerate(value["when"]):
                cls.validate_predicate(item["if"], available, f"{context}.case.when[{index}].if")
                cls.validate_value(item["then"], available, f"{context}.case.when[{index}].then")
            cls.validate_value(value["else"], available, f"{context}.case.else")
        else:
            raise SpecificationValidationError(f"{context}: unsupported expression {operation}")

    @classmethod
    def validate_predicate(cls, predicate: dict, available: dict[str, tuple[str, ...]],
                           context: str) -> None:
        operation, value = next(iter(predicate.items()))
        if operation in ("eq", "ne", "gt", "gte", "lt", "lte"):
            if any('literal' in item and item['literal'] is None for item in value):
                raise SpecificationValidationError(f"{context}.{operation}: compare NULL with is_null or is_not_null")
            for index, item in enumerate(value):
                cls.validate_value(item, available, f"{context}.{operation}[{index}]")
        elif operation in ("and", "or"):
            for index, item in enumerate(value):
                cls.validate_predicate(item, available, f"{context}.{operation}[{index}]")
        elif operation == "not":
            cls.validate_predicate(value, available, f"{context}.not")
        elif operation in ("is_null", "is_not_null"):
            cls.validate_value(value, available, f"{context}.{operation}")
        else:
            raise SpecificationValidationError(f"{context}: unsupported predicate {operation}")

    @staticmethod
    def _validate_column(column: str, available: dict[str, tuple[str, ...]],
                         context: str) -> None:
        if "." not in column:
            raise SpecificationValidationError(f"{context}: must use alias.column: {column}")
        alias, field = column.split(".", 1)
        if alias not in available:
            raise SpecificationValidationError(f"{context}: unknown source alias: {alias}")
        if field not in available[alias]:
            raise SpecificationValidationError(f"{context}: undeclared source column: {column}")
