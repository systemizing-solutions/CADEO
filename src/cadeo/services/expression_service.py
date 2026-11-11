"""Validate a small, explicit expression/predicate DSL; no raw SQL passthrough."""

import math
import re

from cadeo.models.specification import SpecificationValidationError


class ExpressionService:
    @classmethod
    def validate_value(cls, expression: dict, available: dict[str, tuple[str, ...]],
                       context: str) -> None:
        if not isinstance(expression, dict) or len(expression) != 1:
            raise SpecificationValidationError(f"{context}: expected one structured expression")
        operation, value = next(iter(expression.items()))
        if operation == "column":
            cls._validate_column(value, available, context)
        elif operation == "literal":
            if value is not None and type(value) not in (str, bool, int, float):
                raise SpecificationValidationError(f"{context}: literal must be a scalar")
            if type(value) is float and not math.isfinite(value):
                raise SpecificationValidationError(f"{context}: literal must be finite")
        elif operation == "call":
            if not isinstance(value, dict) or set(value) != {"name", "args"}:
                raise SpecificationValidationError(f"{context}.call: expected name and args")
            name = value["name"]
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name):
                raise SpecificationValidationError(f"{context}.call.name: invalid SQL function name")
            if not isinstance(value["args"], list) or len(value["args"]) > 32:
                raise SpecificationValidationError(f"{context}.call.args: expected at most 32 expressions")
            for index, item in enumerate(value["args"]):
                cls.validate_value(item, available, f"{context}.call.args[{index}]")
        elif operation == "registry":
            from cadeo.services.function_registry_service import FunctionRegistryService
            FunctionRegistryService.bind(value, available, context)
        elif operation in ("concat", "coalesce"):
            if not isinstance(value, list) or len(value) < 2:
                raise SpecificationValidationError(f"{context}.{operation}: expected at least two expressions")
            for index, item in enumerate(value):
                cls.validate_value(item, available, f"{context}.{operation}[{index}]")
        elif operation == "case":
            if not isinstance(value, dict) or set(value) != {"when", "else"} or not isinstance(value["when"], list) or not value["when"]:
                raise SpecificationValidationError(f"{context}.case: expected nonempty when and else")
            for index, item in enumerate(value["when"]):
                if not isinstance(item, dict) or set(item) != {"if", "then"}:
                    raise SpecificationValidationError(f"{context}.case.when[{index}]: expected if and then")
                cls.validate_predicate(item["if"], available, f"{context}.case.when[{index}].if")
                cls.validate_value(item["then"], available, f"{context}.case.when[{index}].then")
            cls.validate_value(value["else"], available, f"{context}.case.else")
        else:
            raise SpecificationValidationError(f"{context}: unsupported expression {operation}")

    @classmethod
    def validate_predicate(cls, predicate: dict, available: dict[str, tuple[str, ...]],
                           context: str) -> None:
        if not isinstance(predicate, dict) or len(predicate) != 1:
            raise SpecificationValidationError(f"{context}: expected one structured predicate")
        operation, value = next(iter(predicate.items()))
        if operation in ("eq", "ne", "gt", "gte", "lt", "lte"):
            if not isinstance(value, list) or len(value) != 2 or not all(isinstance(item, dict) for item in value):
                raise SpecificationValidationError(f"{context}.{operation}: expected two expressions")
            if any('literal' in item and item['literal'] is None for item in value):
                raise SpecificationValidationError(f"{context}.{operation}: compare NULL with is_null or is_not_null")
            for index, item in enumerate(value):
                cls.validate_value(item, available, f"{context}.{operation}[{index}]")
        elif operation in ("and", "or"):
            if not isinstance(value, list) or len(value) < 2:
                raise SpecificationValidationError(f"{context}.{operation}: expected at least two predicates")
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
        if not isinstance(column, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", column):
            raise SpecificationValidationError(f"{context}: must use alias.column: {column}")
        alias, field = column.split(".", 1)
        if alias not in available:
            raise SpecificationValidationError(f"{context}: unknown source alias: {alias}")
        if field not in available[alias]:
            raise SpecificationValidationError(f"{context}: undeclared source column: {column}")
