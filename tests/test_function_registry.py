"""Named engineering operations share the structured expression compiler."""

import pytest
import json
from pathlib import Path

from cadeo.models.specification import SpecificationValidationError
from cadeo.services.expression_service import ExpressionService
from cadeo.services.function_registry_service import FunctionRegistryService
from cadeo.services.sql_service import SqlTemplateService


def test_nested_registry_expression():
    expression = {"registry": {"name": "clean_email", "args": {
        "expression": {"registry": {"name": "fill_missing", "args": {
            "expression": {"column": "source.email"}, "value": " UNKNOWN "}}}}}}
    ExpressionService.validate_value(expression, {"source": ("email",)}, "mapping.email")
    assert "LOWER(" in SqlTemplateService().expression(expression)


def test_registry_rejects_unknown_names():
    with pytest.raises(SpecificationValidationError, match="unknown registry function"):
        ExpressionService.validate_value(
            {"registry": {"name": "arbitrary_sql", "args": {}}}, {}, "mapping.email")


def test_exporter_mapping_does_not_change_logical_name():
    registry = FunctionRegistryService()
    assert registry.exporter_mapping("clean_email", "dbt")["macro"] == "de_toolkit.clean_email"
    custom = FunctionRegistryService(exporters={"dbt": {"clean_email": {"macro": "company.email"}}})
    assert custom.exporter_mapping("clean_email", "dbt")["macro"] == "company.email"
    assert registry.exporter_mapping("clean_email", "dbt")["macro"] == "de_toolkit.clean_email"


def registry_value(name, input_value, **options):
    return {"registry": {"name": name, "args": {"expression": {"literal": input_value}, **options}}}


def execute(expression):
    duckdb = pytest.importorskip("duckdb")
    from sqlglot import transpile

    ExpressionService.validate_value(expression, {}, "test")
    intermediate = "SELECT " + SqlTemplateService().expression(expression)
    statement = transpile(intermediate, read="spark", write="duckdb", unsupported_level="RAISE")[0]
    with duckdb.connect() as connection:
        return connection.execute(statement).fetchone()[0]


@pytest.mark.parametrize("name,value,options,expected", [
    ("clean_string", "  Ab\t  Cd  ", {}, "Ab Cd"),
    ("clean_string", "  ", {}, None),
    ("clean_email", "  A@EXAMPLE.COM  ", {}, "a@example.com"),
    ("clean_code", " pre-ab-end ", {"remove_prefix": "pre-", "remove_suffix": "-end"}, "AB"),
    ("clean_integer", "not an int", {}, None),
    ("clean_integer", "42", {}, 42),
    ("clean_boolean", "YES", {}, True),
    ("clean_boolean", "unknown", {}, None),
    ("clean_phone", "+44 (20) 123", {}, "+4420123"),
    ("clean_phone", "   ", {}, None),
    ("clean_phone", "unknown", {}, None),
    ("fill_missing", None, {"value": "fallback"}, "fallback"),
    ("correct_errors", "bad", {"correction_map": {"bad": "good"}}, "good"),
    ("map_integer", "a", {"mapping": {"a": "12"}}, 12),
    ("map_integer", "a", {"mapping": {"a": "bad"}}, None),
    ("map_integer", "bad", {"mapping": {"a": 1}, "preserve_unmapped": True}, None),
    ("mapping", None, {"mapping": {None: "missing"}}, "missing"),
    ("mapping", 1, {"mapping": {1: "one"}}, "one"),
    ("mapping", "a", {"mapping": '{"a": "yes"}'}, "yes"),
    ("convert_value", "  text  ", {"data_type": "text"}, "  text  "),
    ("convert_value", "12", {"data_type": "int"}, 12),
    ("mapping", "A", {"mapping": {"a": "yes"}, "case_sensitive": False}, "yes"),
    ("mapping", "A", {"mapping": {"b": "B"}, "preserve_unmapped": True}, "A"),
    ("standardize_country", " usa ", {"aliases": {"USA": "US"}}, "US"),
    ("standardize_currency", "usd", {}, "USD"),
    ("string_remove_prefix", "a%value", {"prefix": "a%"}, "value"),
    ("string_remove_suffix", "value_end", {"suffix": "_end"}, "value"),
    ("string_slice", "abcdef", {"start": 1, "stop": 4}, "bcd"),
    ("string_slice_replace", "abcdef", {"start": 1, "stop": 4, "replacement": "X"}, "aXef"),
    ("string_zfill", "-12", {"width": 5}, "-0012"),
    ("string_ljust", "abcdef", {"width": 3}, "abcdef"),
    ("string_center", "abc", {"width": 6, "fillchar": "_"}, "_abc__"),
    ("string_split_part", "a,b,c", {"part": 2}, "b"),
    ("string_replace_many", "abc", {"replacements": {"a": "b", "b": "c"}}, "ccc"),
    ("is_email", "invalid", {}, False),
    ("is_email", "a@example.com", {}, True),
    ("is_positive", None, {}, False),
    ("is_positive", None, {"allow_null": True}, True),
    ("is_in_list", "A", {"values": ["a"], "case_sensitive": False}, True),
    ("is_between", 4, {"min_value": 4, "max_value": 5, "inclusive": False}, False),
    ("clip", 12, {"lower": 0, "upper": 10}, 10),
    ("clip", None, {"lower": 0, "upper": 10}, None),
    ("replace_outside_bounds", None, {"lower": 0, "upper": 10, "replacement": 5}, None),
    ("label_encode", "b", {"categories": ["a", "b"]}, 1),
    ("bin_numeric", 0, {"edges": [0, 10, 20]}, None),
    ("bin_numeric", 0, {"edges": [0, 10, 20], "include_lowest": True}, 0),
    ("is_iqr_anomaly", None, {"q1_expression": {"literal": 1}, "q3_expression": {"literal": 3}}, False),
    ("is_modified_zscore_anomaly", 10, {"median_expression": {"literal": 1}, "mad_expression": {"literal": 0}}, False),
    ("mask_redact", None, {}, None),
    ("mask_redact", "secret", {}, "[REDACTED]"),
    ("format_date", "2026-11-11", {"pattern": "%d/%m/%Y"}, "11/11/2026"),
    ("format_number", 12.6, {}, "13"),
    ("format_number", None, {"prefix": "$", "decimal_places": 2}, None),
    ("format_value", "  aB  ", {"data_type": "string", "formatting": {"case": "upper", "trim": True}}, "AB"),
    ("format_value", 12, {"data_type": "integer"}, 12),
])
def test_native_scalar_results(name, value, options, expected):
    assert execute(registry_value(name, value, **options)) == expected


