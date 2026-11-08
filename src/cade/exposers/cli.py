"""Thin CLI exposer: arguments in, application services out."""

import argparse
import json
import os
import shlex
from uuid import uuid4
from pathlib import Path
from sqlalchemy.exc import SQLAlchemyError
import sys


from cade.brokers.file_broker import FileBroker
from cade.brokers.execution_broker import ExecutionBroker
from cade.services.verification_service import VerificationService
from cade.models.specification import SpecificationValidationError
from cade.services.product_service import ProductService
from cade.services.specification_service import SpecificationService


VERSION = "0.8.3"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cade", description="Validate YAML with JSON Schema and generate SQL")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Create a YAML example and adjacent JSON Schema for editor help")
    init.add_argument("path", nargs="?", type=Path, default=Path("product.yaml"))

    tutorial = commands.add_parser('tutorial', help='Tutorial: build a local SQLite connection data product')
    tutorial_commands = tutorial.add_subparsers(dest='tutorial_action')
    tutorial_init = tutorial_commands.add_parser('init', help='Create sample data, contracts, and a local guide; never overwrite')
    tutorial_init.add_argument('path', nargs='?', type=Path, default=Path('tutorial'))
    tutorial_commands.add_parser('guide', help='Read the complete guided example')
    tutorial_report = tutorial_commands.add_parser('report', help='Read the published support queue')
    tutorial_report.add_argument('--path', type=Path, default=Path('tutorial'))
    tutorial_exercise = tutorial_commands.add_parser('exercise', help='Prove a wrong mapping is blocked; build the full product first')
    tutorial_exercise.add_argument('case', choices=('wrong-key', 'wrong-status', 'inner-join'))
    tutorial_exercise.add_argument('--path', type=Path, default=Path('tutorial'))

    schema = commands.add_parser("schema", help="Print the specification JSON Schema or write it to a file")
    schema.add_argument("--out", type=Path, help="Write the schema to this path (will not overwrite)")

    commands.add_parser("dialects", help="List SQL output dialects supported by both SQLGlot and SQLFluff")

    engines = commands.add_parser("engines", help="List SQLAlchemy database drivers and their verified capabilities")
    engines.add_argument("--json", action="store_true", help="Machine-readable driver availability and safety status")
    engines.add_argument("--installed", action="store_true", help="Only list dialects with a loadable DBAPI driver")
    engines.add_argument("--url-env", metavar="NAME", help="Inspect the exact database URL in this environment variable")
    engines.add_argument("--connect", action="store_true", help="Attempt a live connection (requires --url-env)")

    validate = commands.add_parser("validate", help="Validate a YAML file or all YAML specifications in a directory")
    validate.add_argument("spec", type=Path)

    trace = commands.add_parser("trace", help="Show requirement-to-field lineage and bidirectional traceability warnings")
    trace.add_argument("spec", type=Path, help="YAML file or directory")
    trace.add_argument("--json", action="store_true", help="Print machine-readable lineage and warnings")

    checks_command = commands.add_parser("checks", help="List generated quality-check IDs available for acceptance criteria")
    checks_command.add_argument("spec", type=Path, help="YAML file or directory")

    evidence_command = commands.add_parser(
        "evidence", help="Show requirement-to-check traceability and optional build evidence")
    evidence_command.add_argument("spec", type=Path, help="YAML specification or project directory")
    evidence_command.add_argument("--manifest", type=Path,
                                  help="Optional run manifest JSON; without it, show not_run evidence")
    evidence_command.add_argument("--json", action="store_true", help="Output machine-readable evidence")

    plan_command = commands.add_parser("plan", help="Show the validated dependency DAG and execution order without generating SQL")
    plan_command.add_argument("spec", type=Path, help="YAML file or project directory")
    plan_command.add_argument("--target", action="append", default=[],
                              help="Plan this product and required upstream products (repeatable; folder only)")

    compile_command = commands.add_parser("compile", help="Compile a YAML file or recursively compile a directory of YAML files")
    compile_command.add_argument("spec", type=Path, help="YAML file or directory to scan recursively")
    compile_command.add_argument("--out", type=Path, help="Override YAML output directories (directory scans preserve subfolders)")
    compile_command.add_argument("--dialect", type=str.lower, default=None,
                                 help="Override the YAML dialect (default: YAML setting, then spark)")

    build_command = commands.add_parser(
        "build", help="Compile and execute a product or project through SQLAlchemy in DAG order")
    build_command.add_argument("spec", type=Path, help="Product YAML or recursively scanned project folder")
    build_command.add_argument("--target", action="append", default=[],
                               help="Build this product and its required upstream products (repeatable)")
    build_command.add_argument("--out", type=Path,
                               help="Override all YAML output paths; preserve each YAML subfolder")
    build_command.add_argument("--dialect", type=str.lower,
                               help="Override the SQL dialect for every selected product")
    build_command.add_argument("--report", type=Path,
                               help="Save the run manifest to this JSON file (default: alongside the project plan)")

    discover = commands.add_parser(
        "discover", help="Connect to live sources and compare table schemas with YAML contracts")
    discover.add_argument("spec", type=Path, help="YAML file or directory to scan recursively")
    discover.add_argument("--profile", action="store_true",
                          help="Also scan full tables for row counts, null counts and duplicate keys")
    discover.add_argument("--json", action="store_true", help="Print a machine-readable report")

    verify = commands.add_parser("verify", help="Verify existing SQLite data through the SQLAlchemy execution broker")
    verify.add_argument("spec", type=Path)
    verify.add_argument("--database", type=Path, required=True, help="Existing SQLite database with staged data and raw snapshots")
    verify.add_argument("--report", type=Path, help="Write check IDs, failure counts and results as JSON")

    ui = commands.add_parser("ui", help="Start the local JSON Schema-driven web editor")
    ui.add_argument("root", nargs="?", type=Path, default=Path.cwd(),
                    help="Workspace directory (created if missing; existing files are preserved)")
    ui.add_argument("--port", type=int, default=8765,
                    help="Local HTTP port (default: 8765)")
    ui.add_argument("--overwrite", action="store_true",
                    help="Replace the workspace's bundled schema; leave product YAML and other files untouched")

    args = parser.parse_args(argv)
    if args.command == "engines" and args.connect and not args.url_env:
        parser.error("--connect requires --url-env")
    if args.command == "ui":
        if args.root.exists() and not args.root.is_dir():
            parser.error(f"Editor workspace is not a directory: {args.root}")
        try:
            from cade.exposers.web import create_app
        except ImportError as error:
            print(f"Error: Flask is required for the local editor: {error}", file=sys.stderr)
            return 1
        try:
            app = create_app(args.root, overwrite=args.overwrite)
        except (OSError, ValueError) as error:
            parser.error(f"Could not open editor workspace: {error}")
        print(f"Local editor: http://127.0.0.1:{args.port}/", flush=True)
        app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)
        return 0
    files = FileBroker()
    specifications = SpecificationService(files)
    products = ProductService(files, specifications)
    from cade.services.project_service import ProjectService
    projects = ProjectService(files, specifications, products)

    def resolved_items(path: Path):
        """Resolve project references for directory commands; files stay standalone."""
        if path.is_dir():
            return tuple((spec_path, spec) for _, spec_path, spec in projects.load(path).products)
        return tuple((path, spec) for spec in products.validate(path).models)

    try:
        if args.command == 'tutorial':
            from cade.services.tutorial_service import TutorialService
            tutorial_service = TutorialService()
            if args.tutorial_action == 'init':
                tutorial_result = tutorial_service.create(args.path)
                print(f"Created local tutorial: {tutorial_result['root']}")
                print('Run this command in your terminal before normal CLI builds:')
                print(tutorial_result['environment'])
                print(f'First build: cade build {shlex.quote(str(args.path / "starter/product.yaml"))}')
                print('Guide: cade tutorial guide')
                print(f'Editor: cade ui {shlex.quote(str(args.path))} then select Tutorial')
            elif args.tutorial_action == 'report':
                print(json.dumps(tutorial_service.report(args.path), indent=2))
            elif args.tutorial_action == 'exercise':
                tutorial_result = tutorial_service.exercise(args.path, args.case)
                print(f"Run manifest: {tutorial_result['manifest_path']}")
                print('Failed checks: ' + ', '.join(tutorial_result['failed_checks']))
                print(json.dumps({key: tutorial_result[key] for key in (
                    'expected_failure', 'publication_unchanged', 'support_queue')}, indent=2))
                return 0 if tutorial_result['expected_failure'] and tutorial_result['publication_unchanged'] else 1
            else:
                print(files.read_bundled_text('templates/tutorial.md'))
        elif args.command == "init":
            for path in products.init(args.path):
                print(f"Created {path}")
        elif args.command == "schema":
            if args.out is not None:
                files.write_text(args.out, specifications.schema_json(), overwrite=False)
                print(f"Created {args.out}")
            else:
                print(specifications.schema_json(), end="")
        elif args.command == "dialects":
            from cade.services.dialect_service import DialectService
            for name in DialectService.available():
                print(name)
        elif args.command == "engines":
            from cade.services.engine_catalog_service import EngineCatalogService
            service = EngineCatalogService()
            available = service.available(installed_only=args.installed)
            selected = None
            if args.url_env:
                connection_url = os.environ.get(args.url_env)
                if not connection_url:
                    raise SpecificationValidationError(
                        f"Environment variable {args.url_env} is not set")
                selected = service.inspect_url(connection_url, connect=args.connect)
            if args.json:
                print(json.dumps({"engines": available, "selected": selected}, indent=2))
            else:
                print("SQLAlchemy drivers (installed = DBAPI successfully loaded; build = publication certified):")
                for item in available:
                    installed = "ready" if item['driver_loaded'] else "not installed"
                    build_ready = "build-ready" if item['build_ready'] else "not build-ready"
                    print(f"  {item['id']:18} {installed:14} {build_ready:17} {item['name']}")
                    if not item['driver_loaded'] and item['install_command']:
                        print(f"      Optional install: {item['install_command']}")
                if selected:
                    print(f"Configured driver: {selected['driver']} "
                          f"loaded={selected['driver_loaded']} "
                          f"connected={selected['connected']} "
                          f"publication_strategy={selected['publication_strategy'] or 'none'}")
                    if selected['error']:
                        print(f"  Driver check failed: {selected['error']}")
            if selected is not None and (not selected['driver_loaded'] or
                                         (args.connect and not selected['connected'])):
                return 1
        elif args.command == "validate":
            for spec_path, product in resolved_items(args.spec):
                print(f"Valid: {spec_path} — {product.product.name} ({len(product.canonical.attributes)} fields)")
                from cade.services.traceability_service import TraceabilityService
                for warning in TraceabilityService().build(product)["warnings"]:
                    print(f"Warning: {spec_path}: {warning}", file=sys.stderr)
        elif args.command == "trace":
            from cade.services.traceability_service import TraceabilityService
            reports = []
            for spec_path, spec in resolved_items(args.spec):
                report = TraceabilityService().build(spec)
                reports.append({"spec": str(spec_path), **report})
            if args.json:
                print(json.dumps(reports, indent=2, ensure_ascii=False))
            else:
                for report in reports:
                    print(f"Traceability: {report['spec']} — {report['product']}")
                    for field in report["fields"]:
                        print(f"  {field['attribute']}: requirements={field['requirement_ids']}; "
                              f"rules={len(field['rules'])}")
                    for warning in report["warnings"]:
                        print(f"Warning: {warning}", file=sys.stderr)
        elif args.command == "evidence":
            from cade.services.evidence_service import EvidenceService
            manifest = None
            if args.manifest is not None:
                manifest = json.loads(files.read_text(args.manifest))
            service = EvidenceService()
            reports = [service.build(spec, manifest, str(args.manifest) if args.manifest else None)
                       for _, spec in resolved_items(args.spec)]
            if args.json:
                print(json.dumps(reports, indent=2, ensure_ascii=False))
            else:
                for report in reports:
                    print(service.markdown(report), end="")
        elif args.command == "checks":
            from cade.services.sql_service import SqlTemplateService
            for spec_path, spec in resolved_items(args.spec):
                print(f"Checks: {spec_path}")
                for check_id, _ in SqlTemplateService().check_statements(spec):
                    print(f"  {spec.product.name.split('.',1)[1]}/{check_id}")
        elif args.command == "plan":
            from cade.services.dependency_service import DependencyService
            if args.spec.is_dir():
                result = projects.plan(projects.load(args.spec), tuple(args.target))
                print("Project build order: " + " -> ".join(result.products))
                for index, node in enumerate(result.nodes, 1):
                    print(f"  {index:02d}. {node.id} [{node.action}]" +
                          (f" -> {node.artifact}" if node.artifact else ""))
            else:
                from cade.services.project_service import Project
                bundle = products.validate(args.spec)
                project = Project(args.spec.parent, tuple((m.product.name,args.spec,m)
                    for m in bundle.models))
                plan = projects.plan(project, tuple(args.target))
                print(f"Model dependency plan: {args.spec}")
                for index, node in enumerate(plan.nodes, 1):
                    print(f"  {index:02d}. {node.id} [{node.action}]" +
                          (f" -> {node.artifact}" if node.artifact else ""))
        elif args.command == "build":
            from cade.services.build_workflow_service import BuildWorkflowService
            result = BuildWorkflowService(files, products, projects).build(
                args.spec, tuple(args.target), args.out, args.dialect, args.report)
            for warning in result["warnings"]:
                print(f"Warning: {warning}", file=sys.stderr)
            print(f"Acceptance results: {result['acceptance_report_path']}")
            print("Project build order: " + " -> ".join(result["products"]))
            print(f"Run: {result['run_id']} — {result['status']}")
            print(f"Run manifest: {result['manifest_path']}")
            if result["status"] != "passed":
                print(f"Error: {result['manifest']['error']}", file=sys.stderr)
                return 1
            print("All required checks passed; product publication committed.")
        elif args.command == "discover":
            from cade.brokers.database_broker import DatabaseBroker
            from cade.services.discovery_service import DiscoveryService
            service = DiscoveryService(DatabaseBroker())
            all_reports = []
            for spec_path, spec in resolved_items(args.spec):
                results = service.discover(spec, profile=args.profile)
                all_reports.append({
                    "spec": str(spec_path), "product": spec.product.name,
                    "profiled": args.profile,
                    "passed": all(result.passed for result in results),
                    "sources": [result.to_dict() for result in results],
                })
            if args.json:
                print(json.dumps({"passed": all(report["passed"] for report in all_reports),
                                  "specifications": all_reports}, indent=2))
            else:
                for report in all_reports:
                    print(f"Discovery: {report['spec']} — {report['product']}")
                    for result in report["sources"]:
                        status = 'SKIP' if result['skipped_reason'] else ('PASS' if result['passed'] else 'FAIL')
                        print(f"  {status} {result['source']} [{result['table']}]")
                        if result['skipped_reason']:
                            print(f"    {result['skipped_reason']}")
                        for error in result["errors"]:
                            print(f"    {error}")
                        if result["extra_columns"]:
                            print(f"    Additional columns (allowed): {', '.join(result['extra_columns'])}")
                        if result["row_count"] is not None:
                            print(f"    Rows: {result['row_count']}; "
                                  f"duplicate key groups: {result['duplicate_key_groups']}")
                print("Discovery PASSED" if all(report["passed"] for report in all_reports)
                      else "Discovery FAILED")
            return 0 if all(report["passed"] for report in all_reports) else 1
        elif args.command == "verify":
            if args.report is not None:
                if args.report.suffix.lower() != ".json":
                    raise SpecificationValidationError("--report must name a .json file")
                if args.report.resolve() in {args.spec.resolve(), args.database.resolve()}:
                    raise SpecificationValidationError("--report cannot overwrite the input YAML or SQLite database")
            reports = VerificationService(specifications, ExecutionBroker()).verify(args.spec,args.database)
            for report in reports:
                print(f"Model {report['model']}:")
                for check in report['checks']:
                    print(f"  {check['id']}: {check['status']} " +
                          (f"({check['failures']} failures)" if check['failures'] is not None else ''))
                for audit in report['audit']:
                    print('  Audit: ' + ', '.join(str(part) for part in audit))
            passed = all(check['status']=='passed' for report in reports for check in report['checks'])
            if args.report is not None:
                files.write_text(args.report,json.dumps({'format':'cade.test-results.v2',
                    'passed':passed,'models':reports},indent=2)+'\n')
                print(f'Wrote {args.report}')
            if not passed:
                print('FAILED: one or more model quality gates did not pass',file=sys.stderr)
                return 1
            print('PASSED: all model quality checks returned zero failures')
        elif args.command == "compile":
            if args.spec.is_dir():
                _, generated = projects.build(projects.load(args.spec), output=args.out,
                                              dialect=args.dialect,
                                              on_warning=lambda message: print(f"Warning: {message}", file=sys.stderr))
            else:
                generated = products.compile(args.spec, args.out, dialect=args.dialect,
                    on_warning=lambda message: print(f"Warning: {message}", file=sys.stderr))
            for path in generated:
                print(f"Generated {path}")
        return 0
    except (SpecificationValidationError, FileNotFoundError, FileExistsError, PermissionError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except SQLAlchemyError as error:
        # Database errors may include SQL values or credentials. Keep CLI errors sanitized.
        print(f"Error: Database execution failed ({type(error).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
