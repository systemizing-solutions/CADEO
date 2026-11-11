"""Versioned engineering vocabulary, independent of SQL and exporter names."""

import copy
import json
import math
import re
from typing import Any, NoReturn

from cadeo.models.specification import SpecificationValidationError


class FunctionRegistryService:
    VERSION = "0.9.0"
    UPSTREAM = "systemizing-solutions/dbt_data_engineering_toolkit@45b97aa459f542ca72db9a266f50181ae1f226ac"
    _FUNCTIONS: dict[str, dict[str, Any]] = {}
    _EXPRESSION_PARAMETERS = {
        "expression", "left", "right", "numerator", "denominator", "total",
        "required_expression", "when_expression", "start_date", "end_date",
        "q1_expression", "q3_expression", "median_expression", "mad_expression",
        "salt_expression", "loaded_at_expression",
    }
    _EXPRESSION_LISTS = {"fields", "expressions", "components", "partition_by"}
    _STAGES = {
        "deduplicate": "mapping.entities.<model>.deduplicate",
        "union_relations": "mapping.entities.<model>.unions",
        "drop_missing_rows": "mapping.entities.<model>.filters",
        "assertions": "assurance.entities.<model>",
        "assertions_filter": "assurance.entities.<model>",
        "keep_valid_rows": "assurance.entities.<model>",
        "keep_quarantined_rows": "assurance.entities.<model>",
    }

    def __init__(self, exporters: dict | None = None) -> None:
        self.exporters = copy.deepcopy(exporters or {})
        for exporter, mappings in self.exporters.items():
            if not isinstance(exporter, str) or not isinstance(mappings, dict):
                self._error("exporters must map exporter names to function mappings")
            for name, mapping in mappings.items():
                self.definition(name)
                if not isinstance(mapping, dict) or set(mapping) != {"macro"}:
                    self._error(f"{exporter}.{name}: expected a macro mapping")
                if not isinstance(mapping["macro"], str) or not re.fullmatch(
                        r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", mapping["macro"]):
                    self._error(f"{exporter}.{name}: invalid macro name")

    @staticmethod
    def _error(message: str) -> NoReturn:
        raise SpecificationValidationError(message)

    @classmethod
    def definition(cls, name: str) -> dict:
        if not isinstance(name, str) or name not in cls._FUNCTIONS:
            cls._error(f"unknown registry function: {name!r}")
        return copy.deepcopy(cls._FUNCTIONS[name])

    @classmethod
    def catalog(cls) -> dict:
        return {"version": cls.VERSION, "upstream": cls.UPSTREAM,
                "functions": copy.deepcopy(cls._FUNCTIONS)}

    def exporter_mapping(self, name: str, exporter: str) -> dict:
        self.definition(name)
        override = self.exporters.get(exporter, {}).get(name)
        if override is not None:
            return copy.deepcopy(override)
        if exporter == "dbt":
            return {"macro": f"de_toolkit.{name}"}
        self._error(f"{name}: no mapping for exporter {exporter!r}")

    @classmethod
    def bind(cls, call: dict, available: dict | None = None, context: str = "registry") -> dict:
        from cadeo.services.expression_service import ExpressionService

        if not isinstance(call, dict) or set(call) != {"name", "args"}:
            cls._error(f"{context}: expected registry name and args")
        definition = cls.definition(call["name"])
        if definition["kind"] != "scalar":
            stage = definition.get("stage", "a relation/exporter operation, not a field expression")
            cls._error(f"{context}: {call['name']} is not scalar; use {stage}")
        supplied = call["args"]
        if not isinstance(supplied, dict):
            cls._error(f"{context}: args must be named parameters")
        if not all(isinstance(parameter, str) for parameter in supplied):
            cls._error(f"{context}: parameter names must be strings")
        unknown = set(supplied) - set(definition["parameters"])
        missing = set(definition["required"]) - set(supplied)
        if unknown or missing:
            cls._error(f"{context}.{call['name']}: unknown parameters {sorted(unknown)}; missing parameters {sorted(missing)}")
        arguments = copy.deepcopy(definition["defaults"])
        arguments.update(supplied)
        if isinstance(arguments.get("mapping"), str):
            try:
                arguments["mapping"] = json.loads(arguments["mapping"])
            except ValueError as error:
                cls._error(f"{context}.{call['name']}.mapping: invalid JSON object: {error}")
        for parameter, item in arguments.items():
            path = f"{context}.{call['name']}.{parameter}"
            if parameter in definition["expression_parameters"]:
                if item is None and parameter not in definition["required"]:
                    continue
                if not isinstance(item, dict) or len(item) != 1:
                    cls._error(f"{path}: expected a structured expression")
                if available is not None:
                    ExpressionService.validate_value(item, available, path)
            elif parameter in definition["expression_lists"]:
                if item is None and parameter not in definition["required"]:
                    continue
                if not isinstance(item, list) or not item:
                    cls._error(f"{path}: expected a nonempty expression list")
                for expression in item:
                    if not isinstance(expression, dict) or len(expression) != 1:
                        cls._error(f"{path}: expected structured expressions")
                    if available is not None:
                        ExpressionService.validate_value(expression, available, path)
            else:
                cls._validate_option(parameter, item, path)
        cls._validate_options(call["name"], arguments, context)
        return arguments

    @classmethod
    def _validate_option(cls, parameter, value, path):
        boolean = {"trim", "collapse_whitespace", "blank_as_null", "keep_plus", "allow_null",
                   "case_sensitive", "preserve_unmapped", "preserve_null", "inclusive", "right",
                   "include_lowest", "grouping"}
        integer = {"precision", "scale", "width", "length", "group", "part", "start", "stop", "decimal_places"}
        numeric = {"lower", "upper", "lower_bound", "upper_bound", "threshold", "offset", "tolerance",
                   "percentile", "zero_range_value", "zero_variance_value"}
        mappings = {"mapping", "correction_map", "corrections", "aliases", "replacements", "translations"}
        lists = {"values", "categories", "edges", "labels", "true_values", "false_values"}
        strings = {"case", "side", "fillchar", "pattern", "prefix", "suffix", "delimiter",
                   "operator", "form", "remove_prefix", "remove_suffix"}
        valid = True
        if parameter in boolean:
            valid = type(value) is bool
        elif parameter in integer:
            valid = (value is None and parameter not in {"precision", "scale", "width", "group", "part"}) or type(value) is int
        elif parameter in numeric:
            valid = value is None or type(value) in (int, float)
        elif parameter in mappings:
            valid = isinstance(value, dict) and all(
                cls._scalar(key) and cls._scalar(item) for key, item in value.items())
            if parameter in {"replacements", "translations"}:
                valid = valid and all(isinstance(key, str) and isinstance(item, str) for key, item in value.items())
        elif parameter in lists:
            valid = value is None or (isinstance(value, list) and all(cls._scalar(item) for item in value))
            if parameter in {"true_values", "false_values"}:
                valid = valid and isinstance(value, list) and bool(value)
        elif parameter in strings:
            valid = (value is None and parameter in {"remove_prefix", "remove_suffix"}) or isinstance(value, str)
        elif parameter == "data_type":
            valid = isinstance(value, (str, dict)) or value is None
        elif parameter in {"format", "formatting"}:
            valid = value is None or isinstance(value, (str, dict))
        else:
            valid = cls._scalar(value)
        if not valid or (type(value) is float and not math.isfinite(value)):
            cls._error(f"{path}: invalid parameter value")
        if parameter == "data_type" and value is not None:
            options = value if isinstance(value, dict) else {"name": value}
            aliases = {"str": "string", "text": "string", "int": "integer", "number": "numeric",
                       "decimal": "numeric", "datetime": "timestamp", "bool": "boolean"}
            target = options.get("name")
            if not isinstance(target, str) or aliases.get(target.lower(), target.lower()) not in {
                    "string", "integer", "numeric", "date", "timestamp", "boolean"}:
                cls._error(f"{path}: unsupported data type")
            if set(options) - {"name", "precision", "scale", "true_values", "false_values"}:
                cls._error(f"{path}: unknown conversion options")
            for key, item in options.items():
                if key != "name":
                    cls._validate_option(key, item, f"{path}.{key}")
            cls._validate_options("conversion", options, path)
        if parameter in {"format", "formatting"} and isinstance(value, dict):
            allowed = {"pattern", "decimal_places", "grouping", "prefix", "suffix", "null_value",
                       "case", "trim", "collapse_whitespace", "blank_as_null"}
            if set(value) - allowed:
                cls._error(f"{path}: unknown formatting options")
            for key, item in value.items():
                cls._validate_option(key, item, f"{path}.{key}")
            cls._validate_options("formatting", value, path)

    @staticmethod
    def _scalar(value):
        return value is None or type(value) in (str, bool, int) or (type(value) is float and math.isfinite(value))

    @classmethod
    def _validate_options(cls, name, args, context):
        if "mapping" in args and not args["mapping"]:
            cls._error(f"{context}.{name}.mapping: mapping cannot be empty")
        if (args.get("format") is not None or args.get("formatting") is not None) and name in {
                "mapping", "key_value_map", "map_value"} and args.get("data_type") is None:
            cls._error(f"{context}.{name}: formatting requires data_type")
        for parameter, choices in {"case": {"preserve", "lower", "upper", "title", "capitalize"},
                                   "side": {"left", "right", "both"},
                                   "operator": {"=", "!=", "<>", ">", ">=", "<", "<="},
                                   "form": {"NFC", "NFD", "NFKC", "NFKD"}}.items():
            if parameter in args and args[parameter] not in choices:
                cls._error(f"{context}.{name}.{parameter}: expected one of {sorted(choices)}")
        for parameter in ("width", "length", "group", "start", "stop", "scale", "decimal_places"):
            if args.get(parameter) is not None and args[parameter] < 0:
                cls._error(f"{context}.{name}.{parameter}: must be non-negative")
        if "fillchar" in args and len(args["fillchar"]) != 1:
            cls._error(f"{context}.{name}.fillchar: must be one character")
        if "part" in args and args["part"] < 1:
            cls._error(f"{context}.{name}.part: must be positive")
        if "precision" in args and not (1 <= args["precision"] <= 38 and 0 <= args.get("scale", 0) <= args["precision"]):
            cls._error(f"{context}.{name}: precision must be 1..38 and scale 0..precision")
        if args.get("decimal_places") is not None and args["decimal_places"] > 38:
            cls._error(f"{context}.{name}.decimal_places: must not exceed 38")
        if "percentile" in args and not 0 <= args["percentile"] <= 1:
            cls._error(f"{context}.{name}.percentile: must be between zero and one")
        if "edges" in args:
            edges = args["edges"]
            if len(edges) < 2 or any(type(edge) not in (int, float) for edge in edges) or any(
                    left >= right for left, right in zip(edges, edges[1:])):
                cls._error(f"{context}.{name}.edges: expected strictly increasing numeric edges")
            if args.get("labels") is not None and len(args["labels"]) != len(edges) - 1:
                cls._error(f"{context}.{name}.labels: expected one label per interval")
        if "categories" in args and (not args["categories"] or len({str(item) for item in args["categories"]}) != len(args["categories"])):
            cls._error(f"{context}.{name}.categories: expected a nonempty unique vocabulary")
        for lower, upper in (("lower", "upper"), ("lower_bound", "upper_bound"), ("min_value", "max_value")):
            if args.get(lower) is not None and args.get(upper) is not None and args[lower] > args[upper]:
                cls._error(f"{context}.{name}: {lower} must not exceed {upper}")

    @classmethod
    def render(cls, call: dict, sql) -> str:
        from cadeo.services.registry_sql_service import RegistrySqlService
        return RegistrySqlService(sql).render(call["name"], cls.bind(call))


def _register(names, required="expression", defaults=None, kind="scalar"):
    for name in names.split():
        options = defaults or {}
        FunctionRegistryService._FUNCTIONS[name] = {
            "kind": kind, "required": required.split(), "defaults": options,
            "native": "scalar" if kind == "scalar" and name != "unicode_normalize" else
                      "existing_stage" if name in FunctionRegistryService._STAGES else "exporter_only",
            "parameters": required.split() + list(options),
            "expression_parameters": sorted((set(required.split()) | set(options)) &
                                            FunctionRegistryService._EXPRESSION_PARAMETERS),
            "expression_lists": sorted((set(required.split()) | set(options)) &
                                       FunctionRegistryService._EXPRESSION_LISTS),
            "exporters": {"dbt": {"macro": f"de_toolkit.{name}"}},
        }
        if name in FunctionRegistryService._STAGES:
            FunctionRegistryService._FUNCTIONS[name]["stage"] = FunctionRegistryService._STAGES[name]
        if name == "bin_numeric":
            FunctionRegistryService._FUNCTIONS[name]["expression_parameters"].remove("right")


_register("clean_string", defaults={"trim": True, "collapse_whitespace": True, "case": "preserve", "blank_as_null": True})
_register("clean_email clean_integer clean_date clean_timestamp normalize_whitespace null_if_blank "
          "string_capitalize string_casefold string_left_strip string_lower string_remove_all_whitespace "
          "string_remove_punctuation string_remove_whitespace string_right_strip string_strip string_title string_upper")
_register("clean_numeric", defaults={"precision": 38, "scale": 6})
_register("clean_boolean", defaults={"true_values": ["true", "t", "yes", "y", "1"], "false_values": ["false", "f", "no", "n", "0"]})
_register("clean_phone", defaults={"keep_plus": True})
_register("clean_code", defaults={"remove_prefix": None, "remove_suffix": None, "case": "upper", "corrections": {}, "blank_as_null": True})
_register("fill_missing", "expression value")
_register("correct_errors", "expression correction_map")
_register("mapping", "expression mapping", {"default": None, "preserve_unmapped": False, "data_type": None, "format": None, "case_sensitive": True})
_register("key_value_map map_value", "expression mapping", {"default": None, "preserve_unmapped": False, "data_type": None, "formatting": None, "case_sensitive": True})
_register("map_date map_timestamp map_integer", "expression mapping", {"default": None, "preserve_unmapped": False, "case_sensitive": True})
_register("map_numeric", "expression mapping", {"default": None, "preserve_unmapped": False, "case_sensitive": True, "precision": 38, "scale": 6})
_register("map_boolean", "expression mapping", {"default": None, "preserve_unmapped": False, "case_sensitive": True, "true_values": ["true", "t", "yes", "y", "1"], "false_values": ["false", "f", "no", "n", "0"]})
_register("map_string", "expression mapping", {"default": None, "preserve_unmapped": False, "case_sensitive": True, "case": "preserve", "trim": False, "collapse_whitespace": False, "blank_as_null": False})
_register("try_cast convert_value", "expression data_type")
_register("format_value", "expression data_type", {"formatting": None})
_register("format_string", defaults={"case": "preserve", "trim": False, "collapse_whitespace": False, "blank_as_null": False, "null_value": None})
_register("format_date", defaults={"pattern": "%Y-%m-%d", "null_value": None})
_register("format_timestamp", defaults={"pattern": "%Y-%m-%d %H:%M:%S", "null_value": None})
_register("format_temporal", "expression data_type", {"pattern": "%Y-%m-%d"})
_register("format_number", defaults={"decimal_places": None, "grouping": False, "prefix": "", "suffix": "", "null_value": None})
_register("standardize_code", defaults={"aliases": {}, "length": None})
_register("standardize_country standardize_currency", defaults={"aliases": {}})
_register("regex_match", "expression pattern")
_register("regex_replace_all", "expression pattern", {"replacement": ""})
_register("string_regex_extract string_regex_extract_all", "expression pattern", {"group": 1})
_register("string_regex_replace_many string_replace_many", "expression replacements")
_register("string_translate_chars", "expression translations")
_register("string_remove_prefix", "expression prefix")
_register("string_remove_suffix", "expression suffix")
_register("string_center string_ljust string_rjust", "expression width", {"fillchar": " "})
_register("string_pad", "expression width", {"side": "both", "fillchar": " "})
_register("string_zfill", "expression width")
_register("string_slice", defaults={"start": None, "stop": None})
_register("string_slice_replace", defaults={"start": None, "stop": None, "replacement": ""})
_register("string_split_part", defaults={"delimiter": ",", "part": 1})
_register("unicode_normalize", defaults={"form": "NFC"})
_register("is_email is_positive", defaults={"allow_null": False})
_register("is_between", defaults={"min_value": None, "max_value": None, "inclusive": True, "allow_null": False})
_register("is_in_list", "expression values", {"allow_null": False, "case_sensitive": True})
_register("within_bounds", "expression lower upper", {"allow_null": False})
_register("clip", "expression lower upper")
_register("winsorize", "expression lower_bound upper_bound")
_register("replace_outside_bounds", "expression lower upper replacement")
_register("log_transform", defaults={"offset": 1})
_register("safe_add safe_subtract surrogate_key", "fields")
_register("safe_divide", "numerator denominator")
_register("label_encode", "expression categories", {"unknown": None})
_register("bin_numeric", "expression edges", {"labels": None, "right": True, "include_lowest": False})
_register("min_max_scale", defaults={"partition_by": None, "zero_range_value": 0})
_register("standard_score", defaults={"partition_by": None, "zero_variance_value": None})
_register("percentile", "expression percentile", {"partition_by": None})
_register("is_zscore_anomaly", defaults={"threshold": 3, "partition_by": None})
_register("is_iqr_anomaly", "expression q1_expression q3_expression", {"threshold": 1.5})
_register("is_modified_zscore_anomaly", "expression median_expression mad_expression", {"threshold": 3.5})
_register("rule_compare", "left operator right", {"allow_null": False})
_register("rule_date_order", "start_date end_date", {"allow_null": False})
_register("rule_required_when", "required_expression when_expression")
_register("rule_one_of", "expressions")
_register("rule_sum_equals", "total components", {"tolerance": 0, "allow_null": False})
_register("mask_hash", defaults={"salt_expression": None})
_register("mask_redact", defaults={"replacement": "[REDACTED]", "preserve_null": True})
_register("deduplicate", "relation partition_by order_by", kind="relation")
_register("union_relations", "relations", {"column_override": None, "include": None, "exclude": None, "source_column_name": "_dbt_source_relation", "where": None}, "relation")
_register("drop_missing_rows", "relation columns", kind="relation")
_register("distinct_rows", "relation", kind="relation")
_register("trim_rows", "relation order_by", {"start_row": 0, "end_row": None}, "relation")
_register("undersample_classes", "relation target_column order_by", kind="relation")
_register("date_spine", "datepart start_date end_date", kind="relation")
_register("assertions keep_valid_rows keep_quarantined_rows", "", {"column": None}, "assurance")
_register("assertions_filter", "", {"column": None, "exclude_list": None, "include_list": None, "reverse": False}, "assurance")
_register("audit_columns", "", {"source_system": None, "loaded_at_expression": None, "prefix": "_det_"}, "columns")
_register("one_hot", "expression categories", {"prefix": "category"}, "columns")
_register("multi_hot", "expression categories", {"delimiter": "|", "prefix": "token"}, "columns")