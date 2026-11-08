"""Coordinates specification, generation, and local output persistence."""

import json
from pathlib import Path
from typing import Callable

from cade.brokers.file_broker import FileBrokerContract
from cade.models.specification import Specification, DataProduct, SpecificationValidationError
from cade.services.specification_service import SpecificationService
from cade.services.sqlglot_service import SqlGlotSqlService
from cade.services.dialect_service import DialectService
from cade.services.dependency_service import DependencyService
from cade.services.traceability_service import TraceabilityService


class ProductService:
    def __init__(self, files: FileBrokerContract, specifications: SpecificationService) -> None:
        self.files = files
        self.specifications = specifications

    def init(self, path: Path) -> tuple[Path, Path]:
        """Create a complete YAML example and its pinned, versioned local schema."""
        import yaml
        from cade.services.schema_catalog_service import SchemaCatalogService
        schema_document = json.loads(self.specifications.schema_json())
        schema_path = path.with_suffix(f".schema-v{schema_document['x-schema-version']}.json")
        for candidate in (path, schema_path):
            if self.files.exists(candidate):
                raise FileExistsError(f"Already exists: {candidate}")
        template = yaml.safe_load(self.files.read_bundled_text("templates/product.yaml"))
        SchemaCatalogService.validate_schema(schema_document)
        template["schema"] = {"path": schema_path.name,
                              "version": schema_document["x-schema-version"]}
        self.files.write_text(schema_path, self.specifications.schema_json(), overwrite=False)
        from cade.services.specification_layout_service import ordered_document
        template = ordered_document(template, schema_document)
        self.files.write_text(path, yaml.safe_dump(template, sort_keys=False, allow_unicode=True),
                              overwrite=False)
        return path, schema_path

    def validate(self, path: Path) -> DataProduct:
        return self.specifications.load(path)

    def compile(
        self,
        path: Path,
        output: Path | None = None,
        dialect: str | None = None,
        on_warning: Callable[[str], None] | None = None,
    ) -> list[Path]:
        """Compile a file or all YAML files in a folder, checking collisions first.

        A YAML output is relative to that YAML file. A CLI override is relative to
        the current working directory. In directory mode the override is a common
        root; preserving input subdirectories avoids collisions between products.
        """
        specifications = self.files.find_yaml_files(path)
        directory_mode = path.is_dir()
        planned: dict[Path, tuple[Path, str]] = {}
        warnings: list[str] = []

        # Validate the one public YAML once, compile ALL of its model build units.
        # The product graph, not YAML insertion order, determines dependencies.
        for spec_path in specifications:
            try:
                bundle = self.validate(spec_path)
            except SpecificationValidationError as error:
                raise SpecificationValidationError(f"{spec_path}: {error}") from error
            for spec in bundle.models:
                trace = TraceabilityService().build(spec)
                warnings.extend(f"{spec_path} [{spec.product.name}]: traceability: {message}"
                                for message in trace['warnings'])
                destination = self.output_directory(
                    spec_path, spec, path if directory_mode else None, output, warnings)
                selected_dialect = self.output_dialect(spec_path, spec, dialect, warnings)
                artifacts = self._render(spec, destination, DialectService.create(selected_dialect))
                for artifact_path, content in artifacts.items():
                    resolved = artifact_path.resolve()
                    if resolved in planned:
                        other, _ = planned[resolved]
                        raise SpecificationValidationError(
                            f"Output collision: {other} and {spec_path} both generate {resolved}")
                    planned[resolved] = (spec_path, content)
            # A single YAML is a whole product, not just a collection of model
            # SQL files. Publish the combined DAG even in compile-only mode.
            from cade.services.project_service import Project, ProjectService
            project = Project(spec_path.parent, tuple((m.product.name,spec_path,m) for m in bundle.models))
            product_plan = ProjectService(self.files,self.specifications,self).plan(project)
            destination = self.output_directory(
                spec_path,bundle.models[0],path if directory_mode else None,output,[])
            for artifact,contents in (
                (destination / f'{bundle.name}.plan.json', json.dumps(product_plan.to_dict(),indent=2)+'\n'),
                (destination / f'{bundle.name}.plan.mmd', product_plan.mermaid())):
                resolved = artifact.resolve()
                if resolved in planned:
                    raise SpecificationValidationError(f'Output collision for combined product DAG: {artifact}')
                planned[resolved]=(spec_path,contents)

        if on_warning is not None:
            for message in warnings:
                on_warning(message)
        for artifact_path, (_, content) in planned.items():
            self.files.write_text(artifact_path, content)
        return list(planned)

    @staticmethod
    def output_directory(path: Path, spec: Specification, root: Path | None,
                         override: Path | None, warnings: list[str]) -> Path:
        """One shared YAML/CLI output precedence rule for compile and project build."""
        if override is not None:
            if spec.output is not None:
                warnings.append(
                    f"{path}: YAML output '{spec.output}' ignored; explicit --out '{override}' takes precedence")
            return override / path.parent.relative_to(root) if root is not None else override
        if spec.output is not None:
            return path.parent / spec.output
        return path.parent / "build" if root is not None else Path("build")

    @staticmethod
    def output_dialect(path: Path, spec: Specification, override: str | None,
                       warnings: list[str]) -> str:
        """One shared YAML/CLI dialect precedence rule for compile and project build."""
        if override is not None and spec.dialect is not None:
            warnings.append(
                f"{path}: YAML dialect '{spec.dialect}' ignored; "
                f"explicit --dialect '{override}' takes precedence")
        return override or spec.dialect or "spark"

    @staticmethod
    def _render(specification: Specification, output: Path, sql: SqlGlotSqlService) -> dict[Path, str]:
        """Generate exactly the artifacts named by the dependency plan, in DAG order."""
        plan = DependencyService().build(specification)
        emitters = {
            "preserve:raw": lambda: sql.generate_preservation(specification),
            "transform:canonical": lambda: sql.generate(specification),
            "quality:audit": lambda: sql.generate_audit(specification),
            "quality:checks": lambda: sql.generate_checks(specification),
            "publish:product": lambda: sql.generate_publication(specification),
        }
        artifacts: dict[Path, str] = {}
        for node in plan.nodes:
            # External retention outputs a comment-only SQL reference, not executable SQL.
            if node.artifact is not None:
                artifacts[output / node.artifact] = emitters[node.id]()
        name = specification.product.name
        artifacts[output / f"{name}.json"] = json.dumps(
            specification.definition, indent=2, ensure_ascii=False) + "\n"
        artifacts[output / f"{name}.plan.json"] = json.dumps(
            plan.to_dict(), indent=2, ensure_ascii=False) + "\n"
        artifacts[output / f"{name}.plan.mmd"] = plan.mermaid()
        trace_service = TraceabilityService()
        trace = trace_service.build(specification)
        artifacts[output / f"{name}.lineage.json"] = json.dumps(
            trace, indent=2, ensure_ascii=False) + "\n"
        artifacts[output / f"{name}.documentation.md"] = trace_service.markdown(specification, trace)
        return artifacts
