"""Execute a project dependency plan using generated SQL and one SQLAlchemy connection.

The service never recompiles, rewrites, or substitutes STTM SQL. Every executable
step comes from the existing SQLGlot-produced artifact named in the DAG.
"""

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import os
from typing import Callable

from cade.brokers.execution_broker import ExecutionBroker
from cade.services.engine_catalog_service import EngineCatalogService
from cade.models.specification import Specification, SpecificationValidationError
from cade.services.project_service import ProjectPlan
from cade.services.sql_service import SqlTemplateService


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionService:
    def __init__(self, broker: ExecutionBroker | None = None) -> None:
        self.broker = broker or ExecutionBroker()

    @staticmethod
    def connection(project_specs: dict[str, Specification]) -> tuple[str, str]:
        """All selected products execute on ONE database to preserve project atomicity."""
        env_names = set()
        for name, spec in project_specs.items():
            if spec.execution is None:
                raise SpecificationValidationError(
                    f"Product {name}: execution.connection is required for cade build")
            registry = {entry.id: entry.url_env for entry in spec.connections}
            env_name = registry[spec.execution.connection]
            for source in spec.sources:
                if source.product_ref is None and source.connection != spec.execution.connection:
                    raise SpecificationValidationError(
                        f"Product {name}: source {source.id} is not on its execution connection")
            env_names.add(env_name)
        if len(env_names) != 1:
            raise SpecificationValidationError(
                "All products in one atomic build must use the same execution connection environment variable")
        env = next(iter(env_names))
        url = os.environ.get(env)
        if not url:
            raise SpecificationValidationError(f"Missing database URL environment variable: {env}")
        return env, url

    def run(self, plan: ProjectPlan, specs: dict[str, Specification], artifact_root: Path,
            run_id: str, dialects: dict[str, str],
            on_step: Callable[[dict], None] | None = None) -> dict:
        started = perf_counter()
        from cade.services.evidence_service import specification_fingerprint
        manifest = {"format": "cade.run.v1", "run_id": run_id,
                    "specification_hashes": {name: specification_fingerprint(spec) for name, spec in specs.items()},
                    "started_at": _now(), "finished_at": None, "duration_ms": None,
                    "status": "running", "products": list(plan.products),
                    "steps": [], "tests": [], "error": None,
                    "connection_env": None, "publication": []}
        current_step = None
        # Build the run's test inventory from the same canonical IR and check IDs
        # used by SQL generation. Every unexecuted check remains explicitly not_run.
        from cade.services.traceability_service import TraceabilityService
        checks_by_product = {}
        test_results = {}
        for product_name in plan.products:
            specification = specs[product_name]
            trace = TraceabilityService().build(specification)
            check_links = {}
            for requirement in trace["requirements"]:
                for criterion in requirement["acceptance_criteria"]:
                    for check_id in criterion["check_ids"]:
                        link = check_links.setdefault(check_id, {
                            "requirement_ids": set(), "acceptance_criteria_ids": set()})
                        link["requirement_ids"].add(requirement["id"])
                        link["acceptance_criteria_ids"].add(criterion["id"])
            checks = tuple(identifier for identifier, _ in SqlTemplateService().check_statements(specification))
            checks_by_product[product_name] = checks
            for check_id in checks:
                link = check_links.get(check_id)
                result = {"product": product_name, "id": check_id,
                          "status": "not_run", "failures": None, "passed": None,
                          "requirement_ids": sorted(link["requirement_ids"]) if link else [],
                          "acceptance_criteria_ids": sorted(link["acceptance_criteria_ids"]) if link else []}
                manifest["tests"].append(result)
                test_results[(product_name, check_id)] = result
        try:
            env, url = self.connection(specs)
            manifest["connection_env"] = env  # Never save a connection URL or secret.
            parsed_dialect = {name: dialects[name] for name in plan.products}
            with self.broker.transaction(url) as conn:
                backend = conn.dialect.name
                for name, dialect in parsed_dialect.items():
                    expected = EngineCatalogService.expected_output_dialect(backend)
                    if dialect != expected:
                        raise SpecificationValidationError(
                            f"Product {name}: SQL dialect '{dialect}' does not match execution database '{backend}' "
                            f"(expected '{expected}')")
                # The DAG's order is authoritative. Logical transformation nodes
                # express dependencies but do not execute duplicate SQL.
                for node in plan.nodes:
                    if node.action == "logical" and node.kind != "product-reference":
                        continue
                    current_step = {"node": node.id, "kind": node.kind, "action": node.action,
                                    "status": "running", "duration_ms": None, "error": None}
                    manifest["steps"].append(current_step)
                    tick = perf_counter()
                    try:
                        _, name, local_id = node.id.split(":", 2)
                        spec = specs[name]
                        if local_id.startswith("source:"):
                            source_id = local_id.split(":", 1)[1]
                            source = next(s for s in spec.sources if s.id == source_id)
                            current_step["source"] = self.broker.source_ready(conn, source)
                        elif node.action in ("sql", "gate"):
                            if not node.artifact:
                                raise SpecificationValidationError(f"Missing SQL artifact for {node.id}")
                            # The plan contains paths relative to project.plan.json.
                            artifact = (artifact_root / node.artifact).resolve()
                            if not artifact.is_file():
                                raise SpecificationValidationError(f"Missing SQL artifact for {node.id}")
                            script = artifact.read_text(encoding="utf-8")
                            if node.action == "gate":
                                for check_id, failures in self.broker.check_failures(
                                        conn, script, parsed_dialect[name], checks_by_product[name]):
                                    test = test_results[(name, check_id)]
                                    test["status"] = "failed" if failures else "passed"
                                    test["failures"] = failures
                                    test["passed"] = failures == 0
                                    # A failing check is already persisted in the manifest
                                    # before any exception is raised. The broker also stops.
                                    if failures:
                                        raise SpecificationValidationError(
                                            f"Product {name}: quality gate {check_id} failed "
                                            f"({failures} failures); publication blocked")
                            else:
                                statements = self.broker.statements(script, parsed_dialect[name])
                                if local_id == "publish:product":
                                    self.broker.prepare_publication(conn, spec.product.target)
                                if local_id == "quality:audit":
                                    audits = []
                                    for statement in statements:
                                        result = conn.exec_driver_sql(statement)
                                        audits.extend(dict(zip(result.keys(), row)) for row in result.fetchall())
                                    current_step["audit"] = audits
                                else:
                                    for statement in statements:
                                        conn.exec_driver_sql(statement)
                                if local_id == "publish:product":
                                    self.broker.verify_publication(conn, spec.product.target)
                                    manifest["publication"].append(name)
                        else:
                            # e.g. externally managed preservation; not proof of retention.
                            current_step["status"] = "external"
                        if current_step["status"] == "running":
                            current_step["status"] = "passed"
                    except Exception as exc:
                        current_step["status"] = "failed"
                        current_step["error"] = (str(exc) if isinstance(exc, SpecificationValidationError)
                                                 else f"Database or SQL error ({type(exc).__name__}) at {node.id}")
                        raise
                    finally:
                        current_step["duration_ms"] = round((perf_counter() - tick) * 1000, 3)
                        if on_step:
                            on_step(current_step)
            manifest["status"] = "passed"
        except Exception as exc:
            manifest["status"] = "failed"
            manifest["error"] = (str(exc) if isinstance(exc, SpecificationValidationError)
                                 else f"Execution failed ({type(exc).__name__}) at "
                                      f"{current_step['node'] if current_step else 'connection'}")
            # Transaction rollbacks invalidate every publication from this run.
            manifest["publication"] = []
            for step in manifest["steps"]:
                if step["action"] == "sql" and step["status"] == "passed":
                    step["status"] = "rolled_back"
        finally:
            manifest["finished_at"] = _now()
            manifest["duration_ms"] = round((perf_counter() - started) * 1000, 3)
        return manifest
