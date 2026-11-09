"""Local v0.8.5 executable acceptance test (SQLAlchemy)."""
from pathlib import Path
import argparse
from tempfile import TemporaryDirectory

from cadeo.brokers.execution_broker import ExecutionBroker
from cadeo.brokers.file_broker import FileBroker
from cadeo.services.product_service import ProductService
from cadeo.services.specification_service import SpecificationService



def execute_script(connection, script: str) -> None:
    """Execute generated SQL exclusively through the SQLAlchemy broker."""
    for statement in ExecutionBroker.statements(script, "sqlite"):
        connection.exec_driver_sql(statement)

ROOT = Path(__file__).resolve().parent


def prepare(db):
    execute_script(db, """
        CREATE TABLE devices (asset_id TEXT, merchant_account TEXT, sub_id TEXT,
                              status TEXT, updated_at TEXT, unmapped_payload TEXT);
        CREATE TABLE fallback_devices (asset_id TEXT, merchant_account TEXT,
                                       status TEXT, updated_at TEXT, unmapped_notes TEXT);
        CREATE TABLE agreement_scope (agreement_scope_id TEXT, agreement_type TEXT,
                                      original_notes TEXT);
        INSERT INTO devices VALUES
          ('d1','m1','sub1','active','2026-10-10','retain raw A'),
          ('d2','m2','sub2','inactive','2026-10-11','retain raw B'),
          ('d3',NULL,'sub3','active','2026-10-12','retain raw C');
        INSERT INTO fallback_devices VALUES
          ('d1','m0','active','2026-10-15','retain fallback A'),
          ('d4','m4','active','2026-10-12','retain fallback D');
        INSERT INTO agreement_scope VALUES
          ('crm:sub1','merchant agreement','retain agreement notes');
    """)


def run(database: Path | None = None) -> None:
    if database is not None and database.exists():
        raise FileExistsError(f"Demo will not overwrite existing database: {database}")
    if database is not None:
        database.parent.mkdir(parents=True, exist_ok=True)
        database.touch()
    products = ProductService(FileBroker(), SpecificationService(FileBroker()))
    with TemporaryDirectory() as temp:
        output = Path(temp)
        with ExecutionBroker().transaction(f"sqlite:///{database}" if database else "sqlite:///:memory:") as db:
            _execute_demo(db, output, products)


def _execute_demo(db, output, products):
    artifacts = products.compile(ROOT / "sqlite_device_full.yaml", output, "sqlite")
    artifacts_by_name = {path.name: path for path in artifacts}
    prepare(db)
    execute_script(db, artifacts_by_name["device_product.device.preserve.sql"].read_text())
    execute_script(db, artifacts_by_name["device_product.device.sql"].read_text())
    rows = db.exec_driver_sql("SELECT device_id,merchant_account_number,agreement_status,source_system "
                      "FROM device_product__dp_stage ORDER BY device_id").fetchall()
    assert rows == [
        ('d1', 'm1', 'contracted', 'primary'),
        ('d3', 'UNKNOWN', 'uncontracted', 'primary'),
        ('d4', 'm4', 'unknown', 'fallback'),
    ], rows
    assert db.exec_driver_sql("SELECT unmapped_payload FROM device_product__raw_0_d "
                      "WHERE asset_id='d2'").fetchone()[0] == 'retain raw B'
    assert db.exec_driver_sql("SELECT original_notes FROM device_product__raw_0_sc").fetchone()[0] == 'retain agreement notes'
    audit = db.exec_driver_sql(artifacts_by_name["device_product.device.audit.sql"].read_text()).fetchall()
    assert audit[0][0] == 0 and audit[0][8] == 1, audit  # primary: one filtered record
    assert audit[1][0] == 1 and audit[1][9] == 1, audit  # fallback: one precedence loss
    check_queries = [q.strip() for q in ExecutionBroker.statements(artifacts_by_name["device_product.device.checks.sql"].read_text(), "sqlite")
                     if q.strip()]
    failures = [db.exec_driver_sql(q).fetchone()[0] for q in check_queries]
    assert all(x == 0 for x in failures), failures
    execute_script(db, artifacts_by_name["device_product.device.publish.sql"].read_text())
    assert db.exec_driver_sql("SELECT COUNT(*) FROM device_product").fetchone()[0] == 3
    print('PASS: v0.8.5 DAG-ordered staging, checks, publication, reconciliation and preservation')
    print('Output:', rows)
    print('Audit:', audit)
    print('Quality failures:', failures)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run compiled v0.8.5 demo in SQLite')
    parser.add_argument('--database', type=Path, help='Create a persistent database for cadeo verify')
    args = parser.parse_args()
    run(args.database)
