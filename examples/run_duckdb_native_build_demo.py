"""End-to-end DuckDB build/rollback demonstration: SQLGlot -> SQLFluff -> SQLAlchemy.

Install `pip install -e ".[duckdb]"` and run this file. The database is disposable.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
from sqlalchemy import create_engine

from cadeo.exposers.cli import main

HERE = Path(__file__).resolve().parent


def run_demo():
    with TemporaryDirectory() as folder:
        directory = Path(folder)
        database = directory/'devices.duckdb'
        url = f'duckdb:///{database}'
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    'CREATE TABLE input_devices(asset_id VARCHAR NOT NULL, merchant_account_number VARCHAR)')
                conn.exec_driver_sql("INSERT INTO input_devices VALUES ('d1', 'm1')")
        finally:
            engine.dispose()
        os.environ['DPC_EXECUTION_DATABASE_URL'] = url
        args = ['build', str(HERE/'duckdb_devices.yaml'), '--out', str(directory/'build')]
        assert main(args) == 0, 'DuckDB initial native build failed'
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                assert conn.exec_driver_sql(
                    'SELECT source_device_id FROM device_inventory').fetchall() == [('d1',)]
                conn.exec_driver_sql("INSERT INTO input_devices VALUES ('d1', 'm2')")
        finally:
            engine.dispose()
        assert main(args) == 1, 'Duplicate source key must fail the quality gate'
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                assert conn.exec_driver_sql(
                    'SELECT source_device_id FROM device_inventory').fetchall() == [('d1',)]
        finally:
            engine.dispose()
        reports = [json.loads(p.read_text()) for p in (directory/'build').rglob('run-manifest.json')]
        assert {item['status'] for item in reports} == {'passed','failed'}
        print('PASS: DuckDB SQLGlot/SQLFluff compilation, SQLAlchemy execution and safe rollback')


if __name__ == '__main__':
    run_demo()
