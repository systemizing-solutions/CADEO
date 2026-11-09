"""Validate public JSON Schema, then normalize to the shared canonical IR."""

import json
import copy
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from functools import lru_cache
import re
from pathlib import Path, PureWindowsPath
from typing import Any

from jsonschema import Draft202012Validator
import yaml
from yaml.constructor import ConstructorError

from cadeo.brokers.file_broker import FileBrokerContract
from cadeo.models.specification import (
    Attribute,
    ArtifactPaths,
    AcceptanceCriterion,
    Business,
    Consumer,
    Feature,
    Owner,
    Requirement,
    SourceContract,
    Connection,
    Execution,
    Refresh,
    Quality,
    Recovery,
    Operations,
    CanonicalModel, CanonicalRelationship,
    Join,
    JoinCondition,
    Mapping,
    Product,
    Source,
    Specification,
    DataProduct,
    SpecificationValidationError,
)


class UniqueKeysLoader(yaml.SafeLoader):
    """Reject duplicate keys rather than silently dropping mappings."""


def _construct_unique_mapping(loader: UniqueKeysLoader, node: yaml.MappingNode) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str):
            raise ConstructorError(None, None, "YAML object keys must be strings", key_node.start_mark)
        if key in result:
            raise ConstructorError(None, None, f"duplicate YAML key: {key}", key_node.start_mark)
        result[key] = loader.construct_object(value_node, deep=True)
    return result


