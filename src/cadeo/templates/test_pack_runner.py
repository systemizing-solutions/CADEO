"""Run an exported SQL pack without CADEO or its compiler dependencies."""

import argparse
import json
import math
import os
from pathlib import Path
from decimal import Decimal

from sqlalchemy import create_engine


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url-env", default="DATABASE_URL")
    parser.add_argument("--kind", choices=("all", "unit", "data"), default="all")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("format") != "cadeo.sql-test-pack" or manifest.get("version") != 1:
        parser.error("Unsupported test-pack manifest")
    url = os.environ.get(args.url_env)
    if not url:
        parser.error(f"Set the database URL in {args.url_env}")
    checks = [check for check in manifest["checks"]
              if args.kind == "all" or check["kind"] == args.kind]
    if not checks:
        print(json.dumps({"passed": False, "error": "No checks selected", "checks": []}))
        return 1
    results = []
    try:
        engine = create_engine(url)
    except Exception:
        print(json.dumps({"passed": False, "error": "Database configuration failed", "checks": []}))
        return 1
    try:
        for check in checks:
            result = {"id": check["id"], "passed": False, "failures": None}
            try:
                path = (root / check["file"]).resolve()
                if path.parent != root or path.suffix != ".sql":
                    raise ValueError("Invalid check file")
                with engine.connect() as connection:
                    query = connection.exec_driver_sql(path.read_text())
                    rows = query.fetchmany(2)
                    if list(query.keys()) != ["failures"] or len(rows) != 1:
                        raise ValueError("Expected one row and one failures column")
                    failures = rows[0][0]
                    if (isinstance(failures, bool) or not isinstance(failures, (int, float, Decimal))
                            or not math.isfinite(failures) or failures < 0
                            or failures != int(failures)):
                        raise ValueError("Expected a finite nonnegative integer failure count")
                    result.update(failures=int(failures), passed=failures == 0)
            except Exception:
                result["error"] = "SQL execution or result validation failed; verify dialect and prerequisites"
            results.append(result)
    finally:
        engine.dispose()
    passed = all(result["passed"] for result in results)
    print(json.dumps({"passed": passed, "checks": results}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())