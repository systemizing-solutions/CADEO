"""Full v0.6.1 acceptance demo. Run after `pip install -e .`.

Uses the real CLI, SQLGlot, SQLFluff and SQLAlchemy. A failed business
assertion must not replace the previously published SQLite view.
"""
from cadeo.brokers.execution_broker import ExecutionBroker

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory



def execute_script(connection, script: str) -> None:
    """Execute generated SQL exclusively through the SQLAlchemy broker."""
    for statement in ExecutionBroker.statements(script, "sqlite"):
        connection.exec_driver_sql(statement)

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / 'examples/sqlite_business_tests.yaml'


def main():
    with TemporaryDirectory() as temp:
        database = Path(temp) / 'devices.sqlite'
        database.touch()
        destination = Path(temp) / 'generated'
        with ExecutionBroker().transaction(f"sqlite:///{database}") as conn:
            execute_script(conn, '''
                CREATE TABLE input_devices (
                  asset_id TEXT NOT NULL,
                  merchant_account_number TEXT,
                  device_status TEXT NOT NULL
                );
                CREATE TABLE input_merchants (
                  merchant_account_number TEXT NOT NULL,
                  merchant_name TEXT NOT NULL
                );
                INSERT INTO input_devices VALUES ('d1', 'm1', 'active');
                INSERT INTO input_merchants VALUES ('m1', 'Merchant One');
            ''')
        env = {**os.environ, 'DPC_EXECUTION_DATABASE_URL': f'sqlite:///{database}'}
        command = [sys.executable, '-m', 'cadeo', 'build', str(SPEC),
                   '--dialect', 'sqlite', '--out', str(destination)]
        first = subprocess.run(command, env=env, capture_output=True, text=True)
        assert first.returncode == 0, f'Valid build failed:\n{first.stdout}\n{first.stderr}'
        with ExecutionBroker().transaction(f"sqlite:///{database}") as conn:
            before = conn.exec_driver_sql('SELECT source_device_id, device_status, merchant_name '
                                  'FROM device_business_tests').fetchall()
            assert before == [('d1', 'active', 'Merchant One')], before
            conn.exec_driver_sql("UPDATE input_devices SET device_status = 'invalid'")
        second = subprocess.run(command, env=env, capture_output=True, text=True)
        assert second.returncode != 0, 'Invalid device status should have blocked publication'
        assert 'device_status_valid' in second.stderr, second.stderr
        with ExecutionBroker().transaction(f"sqlite:///{database}") as conn:
            after = conn.exec_driver_sql('SELECT source_device_id, device_status, merchant_name '
                                 'FROM device_business_tests').fetchall()
        assert after == before, 'Failed build replaced previously published data'
        print('PASS: valid device built; invalid status failed; previous publication preserved')


if __name__ == '__main__':
    main()
