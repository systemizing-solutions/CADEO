"""Execute compiled SQLite SQL locally and demonstrate a real result."""

from pathlib import Path
from tempfile import TemporaryDirectory

from cade.brokers.execution_broker import ExecutionBroker
from cade.brokers.file_broker import FileBroker
from cade.services.product_service import ProductService
from cade.services.specification_service import SpecificationService




def execute_script(connection, script: str) -> None:
    """Execute generated SQL exclusively through the SQLAlchemy broker."""
    for statement in ExecutionBroker.statements(script, "sqlite"):
        connection.exec_driver_sql(statement)

root = Path(__file__).resolve().parent
files = FileBroker()
products = ProductService(files, SpecificationService(files))

with TemporaryDirectory() as directory:
    artifacts = products.compile(root / "sqlite_devices.yaml", Path(directory), dialect="sqlite")
    with ExecutionBroker().transaction("sqlite:///:memory:") as connection:
        connection.exec_driver_sql("CREATE TABLE input_devices (asset_id TEXT, merchant_account_number TEXT)")
        connection.exec_driver_sql("INSERT INTO input_devices VALUES (?, ?)", [("dev-1", "merch-1"), ("dev-2", "merch-2")])
        execute_script(connection, (Path(directory) / "device_inventory.device.preserve.sql").read_text())
        execute_script(connection, (Path(directory) / "device_inventory.device.sql").read_text())
        rows = connection.exec_driver_sql("SELECT * FROM device_inventory__dp_stage ORDER BY source_device_id").fetchall()
        print("Generated SQL executed successfully:", rows)
        # Parse statements with the same SQLGlot-backed execution broker.
        checks = (Path(directory) / "device_inventory.device.checks.sql").read_text()
        for statement in ExecutionBroker.statements(checks, "sqlite"):
            if statement.strip():
                result = connection.exec_driver_sql(statement).fetchone()[0]
                assert result == 0, f"Data quality check failed: {statement}"
        execute_script(connection, (Path(directory) / "device_inventory.device.publish.sql").read_text())
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM device_inventory").fetchone()[0] == 2
        print("Generated quality checks passed; product published")
