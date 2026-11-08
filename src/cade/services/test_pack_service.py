"""Opt-in standalone exports from the authoritative SQL check registry."""

import json
from pathlib import Path

from cade.brokers.file_broker import FileBrokerContract
from cade.models.specification import Specification
from cade.services.sqlglot_service import SqlGlotSqlService


class TestPackService:
    def __init__(self, files: FileBrokerContract) -> None:
        self.files = files

    def render(self, spec: Specification, output: Path,
               sql: SqlGlotSqlService) -> dict[Path, str]:
        directory = output / "tests" / spec.product.name
        artifacts: dict[Path, str] = {}
        tests = {test.id: test for test in spec.tests}
        checks = []
        for index, (identifier, statement) in enumerate(sql.standalone_checks(spec), 1):
            filename = f"check-{index:04d}.sql"
            artifacts[directory / filename] = statement
            test = tests.get(identifier)
            checks.append({"id": identifier, "file": filename,
                           "kind": "unit" if test and test.type == "expected_rows" else "data",
                           "type": test.type if test else "generated"})
        snapshots = sorted({sql.templates.snapshot_target(spec, index, alias)
                            for index, branch in enumerate(spec.branches)
                            for alias in (branch.source.alias, *(join.alias for join in branch.joins))
                            if spec.preservation.mode == "snapshot"})
        manifest = {"format": "cade.sql-test-pack", "version": 1,
                    "product": spec.product.name, "dialect": sql.dialect,
                    "target": spec.product.target,
                    "prerequisites": {"published_target": spec.product.target,
                                      "sources": sorted({source.table for source in spec.sources}),
                                      "snapshots": snapshots},
                    "success": "Exactly one row and one numeric failures column equal to zero",
                    "checks": checks}
        artifacts[directory / "manifest.json"] = json.dumps(manifest, indent=2) + "\n"
        artifacts[directory / "run.py"] = self.files.read_bundled_text("templates/test_pack_runner.py")
        artifacts[directory / "README.md"] = (
            f"# SQL test pack: {spec.product.name}\n\n"
            "Run each SQL file in a compatible database client, or install SQLAlchemy and the "
            "database driver and run `python run.py --url-env DATABASE_URL`. "
            "No CADE installation, YAML, compiler or build is needed.\n\n"
            "Every query must return exactly one row and one numeric `failures` column; "
            "zero passes, a positive count fails. SQL errors and malformed results fail. "
            "The runner reports every check and exits 0 only when all pass. "
            "Use `--kind unit` to run only self-contained inline-fixture tests, "
            "or `--kind data` for live-data checks.\n\n"
            "Live checks require the published target and/or current source tables listed in "
            "manifest.json, in one database connection. Snapshot checks also require the listed "
            "non-run-specific raw snapshots. Source reconciliation checks describe current "
            "sources, not historical build evidence. Missing prerequisites are errors, not skips. "
            "Use a read-only database account. No setup, staging, publication or writes are emitted.\n\n"
            "These are standalone SQL assertions, not dbt project files. Re-export after contract "
            "changes. Disabling export preserves previously exported packs; it does not delete files.\n")
        return artifacts