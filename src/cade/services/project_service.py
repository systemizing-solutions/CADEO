"""Project-level planning for a collection of independent product specifications.

There is one source catalogue, one per-product IR/planner, and one topological
sort. The project layer only resolves references and combines existing plans.
No separate SQL engine or specification model is introduced here.
"""

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from typing import Callable

from cade.brokers.file_broker import FileBrokerContract
from cade.models.specification import Specification, SpecificationValidationError
from cade.services.dependency_service import (
    DependencyNode, DependencyPlan, DependencyService,
)
from cade.services.dialect_service import DialectService
from cade.services.product_service import ProductService
from cade.services.specification_service import SpecificationService
from cade.services.traceability_service import TraceabilityService


@dataclass(frozen=True)
class Project:
    root: Path
    # Topological order: referenced products are before their consumers.
    products: tuple[tuple[str, Path, Specification], ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _, _ in self.products)


@dataclass(frozen=True)
class ProjectPlan:
    products: tuple[str, ...]
    nodes: tuple[DependencyNode, ...]

    def to_dict(self) -> dict:
        return {
            "format": "cade.project-plan.v1",
            "products": list(self.products),
            "execution_order": [node.id for node in self.nodes],
            "nodes": [node.to_dict() for node in self.nodes],
            "steps": [node.to_dict() for node in self.nodes if node.action in ("sql", "gate")],
            "note": "The project DAG is executed by cade build using the configured SQLAlchemy connection. "
                    "cade compile only generates SQL.",
        }

    def mermaid(self) -> str:
        return DependencyPlan(self.nodes).mermaid()


