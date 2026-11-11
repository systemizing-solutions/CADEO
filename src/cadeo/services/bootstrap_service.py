"""Create explicitly provisional product drafts from source metadata."""

from __future__ import annotations

import csv
import copy
from dataclasses import dataclass
from datetime import date, datetime
import difflib
from importlib import import_module
from itertools import islice
import json
from pathlib import Path
import re
from typing import Any

from cadeo.brokers.database_broker import DatabaseBroker, DatabaseInspectionError
from cadeo.models.specification import SpecificationValidationError


@dataclass(frozen=True)
class SourceField:
    name: str
    type: str
    nullable: bool = True


@dataclass(frozen=True)
class SourceMetadata:
    name: str
    fields: tuple[SourceField, ...]
    key_columns: tuple[str, ...] = ()
    connection: str | None = None
    url_env: str | None = None
    source_kind: str = "artifact"


class BootstrapService:
    """Infer structure only; business intent remains an explicit review task."""

    REVIEW_ITEMS = (
        "business_meaning", "record_grain", "field_types", "source_and_model_keys", "ownership",
        "mapping_intent", "acceptance_criteria",
    )

    @staticmethod
    def require_reviewed(document: dict[str, Any]) -> None:
        review = document.get("bootstrap")
        if (isinstance(review, dict) and
                (review.get("status") != "reviewed" or review.get("review_required"))):
            raise SpecificationValidationError(
                "Bootstrap draft is review-required; complete the checklist and set "
                "bootstrap.status to reviewed before compile or build")

    def __init__(self, database: DatabaseBroker | None = None) -> None:
        self.database = database or DatabaseBroker()

    def from_table(self, url: str, table: str, connection: str, url_env: str) -> SourceMetadata:
        try:
            reflected = self.database.inspect_table(url, table, (), (), False)
        except DatabaseInspectionError:
            raise
        if not reflected.columns:
            raise SpecificationValidationError(f"No columns found for source table: {table}")
        keys = tuple(getattr(reflected, "primary_key_columns", ()))
        return SourceMetadata(table, tuple(SourceField(item.name, item.type, item.nullable)
                                           for item in reflected.columns), keys,
                              connection, url_env, "sqlalchemy")

    @classmethod
    def from_artifact(cls, path: Path, dialect: str | None = None) -> SourceMetadata:
        suffix = path.suffix.lower()
        if suffix in (".sql", ".ddl"):
            return cls.from_ddl(path, dialect)
        if suffix == ".csv":
            return cls.from_csv(path)
        if suffix in (".parquet", ".pq"):
            return cls.from_parquet(path)
        raise SpecificationValidationError("Input must be a SQL DDL, CSV, or Parquet file")

    @classmethod
    def from_ddl(cls, path: Path, dialect: str | None = None) -> SourceMetadata:
        try:
            import sqlglot
            from sqlglot import exp
            statements = sqlglot.parse(path.read_text(encoding="utf-8"), read=dialect)
        except Exception as error:
            raise SpecificationValidationError(f"Cannot parse SQL DDL ({type(error).__name__})") from error
        creates = [statement for statement in statements if isinstance(statement, exp.Create)
                   and isinstance(statement.this, exp.Schema)]
        if len(creates) != 1:
            raise SpecificationValidationError("DDL input must contain exactly one CREATE TABLE statement")
        create = creates[0]
        table_expression = create.this.this
        table_name = table_expression.name
        schema_name = table_expression.db
        name = f"{schema_name}.{table_name}" if schema_name else table_name
        fields: list[SourceField] = []
        keys: list[str] = []
        for item in create.this.expressions:
            if isinstance(item, exp.ColumnDef):
                constraints = item.args.get("constraints") or []
                nullable = not any(isinstance(constraint.kind, exp.NotNullColumnConstraint)
                                   for constraint in constraints)
                fields.append(SourceField(item.name, cls._type_family(item.args.get("kind")), nullable))
                if any(isinstance(constraint.kind, exp.PrimaryKeyColumnConstraint)
                       for constraint in constraints):
                    keys.append(item.name)
            elif isinstance(item, exp.PrimaryKey):
                keys.extend(column.name for column in item.expressions if isinstance(column, exp.Identifier))
        if not fields:
            raise SpecificationValidationError("DDL input contains no table columns")
        fields = [SourceField(field.name, field.type,
                              False if field.name in keys else field.nullable)
                  for field in fields]
        return SourceMetadata(name, tuple(fields), tuple(dict.fromkeys(keys)), source_kind="sql_ddl")

    @classmethod
    def from_csv(cls, path: Path) -> SourceMetadata:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                headers = reader.fieldnames or []
                if not headers or any(not cls._valid_identifier(name) for name in headers):
                    raise SpecificationValidationError("CSV must have unique, nonempty identifier column headers")
                if len(headers) != len(set(headers)):
                    raise SpecificationValidationError("CSV column headers must be unique")
                samples = list(islice(reader, 1000))
        except UnicodeError as error:
            raise SpecificationValidationError("CSV input must be UTF-8 encoded") from error
        fields = tuple(SourceField(name, cls._infer_csv_type([row.get(name) or "" for row in samples]),
                                   not samples or any(not (row.get(name) or "").strip() for row in samples))
                       for name in headers)
        return SourceMetadata(cls._identifier(path.stem), fields, source_kind="csv")

    @classmethod
    def from_parquet(cls, path: Path) -> SourceMetadata:
        try:
            parquet = import_module("pyarrow.parquet")
        except ImportError as error:
            raise SpecificationValidationError(
                "Parquet introspection requires the optional pyarrow package; install it with 'pip install pyarrow'") from error
        try:
            schema = parquet.read_schema(path)
        except Exception as error:
            raise SpecificationValidationError(f"Cannot inspect Parquet metadata ({type(error).__name__})") from error
        fields = tuple(SourceField(field.name, cls._type_family(field.type), field.nullable)
                       for field in schema)
        if not fields:
            raise SpecificationValidationError("Parquet input contains no columns")
        return SourceMetadata(cls._identifier(path.stem), fields, source_kind="parquet")

    @classmethod
    def draft(cls, source: SourceMetadata, product_name: str | None = None,
              schema_path: str = "product.schema-v0.9.0.json") -> dict[str, Any]:
        fields = source.fields
        if not fields:
            raise SpecificationValidationError("Cannot bootstrap a dataset without columns")
        qualified_name = r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}"
        if not re.fullmatch(qualified_name, source.name):
            raise SpecificationValidationError(f"Unsupported source table identifier: {source.name}")
        invalid_fields = [field.name for field in fields if not cls._valid_identifier(field.name)]
        if invalid_fields:
            raise SpecificationValidationError(
                "Unsupported source column identifiers: " + ", ".join(invalid_fields))
        source_id = cls._identifier(source.name)
        model_id = source_id
        candidate_keys = tuple(name for name in source.key_columns if any(f.name == name for f in fields))
        if not candidate_keys:
            candidate_keys = (fields[0].name,)
        source_keys = set(candidate_keys)
        dataset_columns = {field.name: {"type": field.type,
                                        "nullable": False if field.name in source_keys else field.nullable}
                           for field in fields}
        canonical_attributes = {field.name: {"type": field.type,
                                             "nullable": False if field.name in source_keys else field.nullable}
                               for field in fields}
        alias = source_id
        title = product_name or cls._identifier(source.name)
        dataset: dict[str, Any] = {
            "table": source.name,
            "alias": alias,
            "columns": dataset_columns,
            "record_key_columns": list(candidate_keys),
            "contract": {"owner": "REVIEW REQUIRED", "freshness_hours": 24},
        }
        if source.connection:
            dataset["connection"] = source.connection
        definition = {
            "name": cls._identifier(title),
            "description": "REVIEW REQUIRED: describe the business product and its intended use.",
            "owner": {"team": "REVIEW REQUIRED", "contact": "REVIEW REQUIRED"},
            "consumers": [{"id": "review", "purpose": "REVIEW REQUIRED", "access": "read"}],
            "outcome": "REVIEW REQUIRED: state the business outcome.",
            "requirements": [{"id": "REQ_REVIEW", "statement": "REVIEW REQUIRED: add an owned business requirement."}],
            "features": [{"id": "FEAT_REVIEW", "description": "REVIEW REQUIRED: define delivered behavior.",
                          "requirement_ids": ["REQ_REVIEW"],
                          "acceptance_criteria": [{"id": "AC_REVIEW",
                                                   "statement": "REVIEW REQUIRED: define measurable acceptance criteria."}]}],
        }
        entity = {"entity_name": "REVIEW_REQUIRED", "record_grain": "REVIEW REQUIRED: define one row's business meaning.",
                  "unique_key_attributes": list(candidate_keys), "attributes": canonical_attributes}
        mapping = {"source": source_id,
                   "fields": {field.name: {"column": f"{alias}.{field.name}"} for field in fields}}
        document: dict[str, Any] = {
            "schema": {"path": schema_path, "version": "0.9.0"},
            "spec_version": "0.9.0",
            "definition": definition,
            "model": {"entities": {model_id: entity}},
            "sources": {"datasets": {source_id: dataset}},
            "mapping": {"entities": {model_id: mapping}},
            "assurance": {"quality": {"on_failure": "block"},
                          "entities": {model_id: {"reconciliation": {"allow": []},
                                                  "preservation": {"mode": "snapshot"}, "tests": {}}}},
            "delivery": {"output": "./generated", "dialect": "spark",
                         "refresh": {"schedule": "0 5 * * *", "timezone": "UTC"},
                         "recovery": {"strategy": "rerun", "max_attempts": 1,
                                      "runbook": "REVIEW REQUIRED"},
                         "targets": {model_id: {"table": f"review.{model_id}"}}},
            "bootstrap": {"status": "review_required", "review_required": list(cls.REVIEW_ITEMS),
                          "source": {"kind": source.source_kind, "name": source.name},
                          "generated": {"sources": {"datasets": {source_id: copy.deepcopy(dataset)}},
                                        "model": {"entities": {model_id: copy.deepcopy(entity)}},
                                        "mapping": {"entities": {model_id: copy.deepcopy(mapping)}}}},
        }
        if source.connection and source.url_env:
            document["sources"]["connections"] = {source.connection: {"url_env": source.url_env}}
        return document

    @classmethod
    def refresh(cls, current: dict[str, Any], source: SourceMetadata) -> tuple[dict[str, Any], str]:
        """Return a three-way merged proposal and unified diff; never mutate the input."""
        bootstrap = current.get("bootstrap")
        if not isinstance(bootstrap, dict) or not isinstance(bootstrap.get("generated"), dict):
            raise SpecificationValidationError("Refresh requires a CADEO draft with bootstrap.generated metadata")
        fresh = cls.draft(source, current.get("definition", {}).get("name"),
                          current.get("schema", {}).get("path", "product.schema-v0.9.0.json"))
        proposal = json.loads(json.dumps(current))
        old_generated = bootstrap["generated"]
        new_generated = fresh["bootstrap"]["generated"]
        for section in ("sources", "model", "mapping"):
            proposal[section] = cls._merge_generated(proposal.get(section, {}),
                                                     old_generated.get(section, {}),
                                                     new_generated.get(section, {}))
        proposal["bootstrap"]["generated"] = new_generated
        proposal["bootstrap"]["source"] = fresh["bootstrap"]["source"]
        before = cls.serialize(current).splitlines(keepends=True)
        after = cls.serialize(proposal).splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(before, after, fromfile="current draft", tofile="refreshed proposal"))
        return proposal, diff

    @classmethod
    def _merge_generated(cls, current: Any, previous: Any, fresh: Any) -> Any:
        if current == previous:
            return fresh
        if isinstance(current, dict) and isinstance(previous, dict) and isinstance(fresh, dict):
            merged = dict(current)
            for key, new_value in fresh.items():
                if key in previous:
                    if key not in current:
                        continue
                    merged[key] = cls._merge_generated(current.get(key), previous[key], new_value)
                elif key not in current:
                    merged[key] = new_value
            return merged
        return current

    @staticmethod
    def serialize(document: dict[str, Any]) -> str:
        import yaml
        return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)

    @staticmethod
    def _valid_identifier(value: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""))

    @classmethod
    def _identifier(cls, value: str) -> str:
        result = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip()).strip("_").lower()
        if not result or result[0].isdigit():
            result = "source_" + result
        return result

    @staticmethod
    def _type_family(value: Any) -> str:
        text = str(value).lower()
        if any(token in text for token in ("bool",)):
            return "boolean"
        if any(token in text for token in ("int", "serial", "long")):
            return "integer"
        if any(token in text for token in ("float", "double", "decimal", "numeric", "real")):
            return "float"
        if "timestamp" in text or "datetime" in text:
            return "timestamp"
        if re.match(r"^date\d*(?:\[|$)", text):
            return "date"
        return "string"

    @classmethod
    def _infer_csv_type(cls, values: list[str]) -> str:
        present = [value.strip() for value in values if value and value.strip()]
        if not present:
            return "string"
        if all(value.lower() in ("true", "false") for value in present):
            return "boolean"
        if all(re.fullmatch(r"[+-]?\d+", value) for value in present):
            return "integer"
        if all(re.fullmatch(r"[+-]?(?:\d+\.\d*|\d*\.\d+)(?:[eE][+-]?\d+)?", value)
               or re.fullmatch(r"[+-]?\d+[eE][+-]?\d+", value) for value in present):
            return "float"
        try:
            for value in present:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            return "timestamp"
        except ValueError:
            pass
        try:
            for value in present:
                date.fromisoformat(value)
            return "date"
        except ValueError:
            return "string"
