"""End-to-end proof: merchant output is published before device product SQL uses it.

Requires the installed SQLGlot and SQLFluff dependencies. Uses only a disposable
SQLite database created in a TemporaryDirectory.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import os

from cade.brokers.execution_broker import ExecutionBroker
from cade.exposers.cli import main

HERE = Path(__file__).resolve().parent


def main_demo() -> None:
    with TemporaryDirectory() as folder:
        db = Path(folder) / 'cross_product.db'
        db.touch()
        with ExecutionBroker().transaction(f"sqlite:///{db}") as conn:
            conn.exec_driver_sql('CREATE TABLE raw_merchants (merchant_id TEXT NOT NULL, merchant_name TEXT)')
            conn.exec_driver_sql("INSERT INTO raw_merchants VALUES ('m1', 'Example Merchant')")
            conn.exec_driver_sql('CREATE TABLE raw_devices (asset_id TEXT NOT NULL, merchant_account_number TEXT)')
            conn.exec_driver_sql("INSERT INTO raw_devices VALUES ('d1', 'm1')")
        os.environ['DPC_EXECUTION_DATABASE_URL'] = f'sqlite:///{db}'
        code = main(['build', str(HERE / 'cross_product'), '--out', str(Path(folder) / 'build'),
                     '--dialect', 'sqlite'])
        if code:
            raise RuntimeError(f'Cross-product build failed with status {code}')
        with ExecutionBroker().transaction(f"sqlite:///{db}") as conn:
            expected = [('d1', 'Example Merchant')]
            actual = conn.exec_driver_sql('SELECT source_device_id, merchant_name FROM device_inventory').fetchall()
            assert actual == expected, (actual, expected)
            assert conn.exec_driver_sql('SELECT COUNT(*) FROM merchant_inventory').fetchone()[0] == 1
        print('PASS: merchant product published before device product; quality gates passed')


if __name__ == '__main__':
    main_demo()