class ProjectService:
    def __init__(self, files: FileBrokerContract, specifications: SpecificationService,
                 products: ProductService):
        self.files = files
        self.specifications = specifications
        self.products = products

    def load(self, root: Path) -> Project:
        if not root.is_dir():
            raise SpecificationValidationError("Project commands need a folder of YAML specifications")
        definitions: dict[str, tuple[Path, dict]] = {}
        targets: dict[str, str] = {}
        for path in self.files.find_yaml_files(root):
            document = self.specifications.read_document(path)
            name = document['definition']['name']
            if name in definitions:
                raise SpecificationValidationError(
                    f"Duplicate product identity '{name}': {definitions[name][0]} and {path}")
            for model_id, target in document['delivery']['targets'].items():
                physical = target['table']
                if physical in targets:
                    raise SpecificationValidationError(
                        f"Duplicate publication target '{physical}': {targets[physical]} and {name}.{model_id}")
                targets[physical] = f'{name}.{model_id}'
            definitions[name] = (path, document)

        product_nodes = {}
        for name, (_, document) in definitions.items():
            refs = sorted({source['product_ref'].split('.',1)[0]
                           for source in document['sources']['datasets'].values()
                           if 'product_ref' in source})
            for ref in refs:
                if ref not in definitions:
                    raise SpecificationValidationError(f"Product '{name}' references missing product '{ref}'")
            product_nodes[name] = DependencyNode(name, 'product', tuple(refs))
        ordered = DependencyService._sort(product_nodes)

        # Build top-level products first, then flatten model units into the
        # existing project-level DAG. A model uses the same generation engine.
        catalog: dict[str, Specification] = {}
        all_models: list[tuple[str, Path, Specification]] = []
        for node in ordered:
            path, document = definitions[node.id]
            try:
                bundle = self.specifications.for_file(path).parse(document, catalog)
            except SpecificationValidationError as error:
                raise SpecificationValidationError(f'{path}: {error}') from error
            for model in bundle.models:
                catalog[model.product.name] = model
                all_models.append((model.product.name, path, model))
        return Project(root, tuple(all_models))

    @staticmethod
    def _selected(project: Project, targets: tuple[str, ...]) -> tuple[tuple[str, Path, Specification], ...]:
        by_name = {name: spec for name, _, spec in project.products}
        if not targets:
            return project.products
        required = set()
        for target in targets:
            if target in by_name:
                required.add(target)
            else:
                matching = {name for name in by_name if name.startswith(target + '.')}
                if not matching:
                    # A single-product build may name just its model ID. In a
                    # multi-product project this shorthand must be unambiguous.
                    matching = {name for name in by_name if name.rsplit('.', 1)[-1] == target}
                    if len(matching) > 1:
                        raise SpecificationValidationError(
                            f"Ambiguous target model '{target}'; use product.model")
                if not matching:
                    raise SpecificationValidationError(f"Unknown target product or model '{target}'")
                required.update(matching)
        pending = list(required)
        while pending:
            name = pending.pop()
            for source in by_name[name].sources:
                if source.product_ref and source.product_ref not in required:
                    required.add(source.product_ref)
                    pending.append(source.product_ref)
        return tuple(item for item in project.products if item[0] in required)

    def plan(self, project: Project, targets: tuple[str, ...] = ()) -> ProjectPlan:
        included = self._selected(project, targets)
        nodes = {}
        for name, _, spec in included:
            local_plan = DependencyService().build(spec)
            prefix = f"product:{name}:"
            for node in local_plan.nodes:
                dependencies = [prefix + item for item in node.depends_on]
                kind = node.kind
                action = node.action
                if node.id.startswith("source:"):
                    source_id = node.id.removeprefix("source:")
                    source = next(s for s in spec.sources if s.id == source_id)
                    if source.product_ref:
                        dependencies.append(f"product:{source.product_ref}:publish:product")
                        kind = "product-reference"
                        action = "logical"
                key = prefix + node.id
                nodes[key] = DependencyNode(key, kind, tuple(dependencies),
                                            node.artifact, action)
        ordered = DependencyService._sort(nodes)
        return ProjectPlan(tuple(name for name, _, _ in included), ordered)

    def build(self, project: Project, targets: tuple[str, ...] = (),
              output: Path | None = None, dialect: str | None = None,
              on_warning: Callable[[str], None] | None = None,
              build_id: str | None = None,
              test_pack: str = "none") -> tuple[ProjectPlan, list[Path]]:
        """Render the project DAG through the single SQLGlot generator.

        A build ID selects fresh stage/snapshot names for native execution.
        SQL execution is handled by the execution service, not this renderer.
        """
        if test_pack not in ("none", "sql"):
            raise SpecificationValidationError(f"Unknown test-pack exporter: {test_pack}")
        plan = self.plan(project, targets)
        wanted = set(plan.products)
        contents: dict[Path, tuple[str, str]] = {}
        artifact_paths: dict[tuple[str, str], Path] = {}
        warnings: list[str] = []
        scripts: dict[tuple[str, str], str] = {}
        specifications: dict[str, Specification] = {}
        destinations: dict[str, Path] = {}
        for name, path, spec in project.products:
            if name not in wanted:
                continue
            specifications[name] = spec
            destination = ProductService.output_directory(
                path, spec, project.root, output, warnings)
            if build_id is not None:
                destination = destination / 'runs' / build_id
            destinations[name] = destination
            selected_dialect = ProductService.output_dialect(path, spec, dialect, warnings)
            trace = TraceabilityService().build(spec)
            warnings.extend(f"{path}: traceability: {message}" for message in trace["warnings"])
            artifacts = ProductService._render(
                spec, destination, DialectService.create(selected_dialect, build_id=build_id))
            if test_pack == "sql":
                from cade.services.test_pack_service import TestPackService
                artifacts.update(TestPackService(self.files).render(
                    spec, destination, DialectService.create(selected_dialect)))
            local_plan = DependencyService().build(spec)
            for node in local_plan.nodes:
                if node.artifact:
                    artifact_paths[(name, node.artifact)] = (destination / node.artifact).resolve()
            for node in local_plan.nodes:
                if node.action in ("sql", "gate"):
                    scripts[(name, node.id)] = artifacts[destination / node.artifact]
            for filename, text in artifacts.items():
                resolved = filename.resolve()
                if resolved in contents:
                    raise SpecificationValidationError(
                        f"Output collision: {contents[resolved][0]} and {name} both generate {filename}")
                contents[resolved] = (name, text)
        manifest_dir = output if output is not None else project.root / "build"
        if build_id is not None:
            manifest_dir = manifest_dir / build_id
        # Project artifacts contain paths relative to the project.plan.json folder,
        # so a runner needs no duplicate lookup of YAML output configuration.
        def output_node(node: DependencyNode) -> DependencyNode:
            if node.artifact is None:
                return node
            _, name, _ = node.id.split(":", 2)
            location = artifact_paths[(name, node.artifact)]
            return replace(node, artifact=os.path.relpath(location, manifest_dir.resolve()))
        plan = ProjectPlan(plan.products, tuple(output_node(n) for n in plan.nodes))
        for filename, text in (
            (manifest_dir / "project.plan.json", json.dumps(plan.to_dict(), indent=2) + "\n"),
            (manifest_dir / "project.plan.mmd", plan.mermaid()),
        ):
            resolved = filename.resolve()
            if resolved in contents:
                raise SpecificationValidationError(f"Project-plan output collision: {filename}")
            contents[resolved] = ("project", text)
        if on_warning:
            for message in warnings:
                on_warning(message)
        for filename, (_, text) in contents.items():
            self.files.write_text(filename, text)
        return plan, list(contents)