# PyYAML follows YAML 1.1 and otherwise turns the field name `on` into True.
# Use YAML 1.2-style boolean literals (true / false) in this loader only.
UniqueKeysLoader.yaml_implicit_resolvers = {
    key: [(tag, regex) for tag, regex in resolvers if tag != "tag:yaml.org,2002:bool"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
UniqueKeysLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$"), list("tTfF")
)
UniqueKeysLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


from cadeo.models.specification import Branch, Deduplication, Preservation, Reconciliation, Sort
from cadeo.services.expression_service import ExpressionService


class SpecificationService:
    """Parse a format-independent dictionary against the public contract and meaning."""

    SCHEMA_PATH = "schemas/product.schema.json"

    def __init__(self, files: FileBrokerContract, schema_text: str | None = None):
        self.files = files
        self._explicit_schema = schema_text is not None
        packaged = files.read_bundled_text(self.SCHEMA_PATH)
        self._schema_text = schema_text if schema_text is not None else packaged
        self._validator = self._build_validator(self._schema_text)
        # A selected schema can narrow/describe the product contract, but cannot
        # silently authorize fields or expressions the compiler doesn't implement.
        core_schema = json.loads(packaged)
        selected_schema = json.loads(self._schema_text)
        selected_version = selected_schema.get('properties', {}).get('spec_version', {}).get('const')
        if selected_version is not None and selected_version != core_schema['properties']['spec_version']['const']:
            core_schema['properties']['spec_version']['const'] = selected_version
            self._core_validator = self._build_validator(json.dumps(core_schema))
        else:
            self._core_validator = self._build_validator(packaged)

    @staticmethod
    @lru_cache(maxsize=4)
    def _build_validator(schema_text: str) -> Draft202012Validator:
        """Schema validation is expensive; reuse the immutable validator for equal contracts."""
        schema = json.loads(schema_text)
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)

    def schema_json(self) -> str:
        return self._schema_text

    def for_file(self, path: Path) -> "SpecificationService":
        """Resolve exactly the schema pinned by this YAML file, never fall back.

        Standalone CLI, recursive project builds and the web editor use this same
        resolver. A schema is read relative to the YAML location, not the cwd.
        """
        document = self._read_yaml(path)
        if not isinstance(document, dict):
            raise SpecificationValidationError("Specification YAML must contain an object")
        ref = document.get("schema")
        if not isinstance(ref, dict) or "path" not in ref or "version" not in ref:
            raise SpecificationValidationError("schema.path and schema.version are required")
        from cadeo.services.schema_catalog_service import SchemaCatalogService
        relative = SchemaCatalogService.relative_path(ref["path"])
        candidate = (path.resolve().parent / relative).resolve()
        if not candidate.is_file():
            raise SpecificationValidationError(f"Referenced JSON Schema does not exist: {candidate}")
        try:
            selected = SchemaCatalogService.validate_schema(json.loads(self.files.read_text(candidate)))
        except (ValueError, UnicodeError) as error:
            if isinstance(error, SpecificationValidationError):
                raise
            raise SpecificationValidationError(f"Invalid referenced JSON Schema {candidate}: {error}") from error
        loaded = json.loads(selected)
        if ref["version"] != loaded["x-schema-version"]:
            raise SpecificationValidationError(
                f"schema.version mismatch: YAML pins {ref['version']!r}, "
                f"but {candidate} declares {loaded['x-schema-version']!r}")
        return SpecificationService(self.files, schema_text=selected)

    def _read_yaml(self, path: Path) -> Any:
        try:
            return yaml.load(self.files.read_text(path), Loader=UniqueKeysLoader)
        except yaml.YAMLError as error:
            raise SpecificationValidationError(f"Invalid YAML: {error}") from error

    def read_document(self, path: Path) -> dict[str, Any]:
        """Load YAML using its pinned schema before returning the document."""
        document = self._read_yaml(path)
        self.for_file(path).validate_structure(document)
        return document

    def load(self, path: Path) -> DataProduct:
        resolver = self.for_file(path)
        return resolver.parse(self._read_yaml(path))

    def validate_structure(self, document: Any) -> None:
        if not isinstance(document, dict):
            raise SpecificationValidationError("Specification must be a YAML object")
        # Validate the explicit version pin even for drafts parsed in memory.
        ref = document.get("schema")
        if isinstance(ref, dict) and isinstance(ref.get("version"), str):
            declared = json.loads(self._schema_text).get("x-schema-version")
            if ref["version"] != declared:
                raise SpecificationValidationError(
                    f"schema.version mismatch: YAML pins {ref['version']!r}, selected schema declares {declared!r}")
        errors = list(self._core_validator.iter_errors(document))
        if self._validator is not self._core_validator:
            errors.extend(self._validator.iter_errors(document))
        if errors:
            messages = sorted({self._error_message(error) for error in errors})
            raise SpecificationValidationError("Invalid specification:\n  - " + "\n  - ".join(messages))

    def findings(self, document: Any, product_catalog: dict[str, Specification] | None = None) -> list[dict]:
        """One validation pipeline for the CLI and both editor views.

        Each finding has a public YAML path, message and suggested correction.
        Structural problems are collected together; semantic validation follows
        only after structural validation succeeds.
        """
        if not isinstance(document, dict):
            return [{"path": "$", "message": "Specification must be a YAML object", "suggestion": "Start with schema, spec_version and the six stages.", "severity": "error"}]
        structural = list(self._core_validator.iter_errors(document))
        if self._validator is not self._core_validator:
            structural.extend(self._validator.iter_errors(document))
        issues = []
        seen = set()
        for error in structural:
            path = '.'.join(str(x) for x in error.absolute_path)
            if error.validator == 'required':
                missing = sorted(set(error.validator_value) - set(error.instance))
                for key in missing:
                    name = '.'.join(x for x in (path, key) if x)
                    if name not in seen:
                        issues.append({"path": name, "message": f"Required field {key} is missing", "suggestion": f"Add {key} in the {name.split('.')[0]} stage.", "severity": "error"})
                        seen.add(name)
            else:
                name = path or '$'
                msg = self._error_message(error)
                signature = (name, msg)
                if signature not in seen:
                    issues.append({"path": name, "message": msg, "suggestion": "Review the field description, permitted values and type in the JSON Schema.", "severity": "error"})
                    seen.add(signature)
        if issues:
            return sorted(issues, key=lambda x:(x['path'], x['message']))
        try:
            self.parse(document, product_catalog)
        except SpecificationValidationError as error:
            message = str(error)
            name = message.split(':', 1)[0] if ':' in message else '$'
            # Semantic checks in the compiler use normalized IR paths. Translate
            # those paths to the authoritative public YAML field locations.
            prefixes = {'canonical': 'model.entities', 'business': 'definition',
                        'product.consumers': 'definition.consumers',
                        'product.target': 'delivery.target',
                        'product': 'definition', 'connections': 'sources.connections',
                        'sources': 'sources.datasets', 'operations.quality': 'assurance.quality',
                        'operations.refresh': 'delivery.refresh', 'operations.recovery': 'delivery.recovery',
                        'reconciliation': 'assurance.reconciliation', 'preservation': 'assurance.preservation',
                        'tests': 'assurance.tests', 'mapping': 'mapping.fields',
                        'execution': 'delivery.execution', 'deduplicate': 'mapping.deduplicate'}
            for old, renamed in sorted(prefixes.items(), key=lambda item:-len(item[0])):
                if name == old or name.startswith(old+'.'):
                    name = renamed + name[len(old):]
                    break
            issues.append({"path": name, "message": message,
                           "suggestion": "Check the referenced definition and its declared IDs. Use the same IDs in every mapping, rule and test reference.",
                           "severity": "error"})
        return issues

    def parse(self, document: Any, product_catalog: dict[str, Specification] | None = None) -> DataProduct:
        """Validate one public data product and normalize its model build units.

        Each model has distinct schema, STTM, assurance, target and quality gate;
        all model units use the original shared SQL/traceability/execution services.
        """
        self.validate_structure(document)
        from cadeo.services.dependency_service import DependencyNode, DependencyService
        from cadeo.services.specification_layout_service import to_ir_fields
        registries = (document['model']['entities'], document['mapping']['entities'],
                      document['assurance']['entities'], document['delivery']['targets'])
        models = set(registries[0])
        for name, entries in zip(('model.entities','mapping.entities','assurance.entities',
                                  'delivery.targets'), registries):
            missing, extra = models-set(entries), set(entries)-models
            self._require(not missing and not extra,
                f'{name}: model IDs must match model.entities: missing={sorted(missing)}, extra={sorted(extra)}')
        targets = [value['table'] for value in document['delivery']['targets'].values()]
        self._require(len(targets) == len(set(targets)),
                      'delivery.targets: two canonical models cannot publish to the same table')
        registry = document['sources']['datasets']
        model_dependencies = {}
        all_used = set()
        for model_id, mapping in document['mapping']['entities'].items():
            branch_defs = [mapping, *mapping.get('unions', [])]
            refs = set()
            for branch in branch_defs:
                refs.add(branch['source'])
                refs.update(join['source'] for join in branch.get('joins', []))
            for source_id in tuple(refs):
                self._require(source_id in registry,
                    f'mapping.entities.{model_id}: unknown source {source_id}')
            # Resolve source-to-source dependencies, sharing the authoritative registry.
            pending = list(refs)
            while pending:
                source_id = pending.pop()
                for depends_on in registry[source_id].get('depends_on', []):
                    self._require(depends_on in registry,
                        f'sources.datasets.{source_id}.depends_on: unknown source {depends_on}')
                    if depends_on not in refs:
                        refs.add(depends_on); pending.append(depends_on)
            all_used.update(refs)
            deps = {registry[source_id]['model_ref'] for source_id in refs
                    if 'model_ref' in registry[source_id]}
            for dependency in deps:
                self._require(dependency in models,
                    f'mapping.entities.{model_id}: model_ref {dependency!r} does not exist')
            model_dependencies[model_id] = DependencyNode(model_id, 'model', tuple(sorted(deps)))
        self._require(all_used == set(registry),
            f'sources.datasets: unused source definitions {sorted(set(registry)-all_used)}')
        for feature in document['definition']['features']:
            for criterion in feature['acceptance_criteria']:
                for check in criterion.get('check_ids', []):
                    model_id = check.split('/',1)[0]
                    self._require(model_id in models,
                        f'definition.features.{feature["id"]}.acceptance_criteria.{criterion["id"]}: '
                        f'check {check!r} references unknown model {model_id!r}')
        ordered = DependencyService._sort(model_dependencies)  # Unknown refs / cycles fail first.
        catalog = dict(product_catalog or {})
        internal = []
        for node in ordered:
            model_id = node.id
            ir = to_ir_fields(document, model_id)
            # A model's internal source registry contains only source contracts it
            # actually uses; other models may depend on sources not needed here.
            used = set()
            branches = [ir, *ir.get('unions', [])]
            for branch in branches:
                used.add(branch['source'])
                used.update(join['source'] for join in branch.get('joins', []))
            pending = list(used)
            while pending:
                for dep in registry[pending.pop()].get('depends_on', []):
                    if dep not in used:
                        used.add(dep); pending.append(dep)
            ir['sources'] = {source_id: ir['sources'][source_id]
                             for source_id in ir['sources'] if source_id in used}
            # Internal model IDs use a collision-free namespace within the project.
            try:
                model = self._parse_model(ir, document, model_id, catalog)
            except SpecificationValidationError as error:
                message = str(error)
                prefix, sep, description = message.partition(':')
                if sep:
                    for old, new in (
                        ('canonical.',f'model.entities.{model_id}.'),
                        ('model.',f'model.entities.{model_id}.'),
                        ('mapping.',f'mapping.entities.{model_id}.'),
                        ('deduplicate.',f'mapping.entities.{model_id}.deduplicate.'),
                        ('reconciliation.',f'assurance.entities.{model_id}.reconciliation.'),
                        ('preservation.',f'assurance.entities.{model_id}.preservation.'),
                        ('tests.',f'assurance.entities.{model_id}.tests.'),
                        ('product.target',f'delivery.targets.{model_id}.table')):
                        if prefix.startswith(old):
                            prefix = new + prefix[len(old):]
                            break
                    else:
                        prefix = f'model.entities.{model_id}' if not prefix.startswith((
                            'definition','sources','delivery','assurance')) else prefix
                    raise SpecificationValidationError(prefix + ':' + description) from error
                raise SpecificationValidationError(f'model.entities.{model_id}: {message}') from error
            internal.append(model)
            catalog[model.product.name] = model
        return DataProduct(document['definition']['name'], tuple(internal), copy.deepcopy(document))

    def _parse_model(self, document: dict, public_document: dict, model_id: str,
                     product_catalog: dict[str, Specification]) -> Specification:
        """Normalize one model into the same validated IR used by every backend."""

        from cadeo.services.dialect_service import DialectService
        if document.get("dialect") is not None:
            DialectService.validate(document["dialect"])

        relative_output = document.get("output")
        if relative_output is not None:
            # Host-platform Path checks alone would miss Windows absolute paths on Linux.
            self._require(not Path(relative_output).is_absolute()
                          and not PureWindowsPath(relative_output).drive
                          and not relative_output.startswith("\\"),
                          "output must be a relative directory path (relative to the YAML file)")
            self._require("\\" not in relative_output,
                          "output must use forward slashes as directory separators")

        product_data = document["product"]
        owner = product_data["owner"]
        product = Product(
            product_data["name"], product_data["target"], product_data.get("description", ""),
            Owner(owner["team"], owner["contact"]),
            tuple(Consumer(c["id"], c["purpose"], c["access"]) for c in product_data["consumers"]),
        )
        consumer_ids = [consumer.id for consumer in product.consumers]
        self._require(len(set(consumer_ids)) == len(consumer_ids), "definition.consumers: duplicate ID")
        business = self._parse_business(document["business"])
        connections = tuple(Connection(key, value["url_env"])
                            for key, value in document.get("connections", {}).items())
        source_connections = {connection.id for connection in connections}
        sources = self._parse_sources(document["sources"], product.target, product_catalog, public_document["definition"]["name"])
        for source in sources.values():
            if connections and source.product_ref is None:
                self._require(source.connection is not None,
                              f"sources.{source.id}.connection: required when connections is declared")
            if source.connection is not None:
                self._require(source.connection in source_connections,
                              f"sources.{source.id}.connection: unknown connection {source.connection}")
        execution = None
        if document.get("execution") is not None:
            connection_id = document["execution"]["connection"]
            self._require(connection_id in source_connections,
                          f"execution.connection: unknown connection {connection_id}")
            execution = Execution(connection_id)
        used_connections = {source.connection for source in sources.values() if source.connection}
        if execution:
            used_connections.add(execution.connection)
        # Connections are product-scoped; one model need not use every connection.
        operations = self._parse_operations(document["operations"])
        canonical_data = document["canonical"]
        attributes = tuple(Attribute(name, item["type"], item.get("nullable", True),
                                     tuple(item.get("requirement_ids", [])))
                           for name, item in canonical_data["attributes"].items())
        names = {attr.name for attr in attributes}
        self._require(all(not name.startswith("__dp_") for name in names),
                      "canonical attributes cannot start with reserved prefix '__dp_'")
        keys = tuple(canonical_data["key"])
        for key in keys:
            self._require(key in names, f"model.unique_key_attributes: unknown canonical attribute: {key}")
            self._require(not next(a for a in attributes if a.name == key).nullable,
                          f"model.unique_key_attributes: unique key attribute {key} must be non-nullable (set nullable: false)")
        relationships = tuple(CanonicalRelationship(
            item["attribute"], item["entity"], item["cardinality"],
            item.get("related_attribute"))
            for item in public_document["model"]["entities"][model_id].get("relationships", []))
        for index, relationship in enumerate(relationships):
            self._require(relationship.attribute in names,
                          f"model.relationships.{index}.attribute: unknown canonical attribute {relationship.attribute}")
        canonical = CanonicalModel(canonical_data["entity"], canonical_data["grain"], keys,
                                   attributes, relationships)

        raw_branches = [document] + document.get("unions", [])
        branches = tuple(self._parse_branch(raw, index, attributes, sources)
                         for index, raw in enumerate(raw_branches))
        used_sources = {branch.source.id for branch in branches}
        used_sources.update(join.source.id for branch in branches for join in branch.joins)
        # Upstream sources are dependencies even when not mapped directly.
        for source in sources.values():
            for upstream in source.depends_on:
                self._require(upstream in sources,
                              f"sources.{source.id}.depends_on: unknown dependency {upstream}")
        pending = list(used_sources)
        while pending:
            for upstream in sources[pending.pop()].depends_on:
                if upstream not in used_sources:
                    used_sources.add(upstream)
                    pending.append(upstream)
        # The bundle validator checks unused source definitions across ALL models.
        dedup = None
        if "deduplicate" in document:
            info = document["deduplicate"]
            by = tuple(info["by"])
            self._require(set(by) == set(keys), "mapping.deduplicate.by must match model.unique_key_attributes")
            sorts = tuple(Sort(item["field"], item["direction"]) for item in info["order_by"])
            for sort in sorts:
                self._require(sort.field in names, f"deduplicate.order_by: unknown field: {sort.field}")
            self._require(len(set(item.field for item in sorts)) == len(sorts),
                          "deduplicate.order_by must not repeat fields")
            dedup = Deduplication(by=by, order_by=sorts)
        if len(branches) > 1:
            self._require(dedup is not None or len(set(b.priority for b in branches)) == 1,
                          "source priority only affects output when deduplicate is configured")
        reconciliation = Reconciliation(tuple(document["reconciliation"].get("allow", [])))
        preservation_data = document["preservation"]
        preservation = Preservation(preservation_data["mode"], preservation_data.get("reference"))
        if preservation.mode == "snapshot":
            archive_names = set()
            target_parts = product.target.split(".")
            for i, branch in enumerate(branches):
                branch_inputs = [(branch.source.table, branch.source.alias)]
                branch_inputs.extend((join.table, join.alias) for join in branch.joins)
                for _, alias in branch_inputs:
                    archive_names.add(".".join(target_parts[:-1] + [f"{target_parts[-1]}__raw_{i}_{alias}"]))
            for branch in branches:
                for table in [branch.source.table] + [join.table for join in branch.joins]:
                    self._require(table not in archive_names,
                                  f"snapshot archive would overwrite its input source: {table}")
        stage = ".".join(product.target.split(".")[:-1] + [product.target.split(".")[-1] + "__dp_stage"])
        self._require(all(src.table != stage for src in sources.values()),
                      f"staging view {stage} would overwrite a registered source")
        from cadeo.models.specification import BusinessTest
        declarative = tuple(BusinessTest(test_id, definition["type"], copy.deepcopy(definition))
                            for test_id, definition in document.get("tests", {}).items())
        self._validate_tests(declarative, canonical, sources, branches)
        specification = Specification(
            document["spec_version"], product, canonical, branches, dedup, reconciliation,
            preservation, business, tuple(sources.values()), operations, copy.deepcopy(public_document),
            relative_output, document.get("dialect"), connections, execution, declarative,
            ArtifactPaths(**document.get('artifact_paths', {})),
        )
        from cadeo.services.dependency_service import DependencyService
        DependencyService().build(specification)  # Validate one model dependency DAG.
        from cadeo.services.traceability_service import TraceabilityService
        TraceabilityService().build(specification)  # Reject unknown references; warn on missing links.
        return specification

    @staticmethod
    def _validate_tests(tests, canonical, sources, branches) -> None:
        from cadeo.services.expression_service import ExpressionService
        attributes = {attr.name: attr for attr in canonical.attributes}
        registry = {src.id: src for src in sources.values()}
        used = {b.source.id for b in branches}
        used.update(j.source.id for b in branches for j in b.joins)
        builtins = set()  # Check IDs are already generated by one authoritative service.
        # Use only generated baseline checks for collision detection (no recursion).
        # The baseline registry depends solely on canonical, joins and reconciliation.
        builtins.update(f"not_null:{a.name}" for a in canonical.attributes if not a.nullable)
        builtins.update(("canonical_key_unique", "reconciliation"))
        if any(b.joins for b in branches):
            for index, branch in enumerate(branches):
                builtins.update(f"join_unique:{index}:{j.source.id}" for j in branch.joins if j.expect_unique)
        reserved = {"deduplication_ties"}
        for test in tests:
            where = f"tests.{test.id}"
            if (test.id in builtins or test.id in reserved or
                    test.id.startswith(("not_null:", "join_unique:", "snapshot:"))):
                raise SpecificationValidationError(f"{where}: ID collides with a generated check")
            d = test.definition
            if test.type in ("accepted_values", "relationships"):
                if d["column"] not in attributes:
                    raise SpecificationValidationError(f"{where}.column: unknown canonical attribute {d['column']}")
            if test.type == "accepted_values":
                for index, value in enumerate(d["values"]):
                    SpecificationService._check_scalar(
                        value, attributes[d["column"]], f"{where}.values[{index}]")
            if test.type == "relationships":
                source = registry.get(d["source"])
                if source is None or source.id not in used:
                    raise SpecificationValidationError(f"{where}.source: source must be registered and used in the mapping")
                if d["field"] not in source.columns:
                    raise SpecificationValidationError(f"{where}.field: unknown column on source {source.id}")
                right = next(c for c in source.column_contracts if c.name == d["field"])
                if right.type != attributes[d["column"]].type:
                    raise SpecificationValidationError(
                        f"{where}.field: incompatible source type {right.type}; "
                        f"canonical {d['column']} is {attributes[d['column']].type}")
            elif test.type == "expression":
                ExpressionService.validate_predicate(d["predicate"],
                    {"t": tuple(attributes)}, where + ".predicate")
            elif test.type == "expected_rows":
                if set(d["inputs"]) != used:
                    raise SpecificationValidationError(
                        f"{where}.inputs: expected source IDs {sorted(used)}, "
                        f"missing={sorted(used - set(d['inputs']))}, extra={sorted(set(d['inputs']) - used)}")
                for source_id, rows in d["inputs"].items():
                    for index, row in enumerate(rows):
                        SpecificationService._check_fixture_row(
                            row, registry[source_id].column_contracts,
                            f"{where}.inputs.{source_id}[{index}]")
                for index, row in enumerate(d["expected"]):
                    SpecificationService._check_fixture_row(
                        row, canonical.attributes, f"{where}.expected[{index}]")

    @staticmethod
    def _check_fixture_row(row, attributes, location):
        fields = {a.name: a for a in attributes}
        if set(row) != set(fields):
            raise SpecificationValidationError(
                f"{location}: fixture columns mismatch: missing={sorted(set(fields)-set(row))}, "
                f"extra={sorted(set(row)-set(fields))}")
        for name, value in row.items():
            SpecificationService._check_scalar(value, fields[name], f"{location}.{name}")

    @staticmethod
    def _check_scalar(value, attr, location):
        import math
        if value is None:
            if not attr.nullable:
                raise SpecificationValidationError(f"{location}: non-nullable value is NULL")
            return
        expected = {
            "string": lambda v: isinstance(v, str),
            "integer": lambda v: type(v) is int,
            "float": lambda v: type(v) in (float, int),
            "boolean": lambda v: type(v) is bool,
            "date": lambda v: isinstance(v, str),
            "timestamp": lambda v: isinstance(v, str),
        }[attr.type]
        if not expected(value) or (type(value) is float and not math.isfinite(value)):
            raise SpecificationValidationError(f"{location}: expected finite {attr.type} value")

    def _parse_business(self, data: dict) -> Business:
        requirements = tuple(Requirement(item["id"], item["statement"]) for item in data["requirements"])
        req_ids = [item.id for item in requirements]
        self._require(len(set(req_ids)) == len(req_ids), "business.requirements: duplicate requirement ID")
        features = tuple(Feature(item["id"], item["description"], tuple(item["requirement_ids"]),
                                 tuple(AcceptanceCriterion(ac["id"], ac["statement"], tuple(ac.get("check_ids", [])))
                                       for ac in item["acceptance_criteria"]))
                         for item in data["features"])
        feature_ids = [f.id for f in features]
        self._require(len(set(feature_ids)) == len(feature_ids), "business.features: duplicate feature ID")
        all_criteria = [ac.id for feature in features for ac in feature.acceptance_criteria]
        self._require(len(all_criteria) == len(set(all_criteria)), "business.features: duplicate acceptance criterion ID")
        linked = set()
        for feature in features:
            unknown = set(feature.requirement_ids) - set(req_ids)
            self._require(not unknown,
                          f"business.features.{feature.id}: unknown requirement IDs {sorted(unknown)}")
            linked.update(feature.requirement_ids)
        self._require(linked == set(req_ids),
                      f"business.requirements: requirements without features {sorted(set(req_ids) - linked)}")
        return Business(data["outcome"], requirements, features)

    def _parse_sources(self, definitions: dict, target: str,
                       product_catalog: dict[str, Specification], bundle_name: str) -> dict[str, Source]:
        sources = {}
        identities = {}
        for source_id, definition in definitions.items():
            if 'model_ref' in definition or 'product_ref' in definition:
                ref = (bundle_name + '.' + definition['model_ref']) if 'model_ref' in definition else \
                    definition['product_ref']
                self._require(ref in product_catalog,
                              f"sources.datasets.{source_id}: unresolved model reference '{ref}'. "
                              "Model refs must be upstream; cross-product refs require a project directory.")
                upstream = product_catalog[ref]
                alias = definition.get("alias", source_id)
                self._require(upstream.product.target != target,
                              f"sources.{source_id}.product_ref: cannot reference its own output target")
                attrs = upstream.canonical.attributes
                sources[source_id] = Source(
                    source_id, upstream.product.target,
                    tuple(attr.name for attr in attrs), upstream.canonical.key, alias,
                    tuple(Attribute(attr.name, attr.type, attr.nullable) for attr in attrs),
                    None, product_ref=ref)
                continue
            identity = (definition["table"], definition["alias"])
            self._require(identity not in identities,
                          f"sources.{source_id}: duplicate source definition (already registered as {identities.get(identity)})")
            identities[identity] = source_id
            columns = tuple(Attribute(name, col["type"], col.get("nullable", True))
                            for name, col in definition["columns"].items())
            key = tuple(definition["record_key_columns"])
            for key_name in key:
                self._require(key_name in definition["columns"],
                              f"sources.{source_id}.record_key_columns: unknown column {key_name}")
                self._require(not definition["columns"][key_name].get("nullable", True),
                              f"sources.{source_id}.record_key_columns: key {key_name} must be non-nullable")
            self._require(definition["table"] != target,
                          f"sources.{source_id}.table cannot equal product.target")
            contract = definition["contract"]
            sources[source_id] = Source(
                source_id, definition["table"], tuple(definition["columns"]),
                key, definition["alias"], columns,
                SourceContract(contract["owner"], contract["freshness_hours"]),
                depends_on=tuple(definition.get("depends_on", [])),
                connection=definition.get("connection"))
        return sources

    def _parse_operations(self, data: dict) -> Operations:
        refresh = data["refresh"]
        self._require(self._valid_cron(refresh["schedule"]),
                      "operations.refresh.schedule must be a valid five-field numeric cron expression")
        try:
            ZoneInfo(refresh["timezone"])
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise SpecificationValidationError("operations.refresh.timezone must be a valid IANA timezone") from error
        quality = data["quality"]
        recovery = data["recovery"]
        return Operations(Refresh(refresh["schedule"], refresh["timezone"]),
                          Quality(quality["on_failure"]),
                          Recovery(recovery["strategy"], recovery["max_attempts"], recovery["runbook"]))

    @staticmethod
    def _valid_cron(value: str) -> bool:
        """Validate five-field numeric cron without introducing an orchestrator dependency."""
        fields = value.split()
        if len(fields) != 5:
            return False
        for field, (minimum, maximum) in zip(
            fields, ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
        ):
            for portion in field.split(","):
                pieces = portion.split("/")
                if len(pieces) > 2 or (len(pieces) == 2 and
                                         (not pieces[1].isdigit() or int(pieces[1]) < 1)):
                    return False
                base = pieces[0]
                if base == "*":
                    continue
                bounds = base.split("-")
                if not 1 <= len(bounds) <= 2 or any(not item.isdigit() for item in bounds):
                    return False
                values = [int(item) for item in bounds]
                if any(not minimum <= number <= maximum for number in values):
                    return False
                if len(values) == 2 and values[0] > values[1]:
                    return False
        return True

    def _parse_branch(self, data: dict, index: int, attributes: tuple[Attribute, ...],
                      sources: dict[str, Source]) -> Branch:
        prefix = f"unions[{index - 1}]" if index else "root"
        source_id = data["source"]
        self._require(source_id in sources, f"{prefix}.source: unknown registered source ID: {source_id}")
        source = sources[source_id]
        joins_data = data.get("joins", [])
        available: dict[str, tuple[str, ...]] = {source.alias: source.columns}
        joins = []
        for j, item in enumerate(joins_data):
            location = f"{prefix}.joins[{j}]"
            join_source_id = item["source"]
            self._require(join_source_id in sources,
                          f"{location}.source: unknown registered source ID: {join_source_id}")
            join_source = sources[join_source_id]
            alias = join_source.alias
            self._require(alias not in available, f"{location}.alias duplicated: {alias}")
            columns = join_source.columns
            conditions = []
            for k, predicate in enumerate(item["on"]):
                left = predicate["left"]
                right = predicate["right"]
                place = f"{location}.on[{k}]"
                ExpressionService.validate_value(left, available, place + ".left")
                ExpressionService.validate_value(right, {alias: columns}, place + ".right")
                conditions.append(JoinCondition(left, right))
            joins.append(Join(join_source, item["type"], tuple(conditions),
                              item.get("expect_unique", True)))
            available[alias] = columns
        filters = tuple(data.get("filters", []))
        for k, predicate in enumerate(filters):
            ExpressionService.validate_predicate(predicate, available, f"{prefix}.filters[{k}]")
        mapping = data["mapping"]
        targets = {attr.name for attr in attributes}
        self._require(set(mapping) == targets,
                      f"{prefix}.mapping must define exactly the canonical attributes: "
                      f"missing={sorted(targets - set(mapping))}, extra={sorted(set(mapping) - targets)}")
        rules = []
        for attr in attributes:
            expr = mapping[attr.name]
            ExpressionService.validate_value(expr, available, f"{prefix}.mapping.{attr.name}")
            self._require(not ("literal" in expr and expr["literal"] is None and not attr.nullable),
                          f"{prefix}.mapping.{attr.name}: required attribute cannot be a NULL literal")
            rules.append(Mapping(attr.name, expr))
        return Branch(source, tuple(joins), tuple(rules), filters, data.get("priority", 100))

    @staticmethod
    def _require(condition: bool, message: str) -> None:
        if not condition:
            raise SpecificationValidationError(message)

    @staticmethod
    def _error_message(error: Any) -> str:
        location = ".".join(str(item) for item in error.absolute_path)
        if error.validator == "required":
            missing = sorted(set(error.validator_value) - set(error.instance))
            return ", ".join(f"{location + '.' if location else ''}{key}: required" for key in missing)
        if error.validator == "additionalProperties":
            allowed = set(error.schema.get("properties", {}))
            unknown = sorted(set(error.instance) - allowed, key=str)
            return f"{location or 'root'}: unknown field(s) {', '.join(map(str, unknown))}"
        return f"{location or 'root'}: {error.message}"
