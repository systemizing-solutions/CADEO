"""Local, schema-driven Flask editor. HTTP adapts requests to existing services.

No SQL, data access, dependency planning, or domain validation is implemented here.
The browser only receives the public JSON Schema and contract documents; SQL
execution and connection credentials remain on the server in the shared services.
"""

from __future__ import annotations

from hashlib import sha256
from html import escape
import json
from pathlib import Path
import secrets
from typing import Any
from urllib.parse import urlparse

import yaml
from sqlalchemy.exc import SQLAlchemyError

from cadeo.brokers.file_broker import FileBroker
from cadeo.models.specification import SpecificationValidationError
from cadeo.services.build_workflow_service import BuildWorkflowService
from cadeo.services.product_service import ProductService
from cadeo.services.project_service import ProjectService
from cadeo.services.specification_service import SpecificationService, UniqueKeysLoader
from cadeo.services.schema_catalog_service import SchemaCatalogService


class WorkspaceError(ValueError):
    """A requested path is invalid or escapes the selected local workspace."""


def create_app(workspace: Path, *, overwrite: bool = False):
    """Create a local Flask app bound by the CLI to the loopback interface only."""
    from flask import Flask, Response, abort, jsonify, request
    from werkzeug.exceptions import HTTPException

    root = Path(workspace).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise WorkspaceError(f'Not a directory: {root}')
    files = FileBroker()
    schemas = SchemaCatalogService(root)
    schemas.ensure_builtin(files, overwrite=overwrite)
    from cadeo.services.driver_installation_service import DriverInstallationService
    installations = DriverInstallationService()

    def selected_services(choice: str | None = None):
        # Saved documents always resolve their OWN pinned schema from the YAML.
        # Only unsaved editor drafts pass an explicit, already-resolved choice.
        specifications = SpecificationService(files, schemas.load(choice, files) if choice else None)
        products = ProductService(files, specifications)
        projects = ProjectService(files, specifications, products)
        return specifications, products, projects
    csrf_token = secrets.token_urlsafe(32)
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
    app.config['JSON_SORT_KEYS'] = False
    # Not a network service. No auth or external origins are supported.
    app.config['TRUSTED_HOSTS'] = ['localhost', '127.0.0.1', '[::1]']

    def inside(path: Path) -> Path:
        result = path.resolve()
        if not result.is_relative_to(root):
            raise WorkspaceError('Requested path is outside the editor workspace')
        return result

    def workspace_path(value: Any, *, yaml_only: bool = False) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise WorkspaceError('A workspace-relative path is required')
        if Path(value).is_absolute() or '\\' in value:
            raise WorkspaceError('Only workspace-relative forward-slash paths are accepted')
        result = inside(root / value)
        if yaml_only and result.suffix.lower() not in ('.yaml', '.yml'):
            raise WorkspaceError('Specification files must end in .yaml or .yml')
        return result

    def relative(path: Path) -> str:
        return inside(path).relative_to(root).as_posix()

    def input_json() -> dict:
        if not request.is_json:
            raise WorkspaceError('JSON request body required')
        value = request.get_json()
        if not isinstance(value, dict):
            raise WorkspaceError('Expected a JSON object')
        return value

    def items(path: Path, products: ProductService, projects: ProjectService):
        preflight_schema_refs(path)
        if path.is_dir():
            return ((spec_path, spec) for _, spec_path, spec in projects.load(path).products)
        return ((path, spec) for spec in products.validate(path).models)

    def preflight_schema_refs(path: Path) -> None:
        """Do not let UI actions resolve schemas outside their workspace."""
        for yaml_path in files.find_yaml_files(path):
            yaml_path = inside(yaml_path)
            doc = yaml.load(files.read_text(yaml_path), Loader=UniqueKeysLoader)
            schemas.from_document(yaml_path, doc, files)

    def preflight_outputs(path: Path, override: Path | None,
                          products: ProductService, projects: ProjectService) -> None:
        """Prevent YAML output paths from writing outside the chosen workspace."""
        from cadeo.models.specification import ArtifactPaths
        from cadeo.services.dependency_service import DependencyService
        project_root = path if path.is_dir() else None
        for spec_path, spec in items(path, products, projects):
            destination = ProductService.output_directory(
                spec_path, spec, project_root, override, [])
            inside(destination)
            layout = spec.artifact_paths or ArtifactPaths()
            for node in DependencyService().build(spec).nodes:
                if node.artifact:
                    inside(destination / node.artifact)
            for role in ('tests', 'plans', 'metadata', 'lineage', 'documentation'):
                inside(destination / layout.directory(role))
            shared_root = override if override is not None else (path if path.is_dir() else path.parent) / 'build'
            inside(shared_root / layout.project_plans)
        if override is not None:
            inside(override)

    def optional_output(data: dict) -> Path | None:
        value = data.get('out')
        if value in ('', None):
            return None
        return workspace_path(value)

    from cadeo.services.tutorial_service import TutorialService
    tutorial = TutorialService()
    tutorial_root = inside(root if (root / 'tutorial.sqlite').is_file() else root / 'tutorial')
    if (tutorial_root / 'tutorial.sqlite').is_file():
        inside(tutorial_root / 'tutorial.sqlite')
        tutorial.activate(tutorial_root)

    @app.after_request
    def headers(response):
        if request.path == '/api/check-registry' and response.status_code == 422:
            payload = response.get_json(silent=True) or {}
            data = request.get_json(silent=True) or {}
            if not isinstance(data, dict):
                data = {}
            details = '; '.join(f"{issue.get('path', '')}: {issue.get('message', '')}"
                                for issue in payload.get('issues', []))
            app.logger.warning('Check registry unavailable for %s: %s%s',
                               data.get('path') or 'product.yaml', payload.get('error', 'Invalid draft'),
                               f'; {details}' if details else '')
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'")
        return response

    @app.before_request
    def local_writes_only():
        if request.method not in ('POST', 'PUT', 'DELETE', 'PATCH'):
            return None
        origin = request.headers.get('Origin')
        if origin:
            parsed = urlparse(origin)
            if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
                abort(403)
        if request.headers.get('X-Editor-Token') != csrf_token:
            abort(403)
        return None

    @app.errorhandler(Exception)
    def handle_error(error):
        if isinstance(error, HTTPException):
            return jsonify(error=error.description), error.code
        if isinstance(error, (WorkspaceError, FileNotFoundError, FileExistsError, PermissionError,
                              SpecificationValidationError, yaml.YAMLError, ValueError)):
            code = 409 if isinstance(error, FileExistsError) else 422
            return jsonify(error=str(error)), code
        if isinstance(error, SQLAlchemyError):
            # Driver messages may contain credentials or user data.
            return jsonify(error=f'Database operation failed ({type(error).__name__})'), 422
        app.logger.exception('Local editor action failed')
        return jsonify(error=f'Internal editor error ({type(error).__name__})'), 500

    @app.get('/')
    def home():
        return Response(files.read_bundled_text('web/index.html'), mimetype='text/html')

    @app.get('/assets/code-preview.js')
    def code_preview_javascript():
        return Response(files.read_bundled_text('web/code-preview.js'), mimetype='text/javascript')

    @app.get('/assets/lineage-graph.js')
    def graph_javascript():
        return Response(files.read_bundled_text('web/lineage-graph.js'), mimetype='application/javascript')

    @app.get('/assets/editor.js')
    def javascript():
        return Response(files.read_bundled_text('web/editor.js'), mimetype='text/javascript')

    @app.get('/assets/editor.css')
    def stylesheet():
        return Response(files.read_bundled_text('web/editor.css'), mimetype='text/css')

    @app.get('/api/bootstrap')
    def bootstrap():
        return jsonify(token=csrf_token, workspace=str(root), version='0.8.5')

    @app.get('/tutorial')
    def tutorial_page():
        return Response(files.read_bundled_text('web/tutorial.html'), mimetype='text/html')

    @app.get('/assets/tutorial.js')
    def tutorial_javascript():
        return Response(files.read_bundled_text('web/tutorial.js'), mimetype='text/javascript')

    @app.get('/assets/tutorial.css')
    def tutorial_stylesheet():
        return Response(files.read_bundled_text('web/tutorial.css'), mimetype='text/css')

    @app.get('/tutorial/guide')
    def tutorial_guide():
        guide = escape(files.read_bundled_text('templates/tutorial.md'))
        return Response('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>CADEO Tutorial Guide</title><link rel="stylesheet" href="/assets/editor.css">'
            '<link rel="stylesheet" href="/assets/tutorial.css"><main class="tutorial-guide">'
            '<a href="/tutorial">Back to Tutorial</a><pre>' + guide + '</pre></main></html>', mimetype='text/html')

    @app.get('/api/tutorial')
    def tutorial_state():
        for resource in tutorial.RESOURCES:
            inside(tutorial_root / resource)
        return jsonify(ready=tutorial.ready(tutorial_root),
                       path=relative(tutorial_root), cases=tutorial.CASES)

    @app.post('/api/tutorial')
    def tutorial_action():
        data = input_json()
        action_name = data.get('action')
        if action_name == 'init':
            for resource in tutorial.RESOURCES:
                inside(tutorial_root / resource)
            result = tutorial.create(inside(tutorial_root))
            return jsonify(path=relative(tutorial_root), environment=result['environment'])
        inside(tutorial_root / 'tutorial.sqlite')
        if action_name == 'report':
            return jsonify(tutorial.report(inside(tutorial_root)))
        if action_name == 'exercise':
            if data.get('confirm') is not True:
                raise WorkspaceError('Explicit confirmation is required to execute a tutorial exercise')
            if installations.is_installing():
                raise WorkspaceError('Driver installation is running; retry when it finishes')
            case = data.get('case')
            if case not in tutorial.CASES:
                raise WorkspaceError('Choose a listed tutorial exercise')
            path = inside(tutorial_root / case / 'product.yaml')
            preflight_schema_refs(path)
            _, products, projects = selected_services()
            preflight_outputs(path, None, products, projects)
            return jsonify(tutorial.exercise(inside(tutorial_root), case))
        if action_name == 'review':
            from datetime import datetime, timezone
            reviewer, decisions = data.get('reviewer'), data.get('decisions')
            if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(decisions, str) or not decisions.strip():
                raise WorkspaceError('Enter the reviewer name and the agreed decisions')
            if len(reviewer) > 200 or len(decisions) > 4000:
                raise WorkspaceError('Keep the name under 200 characters and decisions under 4000 characters')
            tutorial.activate(inside(tutorial_root))
            path = inside(tutorial_root / 'owner-review.json')
            files.write_text(path, json.dumps({'reviewer': reviewer.strip(), 'decisions': decisions.strip(),
                'recorded_at': datetime.now(timezone.utc).isoformat(),
                'note': 'User-recorded discussion. This is not automatic owner approval.'}, indent=2) + '\n')
            return jsonify(path=relative(path))
        raise WorkspaceError('Unknown tutorial action')

    @app.get('/api/schemas')
    def available_schemas():
        return jsonify(schemas=schemas.list())

    @app.get('/api/schema')
    def schema():
        selected = request.args.get('path', SchemaCatalogService.DEFAULT)
        return Response(schemas.load(selected, files), mimetype='application/schema+json')

    @app.put('/api/schema-file')
    def import_schema():
        data = input_json()
        selected = schemas.save(data.get('path'), data.get('document'), files)
        return jsonify(path=selected)

    @app.get('/api/dialects')
    def dialects():
        from cadeo.services.dialect_service import DialectService
        return jsonify(dialects=DialectService.available())

    @app.get('/api/engines')
    def engines():
        from cadeo.services.engine_catalog_service import EngineCatalogService
        return jsonify(engines=EngineCatalogService().available())

    @app.get('/api/engines/installation')
    def driver_install_status():
        return jsonify(job=installations.current(),
                       interpreter=installations.interpreter(),
                       virtual_environment=installations.virtual_environment())

    @app.post('/api/engines/install')
    def install_driver():
        data = input_json()
        return jsonify(installations.start(data.get('engine_id'),
                                           confirmed=data.get('confirm') is True)), 202

    @app.get('/api/files')
    def list_files():
        paths = sorted(relative(p) for p in root.rglob('*')
                       if p.is_file() and p.suffix.lower() in ('.yaml', '.yml')
                       and p.resolve().is_relative_to(root))
        return jsonify(files=paths)

    @app.get('/api/template')
    def template():
        text = files.read_bundled_text('templates/product.yaml')
        document = yaml.load(text, Loader=UniqueKeysLoader)
        name = request.args.get('path') or 'product.yaml'
        path = workspace_path(name, yaml_only=True)
        document = schemas.to_document(path, schemas.DEFAULT, document, files)
        return jsonify(document=document, schema=schemas.DEFAULT)

    @app.get('/api/document')
    def get_document():
        path = workspace_path(request.args.get('path'), yaml_only=True)
        text = files.read_text(path)
        document = yaml.load(text, Loader=UniqueKeysLoader)
        if not isinstance(document, dict):
            raise WorkspaceError('YAML must contain an object at its root')
        choice = schemas.from_document(path, document, files)
        return jsonify(document=document, yaml=text, path=relative(path), schema=choice,
                       etag=sha256(text.encode('utf-8')).hexdigest())

    @app.put('/api/document')
    def save_document():
        data = input_json()
        path = workspace_path(data.get('path'), yaml_only=True)
        document = data.get('document')
        if not isinstance(document, dict):
            raise WorkspaceError('Document must be an object')
        expected = data.get('etag')
        if path.exists():
            if not isinstance(expected, str) or sha256(files.read_text(path).encode()).hexdigest() != expected:
                raise FileExistsError('Document was changed on disk; reload before overwriting')
        elif expected not in (None, ''):
            raise FileExistsError('Document no longer exists at the expected path')
        selected = data.get('schema', schemas.DEFAULT)
        # The explicit UI selection is written into the document; after saving,
        # the compiler resolves this field, not the UI setting or any YAML comment.
        document = schemas.to_document(path, selected, document, files)
        raw = data.get('raw_yaml')
        if raw is not None:
            if not isinstance(raw, str) or yaml.load(raw, Loader=UniqueKeysLoader) != document:
                raise WorkspaceError('Raw YAML no longer matches the edited specification; review before reformatting')
            text = raw
        else:
            from cadeo.services.specification_layout_service import ordered_document
            shape = json.loads(schemas.load(selected, files))
            document = ordered_document(document, shape)
            text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
        files.write_text(path, text, overwrite=path.exists())
        return jsonify(path=relative(path), yaml=text, document=document,
                       etag=sha256(text.encode()).hexdigest())

    @app.post('/api/yaml/import')
    def import_yaml():
        data = input_json()
        text = data.get('yaml')
        if not isinstance(text, str):
            raise WorkspaceError('YAML input must be a string')
        document = yaml.load(text, Loader=UniqueKeysLoader)
        if not isinstance(document, dict):
            raise WorkspaceError('YAML must contain an object at its root')
        path = workspace_path(data.get('path') or 'product.yaml', yaml_only=True)
        selected = schemas.from_document(path, document, files)
        return jsonify(document=document, schema=selected)

    @app.post('/api/yaml/export')
    def export_yaml():
        data = input_json()
        document = data.get('document')
        if not isinstance(document, dict):
            raise WorkspaceError('Document must be an object')
        selected = data.get('schema', schemas.DEFAULT)
        path = workspace_path(data.get('path') or 'product.yaml', yaml_only=True)
        document = schemas.to_document(path, selected, document, files)
        raw = data.get('raw_yaml')
        if raw is not None:
            if not isinstance(raw, str) or yaml.load(raw, Loader=UniqueKeysLoader) != document:
                raise WorkspaceError('Raw YAML no longer matches the edited specification; review before reformatting')
            content = raw
        else:
            from cadeo.services.specification_layout_service import ordered_document
            shape = json.loads(schemas.load(selected, files))
            document = ordered_document(document, shape)
            content = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
        return jsonify(yaml=content, document=document)

    @app.post('/api/validate')
    def validate_document():
        """Validate current unsaved document or the saved entire project.

        Both use SpecificationService's exact schema and semantic checks.
        Unsaved contents are never replaced by the disk copy.
        """
        from datetime import datetime, timezone
        from cadeo.services.traceability_service import TraceabilityService
        data = input_json()
        scope = data.get('scope', 'file')
        if scope not in ('file', 'project'):
            raise WorkspaceError('Validation scope must be file or project')
        now = datetime.now(timezone.utc).isoformat()
        if scope == 'project':
            preflight_schema_refs(root)
            _, products, projects = selected_services()
            results = []
            # Validate independently; one bad file must not hide other errors.
            for path in files.find_yaml_files(root):
                try:
                    bundle = products.validate(path)
                    warnings = [f'{spec.product.name}: {w}' for spec in bundle.models
                                for w in TraceabilityService().build(spec)['warnings']]
                    results.append({'path': relative(path), 'valid': True,
                                    'issues': [], 'warnings': warnings})
                except (SpecificationValidationError, ValueError) as error:
                    results.append({'path': relative(path), 'valid': False,
                                    'issues': [{'path': '$', 'message': str(error), 'severity': 'error',
                                                'suggestion': 'Open the specification and correct the indicated field.'}], 'warnings': []})
            # Include duplicate identities and product cycles from shared project planner.
            if all(r['valid'] for r in results):
                try:
                    projects.load(root)
                except SpecificationValidationError as error:
                    results.append({'path': '.', 'valid': False, 'issues': [
                        {'path': '$', 'message': str(error), 'severity': 'error',
                         'suggestion': 'Resolve duplicate product IDs, missing product references or cycles.'}], 'warnings': []})
            return jsonify(scope='project', started_at=now, valid=bool(results) and all(r['valid'] for r in results),
                           results=results, status='completed')
        document = data.get('document')
        if not isinstance(document, dict):
            raise WorkspaceError('Document must be an object')
        path = workspace_path(data.get('path') or 'product.yaml', yaml_only=True)
        choice = schemas.from_document(path, document, files)
        if data.get('schema') not in (None, choice):
            raise WorkspaceError('Selected JSON Schema does not match schema.path in the YAML')
        specifications, products, projects = selected_services(choice)
        catalog = {}
        if any('product_ref' in value for value in document.get('sources', {}).get('datasets', {}).values()
               if isinstance(value, dict)):
            preflight_schema_refs(root)
            try:
                catalog = {name: spec for name, _, spec in projects.load(root).products
                           if name != document.get('definition', {}).get('name')}
            except SpecificationValidationError:
                # Still report the precise draft issues before project-level errors.
                pass
        findings = specifications.findings(document, catalog)
        warnings = []
        if not findings:
            spec = specifications.parse(document, catalog)
            warnings = [f'{model.product.name}: {w}' for model in spec.models
                        for w in TraceabilityService().build(model)['warnings']]
        return jsonify(scope='file', started_at=now, status='completed', valid=not findings,
                       product=document.get('definition', {}).get('name'),
                       issues=findings, warnings=warnings)

    @app.post('/api/yaml/diagnostics')
    def yaml_diagnostics():
        """Diagnose the UNSAVED YAML through the CLI's validation services.

        Results carry source positions, but never save, normalize or replace text.
        """
        from cadeo.services.editor_diagnostics_service import syntax_findings, positioned_findings
        from cadeo.services.traceability_service import TraceabilityService
        data = input_json()
        raw = data.get('yaml')
        if not isinstance(raw, str):
            raise WorkspaceError('YAML diagnostics require a text document')
        path = workspace_path(data.get('path') or 'product.yaml', yaml_only=True)
        document, issues = syntax_findings(raw)
        if issues:
            return jsonify(valid=False, issues=issues, warnings=[])
        try:
            choice = schemas.from_document(path, document, files)
            if data.get('schema') not in (None, choice):
                raise WorkspaceError('Selected schema differs from YAML schema.path; update the reference')
            specifications, _, projects = selected_services(choice)
            catalog = {}
            if any(isinstance(value, dict) and 'product_ref' in value
                   for value in document.get('sources', {}).get('datasets', {}).values()):
                preflight_schema_refs(root)
                try:
                    catalog = {name: spec for name, _, spec in projects.load(root).products
                               if name != document.get('definition', {}).get('name')}
                except SpecificationValidationError:
                    pass
            findings = specifications.findings(document, catalog)
            warnings = [] if findings else TraceabilityService().build(
                specifications.parse(document, catalog))['warnings']
        except (WorkspaceError, SpecificationValidationError, ValueError, FileNotFoundError) as error:
            findings = [{'path': 'schema', 'severity': 'error', 'message': str(error),
                         'suggestion': 'Use an existing version-pinned schema in this workspace.'}]
            warnings = []
        issues = positioned_findings(raw, findings)
        return jsonify(valid=not issues, issues=issues, warnings=warnings)

    @app.post('/api/graph')
    def visual_graph():
        """Read-only views of the same IR, traceability and dependency plan as CLI.

        A file graph uses the unsaved document; project planning reads saved YAML.
        Neither path writes files, generates SQL or executes database statements.
        """
        from cadeo.services.visual_graph_service import VisualGraphService
        from cadeo.services.dependency_service import DependencyService
        data = input_json()
        mode = data.get('mode', 'lineage')
        scope = data.get('scope', 'file')
        if mode not in ('lineage', 'dependencies', 'erd') or scope not in ('file', 'project'):
            raise WorkspaceError('Graph mode must be erd, lineage or dependencies; scope must be file or project')
        if scope == 'project':
            if mode not in ('dependencies', 'erd'):
                raise WorkspaceError('Project scope is available for ERD and dependency graphs only')
            preflight_schema_refs(root)
            _, _, projects = selected_services()
            project = projects.load(root)
            if mode == 'erd':
                return jsonify(VisualGraphService.erd([spec for _, _, spec in project.products], scope='project'))
            return jsonify(VisualGraphService.dependencies(projects.plan(project)))
        document = data.get('document')
        if not isinstance(document, dict):
            raise WorkspaceError('Graph requires the current specification document')
        path = workspace_path(data.get('path') or 'product.yaml', yaml_only=True)
        choice = schemas.from_document(path, document, files)
        if data.get('schema') not in (None, choice):
            raise WorkspaceError('Selected JSON Schema differs from the one pinned in the YAML')
        specifications, _, projects = selected_services(choice)
        catalog = {}
        if any(isinstance(item, dict) and 'product_ref' in item
               for item in document.get('sources', {}).get('datasets', {}).values()):
            preflight_schema_refs(root)
            catalog = {name: spec for name, _, spec in projects.load(root).products
                       if name != document.get('definition', {}).get('name')}
        findings = specifications.findings(document, catalog)
        if findings:
            return jsonify(error='Validate the specification before viewing its graph',
                           issues=findings), 422
        spec = specifications.parse(document, catalog)
        if mode == 'erd':
            return jsonify(VisualGraphService.erd(spec.models))
        if mode == 'lineage':
            return jsonify(VisualGraphService().multi_lineage(spec.models, spec.name))
        from cadeo.services.project_service import Project
        temporary = Project(path.parent, tuple((model.product.name, path, model) for model in spec.models))
        return jsonify(VisualGraphService.dependencies(projects.plan(temporary)))

    @app.post('/api/check-registry')
    def check_registry():
        """Resolve selectable IDs using the exact compiler quality-check registry.

        Operates on an unsaved draft; never reads a stale saved copy of it.
        It neither generates files nor executes SQL. Invalid drafts must not
        produce a misleading list of selectable IDs.
        """
        from cadeo.services.sql_service import SqlTemplateService

        data = input_json()
        document = data.get('document')
        if not isinstance(document, dict):
            raise WorkspaceError('Check registry requires the current product document')
        path = workspace_path(data.get('path') or 'product.yaml', yaml_only=True)
        choice = schemas.from_document(path, document, files)
        if data.get('schema') not in (None, choice):
            raise WorkspaceError('Selected JSON Schema differs from the schema pinned in YAML')
        specifications, _, projects = selected_services(choice)
        catalog = {}
        if any(isinstance(source, dict) and 'product_ref' in source
               for source in document.get('sources', {}).get('datasets', {}).values()):
            preflight_schema_refs(root)
            catalog = {name: spec for name, _, spec in projects.load(root).products
                       if name != document.get('definition', {}).get('name')}
        specifications.validate_structure(document)
        # Check links are precisely what the user is editing. Temporarily
        # exclude those *references* while discovering candidate IDs, so a
        # stale check_id does not prevent the user from selecting its replacement.
        # All other schema/semantic rules still run through SpecificationService;
        # the original YAML remains untouched and normal validation/build still
        # reject any unknown reference.
        from copy import deepcopy
        candidate_document = deepcopy(document)
        current_ids = set()
        for feature in candidate_document.get('definition', {}).get('features', []):
            if not isinstance(feature, dict):
                continue
            for criterion in feature.get('acceptance_criteria', []):
                if isinstance(criterion, dict):
                    current_ids.update(criterion.get('check_ids', []))
                    criterion['check_ids'] = []
        findings = specifications.findings(candidate_document, catalog)
        if findings:
            return jsonify(error='Complete or correct the specification before selecting checks',
                           issues=findings), 422
        spec = specifications.parse(candidate_document, catalog)
        checks = []
        for model in spec.models:
            model_id = model.product.name[len(spec.name)+1:]
            types = {test.id: test.type for test in model.tests}
            checks.extend({'id': model_id + '/' + check_id,
                           'type': types.get(check_id,'generated'), 'model': model_id}
                          for check_id, _ in SqlTemplateService().check_statements(model))
        return jsonify(product=spec.name, checks=checks,
                       unknown_references=sorted(current_ids - {item['id'] for item in checks}))

    @app.post('/api/evidence')
    def evidence():
        """Project check evidence for the current unsaved document, without SQL execution.

        Run manifests are read only from the editor workspace; a run for a
        different product or old specification is never treated as a pass.
        """
        from cadeo.services.evidence_service import EvidenceService
        data = input_json()
        document = data.get('document')
        if not isinstance(document, dict):
            raise WorkspaceError('Evidence requires the current product document')
        path = workspace_path(data.get('path') or 'product.yaml', yaml_only=True)
        choice = schemas.from_document(path, document, files)
        if data.get('schema') not in (None, choice):
            raise WorkspaceError('Selected schema differs from the schema pinned in YAML')
        specifications, _, projects = selected_services(choice)
        catalog = {}
        if any(isinstance(item, dict) and 'product_ref' in item
               for item in document.get('sources', {}).get('datasets', {}).values()):
            preflight_schema_refs(root)
            catalog = {name: spec for name, _, spec in projects.load(root).products
                       if name != document.get('definition', {}).get('name')}
        findings = specifications.findings(document, catalog)
        if findings:
            return jsonify(error='Validate the specification before viewing evidence',
                           issues=findings), 422
        spec = specifications.parse(document, catalog)
        choice_path = data.get('manifest_path')
        manifest_path = None
        manifest = None
        if choice_path not in (None, ''):
            manifest_path = workspace_path(choice_path)
            if manifest_path.suffix.lower() != '.json':
                raise WorkspaceError('Run manifest must be a JSON file')
        else:
            # Discover the latest run in the two conventional build directories.
            # Explicit paths remain available for custom --report or --out runs.
            candidates = []
            for build_root in {inside(root / 'build'), inside(path.parent / 'build')}:
                if build_root.is_dir():
                    candidates.extend(build_root.glob('*/run-manifest.json'))
            for candidate in sorted(set(candidates), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
                if candidate.stat().st_size > 2 * 1024 * 1024:
                    continue
                try:
                    value = json.loads(files.read_text(inside(candidate)))
                except (ValueError, OSError):
                    continue
                if value.get('format') == 'cadeo.run.v1' and any(
                        model.product.name in value.get('products', []) for model in spec.models):
                    manifest_path, manifest = candidate, value
                    break
        if manifest_path is not None and manifest is None:
            if not manifest_path.is_file() or manifest_path.stat().st_size > 2 * 1024 * 1024:
                raise WorkspaceError('Run manifest does not exist or is too large')
            try:
                manifest = json.loads(files.read_text(manifest_path))
            except json.JSONDecodeError as error:
                raise WorkspaceError('Run manifest is not valid JSON') from error
        return jsonify(EvidenceService().aggregate(spec, manifest,
            relative(manifest_path) if manifest_path is not None else None))

    @app.get('/api/artifact')
    def artifact():
        path = workspace_path(request.args.get('path'))
        if not path.is_file() or path.suffix.lower() not in ('.sql','.md','.json','.mmd'):
            raise WorkspaceError('Only generated SQL, Markdown and plan files can be previewed')
        content = files.read_text(path)
        if len(content) > 1024*1024:
            raise WorkspaceError('Artifact too large for browser preview')
        return jsonify(path=relative(path), content=content)

    @app.post('/api/action')
    def action():
        data = input_json()
        name = data.get('action')
        allowed = {'validate', 'trace', 'checks', 'plan', 'compile', 'build', 'discover', 'verify'}
        if name not in allowed:
            raise WorkspaceError(f'Unknown editor action: {name}')
        if installations.is_installing() and name in ('build', 'discover', 'verify'):
            raise WorkspaceError('Driver installation is running; retry database actions when it finishes')
        path = workspace_path(data.get('path'))
        if not path.exists() or (path.is_file() and path.suffix.lower() not in ('.yaml', '.yml')):
            raise WorkspaceError('Select an existing YAML specification or project directory')
        # Both project and single-file actions use the schema pinned by YAML.
        # Preflight the entire project before a service can open any schema file.
        preflight_schema_refs(path)
        specifications, products, projects = selected_services()
        if path.is_file() and data.get('schema') is not None:
            current = yaml.load(files.read_text(path), Loader=UniqueKeysLoader)
            choice = schemas.from_document(path, current, files)
            if choice != data['schema']:
                raise WorkspaceError('Editor selection differs from the YAML schema reference; save first')
        targets = data.get('targets') or []
        if not isinstance(targets, list) or any(not isinstance(x, str) for x in targets):
            raise WorkspaceError('targets must be a list of product or model identities')
        opts = tuple(targets)
        override = optional_output(data)
        dialect = data.get('dialect') or None
        if dialect is not None and not isinstance(dialect, str):
            raise WorkspaceError('dialect must be a string')
        if name == 'compile' and path.is_file() and override is None:
            # The standalone CLI defaults to cwd/build. The local web editor
            # instead uses a workspace-local default, regardless of launcher cwd.
            if all(m.output is None for m in products.validate(path).models):
                override = root / 'build'
        if name in ('compile', 'build'):
            preflight_outputs(path, override, products, projects)
        if name == 'validate':
            from cadeo.services.traceability_service import TraceabilityService
            results = []
            for spec_path, spec in items(path, products, projects):
                results.append({'path': relative(spec_path), 'product': spec.product.name,
                                'warnings': TraceabilityService().build(spec)['warnings']})
            return jsonify(valid=True, results=results)
        if name == 'trace':
            from cadeo.services.traceability_service import TraceabilityService
            return jsonify(reports=[{'path': relative(p), **TraceabilityService().build(s)}
                                    for p, s in items(path, products, projects)])
        if name == 'checks':
            from cadeo.services.sql_service import SqlTemplateService
            return jsonify(results=[{'path': relative(p), 'checks': [id for id, _ in
                                        SqlTemplateService().check_statements(s)]}
                                    for p, s in items(path, products, projects)])
        if name == 'plan':
            from cadeo.services.project_service import Project
            project = projects.load(path) if path.is_dir() else Project(
                path.parent, tuple((m.product.name, path, m) for m in products.validate(path).models))
            return jsonify(plan=projects.plan(project, opts).to_dict())
        if name == 'compile':
            test_pack = data.get('test_pack', 'none')
            if test_pack not in ('none', 'sql'):
                raise WorkspaceError('test_pack must be none or sql')
            generated = products.compile(path, override, dialect=dialect, test_pack=test_pack) if path.is_file() else \
                projects.build(projects.load(path), output=override, dialect=dialect, test_pack=test_pack)[1]
            # Metadata is only a projection of already generated artifacts.
            # No duplicate SQL compilation or dialect inference in JavaScript.
            owners = [(spec.product.name, ProductService.output_dialect(spec_path, spec, dialect, []))
                      for spec_path, spec in items(path, products, projects)]
            check_ids = {}
            pack_directories = []
            for manifest_path in generated:
                if manifest_path.name == 'manifest.json':
                    manifest = json.loads(files.read_text(manifest_path))
                    if manifest.get('format') != 'cadeo.sql-test-pack':
                        continue
                    pack_directories.append(manifest_path.parent)
                    check_ids.update({(manifest_path.parent / check['file']).resolve(): check['id']
                                      for check in manifest['checks']})
            artifacts = []
            for generated_path in generated:
                if generated_path.suffix.lower() != '.sql':
                    continue
                standalone = generated_path.resolve() in check_ids
                matching = [item for item in owners if generated_path.name.startswith(item[0] + '.')
                            or (standalone and generated_path.parent.name == item[0])]
                owner = max(matching, key=lambda item: len(item[0])) if matching else None
                artifact_type = ('standalone test' if standalone else
                                 'preservation' if generated_path.name.endswith('.preserve.sql') else
                                 'reconciliation' if generated_path.name.endswith('.audit.sql') else
                                 'quality checks' if generated_path.name.endswith('.checks.sql') else
                                 'publication' if generated_path.name.endswith('.publish.sql') else
                                 'transformation')
                artifacts.append({'path': relative(generated_path), 'dialect': owner[1] if owner else None,
                                  'product': owner[0] if owner else None, 'kind': artifact_type,
                                  'check_id': check_ids.get(generated_path.resolve()),
                                  'grammar_checked': True})
            return jsonify(generated=[relative(p) for p in generated], artifacts=artifacts,
                           test_packs=[relative(directory) for directory in pack_directories])
        if name == 'build':
            if data.get('confirm') is not True:
                raise WorkspaceError('Explicit confirmation is required to execute a build')
            result = BuildWorkflowService(files, products, projects).build(
                path, opts, override, dialect)
            result['generated_files'] = [relative(Path(p)) for p in result['generated_files']]
            result['manifest_path'] = relative(Path(result['manifest_path']))
            result['acceptance_report_path'] = relative(Path(result['acceptance_report_path']))
            return jsonify(result)
        if name == 'discover':
            from cadeo.brokers.database_broker import DatabaseBroker
            from cadeo.services.discovery_service import DiscoveryService
            service = DiscoveryService(DatabaseBroker())
            reports = []
            for spec_path, spec in items(path, products, projects):
                results = service.discover(spec, profile=data.get('profile') is True)
                reports.append({'path': relative(spec_path), 'passed': all(r.passed for r in results),
                                'sources': [r.to_dict() for r in results]})
            return jsonify(passed=all(r['passed'] for r in reports), reports=reports)
        # `verify` intentionally stays the existing SQLite verification workflow,
        # backed by SQLAlchemy's execution broker (not sqlite3).
        from cadeo.brokers.execution_broker import ExecutionBroker
        from cadeo.services.verification_service import VerificationService
        from cadeo.services.sql_service import SqlTemplateService
        if path.is_dir():
            raise WorkspaceError('Verify accepts one YAML specification')
        database = workspace_path(data.get('database'))
        reports = VerificationService(specifications, ExecutionBroker()).verify(path,database)
        passed = all(c['status']=='passed' for r in reports for c in r['checks'])
        return jsonify(passed=passed,models=reports,
            checks=[c for r in reports for c in r['checks']],
            audit=[{'model':r['model'],'rows':r['audit']} for r in reports])

    return app
