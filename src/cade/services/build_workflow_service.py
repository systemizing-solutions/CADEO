"""One application-level build workflow shared by CLI and local web editor.

Compilation remains in ProjectService; database execution remains in ExecutionService.
This service only orchestrates them and safely records their common output.
"""

import json
import os
from pathlib import Path
from uuid import uuid4
from urllib.parse import quote

from sqlalchemy.engine import make_url

from cade.brokers.file_broker import FileBrokerContract
from cade.models.specification import SpecificationValidationError
from cade.services.execution_service import ExecutionService
from cade.services.product_service import ProductService
from cade.services.project_service import Project, ProjectService
from cade.services.evidence_service import EvidenceService


class BuildWorkflowService:
    def __init__(self, files: FileBrokerContract, products: ProductService,
                 projects: ProjectService, executor: ExecutionService | None = None) -> None:
        self.files = files
        self.products = products
        self.projects = projects
        self.executor = executor or ExecutionService()

    def build(self, path: Path, targets: tuple[str, ...] = (),
              output: Path | None = None, dialect: str | None = None,
              report: Path | None = None) -> dict:
        if path.is_dir():
            project = self.projects.load(path)
        else:
            bundle = self.products.validate(path)
            project = Project(path.parent, tuple((spec.product.name, path, spec)
                                                 for spec in bundle.models))
        run_id = 'b' + uuid4().hex[:12]
        warnings: list[str] = []
        plan, generated = self.projects.build(
            project, targets, output=output, dialect=dialect, build_id=run_id,
            on_warning=warnings.append)
        selected = {name: (spec_path, spec)
                    for name, spec_path, spec in project.products if name in plan.products}
        manifest_dir = (output if output is not None else project.root / 'build') / run_id
        destination = report if report is not None else manifest_dir / 'run-manifest.json'
        if destination.suffix.lower() != '.json':
            raise SpecificationValidationError('--report must name a .json file')
        forbidden = {file.resolve() for file in generated}
        forbidden.update(spec_path.resolve() for spec_path, _ in selected.values())
        if destination.resolve() in forbidden:
            raise SpecificationValidationError('--report cannot overwrite YAML or generated assets')
        if destination.exists():
            raise FileExistsError(f'Run manifest already exists: {destination}')
        for _, spec in selected.values():
            if spec.execution is None:
                continue
            reference = next(c for c in spec.connections if c.id == spec.execution.connection)
            url = os.environ.get(reference.url_env)
            if url:
                parsed = make_url(url)
                if parsed.get_backend_name() == 'sqlite' and parsed.database not in (None, '', ':memory:'):
                    if destination.resolve() == Path(parsed.database).resolve():
                        raise SpecificationValidationError('--report cannot overwrite configured database')
        specs = {name: spec for name, (_, spec) in selected.items()}
        selected_dialects = {
            name: ProductService.output_dialect(spec_path, spec, dialect, [])
            for name, (spec_path, spec) in selected.items()}
        manifest = self.executor.run(plan, specs, manifest_dir, run_id, selected_dialects)
        self.files.write_text(destination, json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
        acceptance_report = manifest_dir / 'acceptance-results.md'
        self.files.write_text(acceptance_report, '\n'.join(
            EvidenceService().markdown(EvidenceService().build(spec, manifest, str(destination)))
            for spec in specs.values()))
        product_reports: dict[Path, list[str]] = {}
        for spec_path, spec in selected.values():
            product_directory = ProductService.output_directory(spec_path, spec, project.root, output, [])
            product_report = product_directory / 'runs' / run_id / 'evidence' / 'acceptance-results.md'
            product_reports.setdefault(product_report, []).append(
                EvidenceService().markdown(EvidenceService().build(spec, manifest, str(destination))))
        for product_report, sections in product_reports.items():
            manifest_link = quote(Path(os.path.relpath(
                destination.resolve(), product_report.parent.resolve())).as_posix())
            self.files.write_text(product_report,
                                  f'Manifest: [run-manifest.json]({manifest_link})\n\n' + '\n'.join(sections))
            generated.append(product_report)
        return {
            'run_id': run_id, 'status': manifest['status'], 'products': list(plan.products),
            'manifest_path': str(destination), 'acceptance_report_path': str(acceptance_report),
            'manifest': manifest, 'warnings': warnings,
            'generated_files': [str(file) for file in generated],
        }
