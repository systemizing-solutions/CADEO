"""Create local input tables, compile a two-hop join, and execute quality tests."""

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
    paths = products.compile(root / "sqlite_joined_devices.yaml", Path(directory), dialect="sqlite")
    artifacts_by_name = {path.name: path for path in paths}
    with ExecutionBroker().transaction("sqlite:///:memory:") as db:
        execute_script(db, """
            CREATE TABLE input_devices (asset_id TEXT, merchant_account_number TEXT);
            CREATE TABLE asset_scope (source_asset_id TEXT, product_service_id TEXT);
            CREATE TABLE product_service (product_service_id TEXT, product_name TEXT);
            INSERT INTO input_devices VALUES ('dev-1', 'merch-1'), ('dev-2', 'merch-2');
            INSERT INTO asset_scope VALUES ('dev-1', 'product-1');
            INSERT INTO product_service VALUES ('product-1', 'Card Terminal');
        """)
        execute_script(db, artifacts_by_name["joined_device_inventory.device.preserve.sql"].read_text())
        execute_script(db, artifacts_by_name["joined_device_inventory.device.sql"].read_text())
        rows = db.exec_driver_sql(
            "SELECT * FROM joined_device_inventory__dp_stage ORDER BY source_device_id"
        ).fetchall()
        assert rows == [('dev-1', 'merch-1', 'Card Terminal'), ('dev-2', 'merch-2', None)], rows
        print("Generated LEFT JOIN SQL executed:", rows)
        for statement in ExecutionBroker.statements(artifacts_by_name["joined_device_inventory.device.checks.sql"].read_text(), "sqlite"):
            if statement.strip():
                assert db.exec_driver_sql(statement).fetchone()[0] == 0
        execute_script(db, artifacts_by_name["joined_device_inventory.device.publish.sql"].read_text())
        assert db.exec_driver_sql("SELECT COUNT(*) FROM joined_device_inventory").fetchone()[0] == 2
        print("Generated join-key and canonical-key quality checks passed; product published")