@pytest.mark.parametrize("name,arguments", [
    ("clean_email", {"expression": {"column": "source.typo"}}),
    ("clean_email", {"expression": "raw SQL"}),
    ("clean_email", {"expression": {"literal": "a"}, "typo": True}),
    ("clip", {"expression": {"literal": 1}, "lower": 10, "upper": 0}),
    ("bin_numeric", {"expression": {"literal": 1}, "edges": [0]}),
    ("rule_compare", {"left": {"literal": 1}, "right": {"literal": 1}, "operator": "; DROP TABLE x"}),
    ("clean_numeric", {"expression": {"literal": 1}, "precision": 2, "scale": 3}),
    ("string_pad", {"expression": {"literal": "a"}, "width": 2, "fillchar": "ab"}),
    ("format_value", {"expression": {"literal": 1}, "data_type": "numeric", "formatting": {"decimal_places": "1); DROP TABLE x"}}),
    ("clean_boolean", {"expression": {"literal": "yes"}, "true_values": []}),
    ("mapping", {"expression": {"literal": 1}, "mapping": {}}),
    ("string_pad", {"expression": {"literal": "a"}, "width": None}),
    ("clean_email", {"expression": {"call": {"name": "LOWER"}}}),
    ("clean_email", {"expression": {"column": 5}}),
    ("clean_email", {"expression": {"literal": {"unsafe": "object"}}}),
    ("clean_email", {"expression": {"case": {"when": [], "else": {"literal": "x"}}}}),
])
def test_invalid_registry_parameters(name, arguments):
    with pytest.raises(SpecificationValidationError):
        ExpressionService.validate_value({"registry": {"name": name, "args": arguments}}, {"source": ("email",)}, "mapping")


def test_relation_functions_reuse_existing_stage():
    with pytest.raises(SpecificationValidationError, match=r"mapping.entities.<model>.deduplicate"):
        ExpressionService.validate_value({"registry": {"name": "deduplicate", "args": {}}}, {}, "mapping")


def test_schema_registry_matches_catalogue():
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "src/cadeo/schemas/product.schema.json").read_text())
    names = schema["$defs"]["registryCall"]["properties"]["name"]["enum"]
    assert set(names) == {name for name, item in FunctionRegistryService.catalog()["functions"].items() if item["kind"] == "scalar"}


def test_registry_product_parse_and_compile(tmp_path):
    import yaml
    from cadeo.brokers.file_broker import FileBroker
    from cadeo.services.specification_service import SpecificationService
    from cadeo.services.sqlglot_service import SqlGlotSqlService
    from tests.support import write_document

    root = Path(__file__).resolve().parents[1]
    document = yaml.safe_load((root / "src/cadeo/templates/product.yaml").read_text())
    document["mapping"]["entities"]["device"]["fields"]["merchant_account_number"] = {
        "registry": {"name": "clean_string", "args": {"expression": {"column": "s.merchant_account_number"}}}}
    path = tmp_path / "product.yaml"
    write_document(path, document)
    product = SpecificationService(FileBroker()).load(path)
    assert "REGEXP_REPLACE" in SqlGlotSqlService("duckdb").generate(product.models[0])


