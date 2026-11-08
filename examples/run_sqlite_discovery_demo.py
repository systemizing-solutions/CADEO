"""Execute live discovery and profiling against a real, temporary SQLite file.

No SQLGlot/SQLFluff installation or warehouse credentials are needed for this demo.
"""

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from cade.brokers.execution_broker import ExecutionBroker
from cade.exposers.cli import main


if __name__ == "__main__":
    with TemporaryDirectory() as temporary:
        database = Path(temporary) / "source.sqlite"
        database.touch()
        with ExecutionBroker().transaction(f"sqlite:///{database}") as connection:
            connection.exec_driver_sql("CREATE TABLE input_devices ("
                               "asset_id TEXT NOT NULL, merchant_account_number TEXT)")
            connection.exec_driver_sql("INSERT INTO input_devices VALUES (?, ?)",
                                   [("A1", "M1"), ("A2", None)])
        os.environ["CADE_DEMO_DATABASE_URL"] = f"sqlite:///{database}"
        spec = Path(__file__).with_name("sqlite_discovery.yaml")
        raise SystemExit(main(["discover", str(spec), "--profile"]))
