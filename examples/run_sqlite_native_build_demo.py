"""Real CLI build/rerun/quality-failure proof using SQLGlot + SQLFluff + SQLAlchemy.

Run after `pip install -e .`. This uses a disposable SQLite database only.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os

from cadeo.brokers.execution_broker import ExecutionBroker
from cadeo.exposers.cli import main

HERE = Path(__file__).resolve().parent


def run_demo():
    with TemporaryDirectory() as temp:
        root = Path(temp)
        database = root / 'data.db'
        database.touch()
        specification = HERE / 'sqlite_devices.yaml'
        with ExecutionBroker().transaction(f"sqlite:///{database}") as conn:
            conn.exec_driver_sql('CREATE TABLE input_devices (asset_id TEXT NOT NULL, merchant_account_number TEXT)')
            conn.exec_driver_sql("INSERT INTO input_devices VALUES ('d1', 'm1')")
        os.environ['DPC_EXECUTION_DATABASE_URL'] = f'sqlite:///{database}'
        output = root / 'generated'
        args = ['build', str(specification), '--dialect', 'sqlite', '--out', str(output)]
        assert main(args) == 0, 'Initial product build failed'
        with ExecutionBroker().transaction(f"sqlite:///{database}") as conn:
            assert conn.exec_driver_sql('SELECT source_device_id FROM device_inventory').fetchall() == [('d1',)]
            conn.exec_driver_sql("INSERT INTO input_devices VALUES ('d1', 'm2')")
        assert main(args) == 1, 'Duplicate canonical key should fail quality gate'
        with ExecutionBroker().transaction(f"sqlite:///{database}") as conn:
            assert conn.exec_driver_sql('SELECT source_device_id FROM device_inventory').fetchall() == [('d1',)]
        reports = [json.loads(path.read_text()) for path in output.glob('*/run-manifest.json')]
        assert sorted(report['status'] for report in reports) == ['failed', 'passed']
        failed = next(report for report in reports if report['status'] == 'failed')
        assert not failed['publication'] and any(not item['passed'] for item in failed['tests'])
        print('PASS: native SQLite build, quality failure, run reports and original publication preserved')


if __name__ == '__main__':
    run_demo()
