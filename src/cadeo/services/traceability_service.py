"""Derive requirements, column lineage, checks and documentation from the same IR.

No additional source, mapping, or requirement definitions live in this service.
Only explicit links (requirement_ids and check_ids) are authored in YAML.
"""

from __future__ import annotations

from typing import Any

from cadeo.models.specification import Specification, SpecificationValidationError
from cadeo.services.sql_service import SqlTemplateService
from cadeo.services.dependency_service import DependencyService


class TraceabilityService:
    """Bidirectional traceability: warn on gaps and reject dangling IDs."""

    @staticmethod
    def _columns(tree: Any) -> tuple[str, ...]:
        """Find source columns in nested CASE, COALESCE and Boolean predicates."""
        found: set[str] = set()
        def visit(node: Any) -> None:
            if isinstance(node, dict):
                if "registry" in node:
                    from cadeo.services.function_registry_service import FunctionRegistryService
                    call = node["registry"]
                    definition = FunctionRegistryService.definition(call["name"])
                    for parameter in definition["expression_parameters"] + definition["expression_lists"]:
                        if parameter in call["args"]:
                            visit(call["args"][parameter])
                    return
                if "column" in node:
                    found.add(node["column"])
                for value in node.values():
                    visit(value)
            elif isinstance(node, (list, tuple)):
                for item in node:
                    visit(item)
        visit(tree)
        return tuple(sorted(found))

    def build(self, specification: Specification) -> dict[str, Any]:
        spec = specification
        artifact_paths = {node.id: node.artifact for node in DependencyService().build(spec).nodes
                          if node.artifact is not None}
        requirements = {r.id: r for r in spec.business.requirements}
        checks = tuple(check_id for check_id, _ in SqlTemplateService().check_statements(spec))
        checks_set = set(checks)
        test_types = {test.id: test.type for test in spec.tests}
        # Aliases are scoped to a transformation branch, not to the whole product.
        # A UNION can reuse the same alias for entirely different physical tables.
        def physical(column: str, sources_by_alias: dict[str, Any]) -> str:
            alias, name = column.split(".", 1)
            return f"{sources_by_alias[alias].table}.{name}"

        for attr in spec.canonical.attributes:
            missing = sorted(set(attr.requirement_ids) - set(requirements))
            if missing:
                raise SpecificationValidationError(
                    f"canonical.attributes.{attr.name}.requirement_ids: unknown requirements {missing}")
        for feature in spec.business.features:
            for criterion in feature.acceptance_criteria:
                missing = sorted(set(criterion.check_ids) - checks_set)
                if missing:
                    raise SpecificationValidationError(
                        f"business.features.{feature.id}.acceptance_criteria.{criterion.id}.check_ids: "
                        f"unknown generated checks {missing}. Use 'cadeo checks' to list valid IDs.")

        warnings: list[str] = []
        attr_to_reqs = {a.name: set(a.requirement_ids) for a in spec.canonical.attributes}
        linked_checks: set[str] = set()
        linked_requirements: set[str] = set()
        req_to_features: dict[str, list[str]] = {rid: [] for rid in requirements}
        req_to_acceptance: dict[str, list[dict[str, Any]]] = {rid: [] for rid in requirements}
        for feature in spec.business.features:
            for rid in feature.requirement_ids:
                req_to_features[rid].append(feature.id)
                for criterion in feature.acceptance_criteria:
                    item = {"id": criterion.id, "feature_id": feature.id,
                            "statement": criterion.statement, "check_ids": list(criterion.check_ids)}
                    req_to_acceptance[rid].append(item)
                    linked_checks.update(criterion.check_ids)
                    if criterion.check_ids:
                        linked_requirements.add(rid)
                    else:
                        warnings.append(
                            f"[spec->tests] acceptance criterion {criterion.id} has no check_ids")
        for attr in spec.canonical.attributes:
            if not attr.requirement_ids:
                warnings.append(
                    f"[model->spec] canonical attribute {attr.name} has no requirement_ids")
        for req_id in requirements:
            if req_id not in linked_requirements and not any(
                req_id in ids for ids in attr_to_reqs.values()
            ):
                warnings.append(
                    f"[spec->model] requirement {req_id} has no canonical attributes or acceptance checks")
            elif req_id not in linked_requirements:
                warnings.append(
                    f"[spec->tests] requirement {req_id} has no linked acceptance checks")
        for check_id in checks:
            if check_id not in linked_checks:
                warnings.append(f"[tests->spec] generated check {check_id} has no acceptance criterion")

        fields: list[dict[str, Any]] = []
        mapped_source_columns: set[str] = set()
        for attr in spec.canonical.attributes:
            branch_rules: list[dict[str, Any]] = []
            for branch_number, branch in enumerate(spec.branches):
                sources_by_alias = {branch.source.alias: branch.source}
                sources_by_alias.update({join.alias: join.source for join in branch.joins})
                mapping = next((m for m in branch.mappings if m.target == attr.name), None)
                if mapping is None:  # Current semantic validator already rejects missing mappings.
                    warnings.append(
                        f"[model->sttm] canonical attribute {attr.name} is missing branch {branch_number} mapping")
                    continue
                references = self._columns(mapping.expression)
                physical_references = [physical(column, sources_by_alias) for column in references]
                mapped_source_columns.update(physical_references)
                context = set()
                for join in branch.joins:
                    for condition in join.on:
                        context.update(self._columns(condition.left))
                        context.update(self._columns(condition.right))
                for predicate in branch.filters:
                    context.update(self._columns(predicate))
                branch_rules.append({
                    "id": f"mapping:branch:{branch_number}:{attr.name}",
                    "branch": branch_number,
                    "source": branch.source.id,
                    "joined_sources": [join.source.id for join in branch.joins],
                    "expression": mapping.expression,
                    "source_columns": list(references),
                    "physical_source_columns": physical_references,
                    "context_columns": sorted(context),
                    "physical_context_columns": [physical(column, sources_by_alias)
                                                 for column in sorted(context)],
                    "derivation": "source" if references else "constant",
                })
            fields.append({
                "attribute": attr.name,
                "type": attr.type,
                "nullable": attr.nullable,
                "requirement_ids": list(attr.requirement_ids),
                "rules": branch_rules,
                "assets": [artifact_paths['transform:canonical'], artifact_paths['publish:product']],
            })

        # A deliberately unmapped source attribute is still kept by raw preservation;
        # it is visible in the report but is not an error or automatic warning.
        all_source_columns = sorted(
            f"{source.table}.{name}" for source in spec.sources for name in source.columns
        )
        unmapped = sorted(set(all_source_columns) - mapped_source_columns)

        requirements_report: list[dict[str, Any]] = []
        for req_id, requirement in requirements.items():
            assigned = sorted(name for name, ids in attr_to_reqs.items() if req_id in ids)
            ac = req_to_acceptance[req_id]
            test_ids = sorted({key for criterion in ac for key in criterion["check_ids"]})
            assets: set[str] = set()
            if assigned:
                assets.update((artifact_paths['transform:canonical'], artifact_paths['publish:product']))
            if test_ids:
                assets.add(artifact_paths['quality:checks'])
                if "reconciliation" in test_ids:
                    assets.add(artifact_paths['quality:audit'])
            requirements_report.append({
                "id": req_id,
                "statement": requirement.statement,
                "feature_ids": sorted(set(req_to_features[req_id])),
                "attributes": assigned,
                "acceptance_criteria": ac,
                "check_ids": test_ids,
                "assets": sorted(assets),
            })

        return {
            "format": "cadeo.traceability.v1",
            "product": spec.product.name,
            "spec_version": spec.spec_version,
            "target": spec.product.target,
            "fields": fields,
            "requirements": requirements_report,
            "quality_checks": [
                {"id": check_id, "type": test_types.get(check_id, "generated"),
                 "asset": artifact_paths['quality:checks'],
                 "result": "not_run", "runtime_result": "run-manifest.json (in the build run directory or --report path)"}
                for check_id in checks
            ],
            "unmapped_source_columns": unmapped,
            "warnings": warnings,
        }

    def runtime_markdown(self, specs: dict[str, Specification], manifest: dict, manifest_path: str) -> str:
        """One run-time acceptance report from the canonical IR and actual run manifest.

        A check that did not execute has status not_run, never an assumed pass.
        This is a presentation of the manifest, not a second results store.
        """
        results = {(item["product"], item["id"]): item for item in manifest.get("tests", [])}
        lines = [f"# Build acceptance results — {manifest.get('run_id', 'unknown')}", "",
                 f"**Build:** {manifest.get('status', 'unknown')}  ",
                 f"**Run manifest:** `{manifest_path}`", "",
                 "| Product | Requirement | Acceptance criterion | Test ID | Type | Result | Failures |",
                 "|---|---|---|---|---|---|---|"]
        for product_name in manifest.get("products", list(specs)):
            trace = self.build(specs[product_name])
            types = {item["id"]: item["type"] for item in trace["quality_checks"]}
            for requirement in trace["requirements"]:
                for criterion in requirement["acceptance_criteria"]:
                    for check_id in criterion["check_ids"]:
                        result = results.get((product_name, check_id))
                        status = (result.get("status") or ("passed" if result["passed"] else "failed")) if result else "not_run"
                        count = str(result["failures"]) if result and result["failures"] is not None else "—"
                        values = (product_name, requirement["id"], criterion["id"],
                                  check_id, types[check_id], status, count)
                        lines.append("| " + " | ".join(str(v).replace("|", "\\|") for v in values) + " |")
        lines += ["", "Tests that did not execute are **not_run** and have no result. "
                  "For transaction rollback and step failures, inspect the run manifest.", ""]
        return "\n".join(lines)

    def markdown(self, spec: Specification, report: dict[str, Any]) -> str:
        """Generate readable product documentation without a second data model."""
        def cell(value: Any) -> str:
            return str(value).replace("|", "\\|").replace("\n", " ")

        lines = [f"# {spec.product.name}", "",
                 f"**Target:** `{spec.product.target}`  ",
                 f"**Canonical entity:** {spec.canonical.entity}  ",
                 f"**Grain:** {spec.canonical.grain}  ",
                 f"**Owner:** {spec.product.owner.team} ({spec.product.owner.contact})", "",
                 "## Requirements and acceptance coverage", "",
                 "| Requirement | Canonical fields | Acceptance checks | Generated assets |",
                 "|---|---|---|---|"]
        for req in report["requirements"]:
            lines.append("| " + " | ".join(cell(value) for value in (
                req["id"], ", ".join(req["attributes"]) or "—",
                ", ".join(req["check_ids"]) or "—", ", ".join(req["assets"]) or "—")) + " |")
        lines += ["", "## Canonical attribute lineage", "",
                  "| Attribute | Requirement IDs | STTM rules and physical source columns |",
                  "|---|---|---|"]
        for field in report["fields"]:
            rules = "; ".join(
                f"{r['id']} ← " + (
                    ", ".join(f"{alias} → {table}" for alias, table in zip(
                        r["source_columns"], r["physical_source_columns"]))
                    or "(constant)")
                for r in field["rules"])
            lines.append("| " + " | ".join(cell(x) for x in (
                field["attribute"], ", ".join(field["requirement_ids"]) or "—", rules)) + " |")
        lines += ["", "## Acceptance criteria", ""]
        for feature in spec.business.features:
            lines.append(f"### {feature.id} — {feature.description}")
            for criterion in feature.acceptance_criteria:
                lines.append(f"- **{criterion.id}:** {criterion.statement} "
                             f"(checks: {', '.join(criterion.check_ids) or 'not linked'})")
        lines += ["", "## Quality checks", "",
                  "The compiler generates test SQL. Results appear in the build run manifest "
                  "(`cadeo build`) or a `cadeo verify --report` report. "
                  "Unexecuted tests are `not_run`.", ""]
        for check in report["quality_checks"]:
            lines.append(f"- `{check['id']}` ({check['type']}) — {check['result']}")
        lines += ["", "## Unmapped physical source columns", "",
                  "These fields do not directly produce canonical attributes. "
                  "Their preservation follows the configured raw-preservation contract.", "",
                  ", ".join(f"`{x}`" for x in report["unmapped_source_columns"]) or "None", "",
                  "## Traceability warnings", ""]
        lines += [f"- {message}" for message in report["warnings"]] or ["None"]
        return "\n".join(lines) + "\n"