def test_catalogue_cli_and_api(tmp_path, capsys):
    from cadeo.exposers.cli import main
    from cadeo.exposers.web import create_app

    assert main(["functions", "--json"]) == 0
    catalogue = json.loads(capsys.readouterr().out)
    assert create_app(tmp_path).test_client().get("/api/functions").json == catalogue
    assert len(catalogue["functions"]) == 104


def test_registry_lineage_ignores_literal_dictionaries():
    from cadeo.services.traceability_service import TraceabilityService

    value = {"registry": {"name": "mapping", "args": {
        "expression": {"column": "source.value"}, "mapping": {"column": "not.a.source"}}}}
    assert TraceabilityService._columns(value) == ("source.value",)


@pytest.mark.parametrize("name", [name for name, definition in FunctionRegistryService.catalog()["functions"].items()
                                  if definition["native"] == "scalar"])
def test_every_native_scalar_executes(name):
    definition = FunctionRegistryService.definition(name)
    examples = {
        "data_type": "string", "mapping": {"3": "three"}, "correction_map": {3: 4},
        "value": "fallback", "lower": 0, "upper": 10, "lower_bound": 0, "upper_bound": 10,
        "replacement": "x", "values": [3], "categories": [3, 4], "edges": [0, 5, 10],
        "operator": ">", "pattern": "(.*)", "prefix": "x", "suffix": "x", "width": 8,
        "replacements": {"3": "4"}, "translations": {"3": "4"}, "percentile": 0.5,
    }
    arguments = {}
    for parameter in definition["required"]:
        if parameter in definition["expression_parameters"]:
            arguments[parameter] = {"literal": True if parameter == "when_expression" else 3}
        elif parameter in definition["expression_lists"]:
            arguments[parameter] = [{"literal": 3}, {"literal": 4}]
        else:
            arguments[parameter] = examples[parameter]
    if name in {"format_date", "format_timestamp", "format_temporal"}:
        arguments["expression"] = {"registry": {"name": "clean_timestamp", "args": {"expression": {"literal": "2026-11-11 12:00:00"}}}}
        if name == "format_temporal":
            arguments["data_type"] = "timestamp"
    execute({"registry": {"name": name, "args": arguments}})


def test_window_statistics_preserve_nulls_and_float_scaling():
    duckdb = pytest.importorskip("duckdb")
    from sqlglot import transpile

    statements = []
    for name in ("min_max_scale", "standard_score", "percentile", "is_zscore_anomaly"):
        arguments = {"expression": {"column": "source.value"}, "partition_by": [{"column": "source.group_id"}]}
        if name == "percentile":
            arguments["percentile"] = 0.5
        value = {"registry": {"name": name, "args": arguments}}
        ExpressionService.validate_value(value, {"source": ("value", "group_id")}, "statistics")
        statements.append(SqlTemplateService().expression(value))
    intermediate = "SELECT " + ", ".join(statements) + " FROM (VALUES (0, 1), (1, 1), (2, 1), (NULL, 1)) AS source(value, group_id) ORDER BY value NULLS LAST"
    with duckdb.connect() as connection:
        rows = connection.execute(transpile(intermediate, read="spark", write="duckdb")[0]).fetchall()
    assert [row[0] for row in rows] == [0, 0.5, 1, None]
    assert [row[1] for row in rows] == [-1, 0, 1, None]
    assert [row[2] for row in rows] == [1, 1, 1, 1]
    assert [row[3] for row in rows] == [False, False, False, False]


@pytest.mark.parametrize("dialect", ["sqlite", "postgres"])
def test_safe_conversion_never_degrades_to_ordinary_cast(dialect):
    from cadeo.services.sqlglot_service import SqlGlotSqlService

    intermediate = "SELECT " + SqlTemplateService().expression(registry_value("clean_integer", "not-a-number"))
    with pytest.raises(SpecificationValidationError, match="refusing to replace TRY_CAST"):
        SqlGlotSqlService(dialect)._translate(intermediate, "safe conversion")


@pytest.mark.parametrize("dialect", ["duckdb", "spark", "bigquery", "snowflake", "tsql"])
def test_sqlglot_safe_conversion_capability(dialect):
    from cadeo.services.sqlglot_service import SqlGlotSqlService

    assert SqlGlotSqlService._supports_safe_cast(dialect)


def test_ordinary_cast_remains_available_on_sqlite():
    from cadeo.services.sqlglot_service import SqlGlotSqlService

    assert "CAST" in SqlGlotSqlService("sqlite")._translate("SELECT CAST(12 AS STRING)", "ordinary cast")