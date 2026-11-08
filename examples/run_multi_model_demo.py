"""Live SQLGlot -> SQLFluff -> SQLAlchemy two-table product acceptance test.

Run after `python -m pip install -e .`.
Proves a valid Merchant+Device build followed by a failed-quality rerun that
leaves both prior published views intact. All DB I/O goes through SQLAlchemy.
"""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from cade.brokers.execution_broker import ExecutionBroker
from cade.brokers.file_broker import FileBroker
from cade.services.specification_service import SpecificationService
from cade.services.product_service import ProductService
from cade.services.project_service import ProjectService
from cade.services.build_workflow_service import BuildWorkflowService

HERE = Path(__file__).resolve().parent


def run_demo() -> None:
    with TemporaryDirectory() as folder:
        database = Path(folder) / 'merchant_operations.sqlite'
        database.touch()
        url = f'sqlite:///{database}'
        os.environ['DPC_EXECUTION_DATABASE_URL'] = url
        with ExecutionBroker().transaction(url) as connection:
            connection.exec_driver_sql('CREATE TABLE raw_merchants '
                '(merchant_id TEXT NOT NULL, merchant_name TEXT)')
            connection.exec_driver_sql('CREATE TABLE raw_devices '
                '(device_id TEXT NOT NULL, merchant_id TEXT NOT NULL, device_status TEXT NOT NULL)')
            connection.exec_driver_sql("INSERT INTO raw_merchants VALUES ('m1','Merchant One')")
            connection.exec_driver_sql("INSERT INTO raw_devices VALUES ('d1','m1','active')")
        files = FileBroker()
        specs = SpecificationService(files)
        products = ProductService(files, specs)
        projects = ProjectService(files, specs, products)
        workflow = BuildWorkflowService(files, products, projects)
        path = HERE / 'multi_model' / 'product.yaml'
        output = Path(folder) / 'generated'
        first = workflow.build(path, output=output, dialect='sqlite')
        assert first['status'] == 'passed', first['manifest'].get('error')
        assert first['products'] == ['merchant_operations.merchants', 'merchant_operations.devices']
        with ExecutionBroker().transaction(url) as connection:
            merchants = connection.exec_driver_sql('SELECT merchant_id, merchant_name FROM canonical_merchants').fetchall()
            devices = connection.exec_driver_sql('SELECT device_id, merchant_id, merchant_name FROM canonical_devices').fetchall()
            assert merchants == [('m1', 'Merchant One')], merchants
            assert devices == [('d1', 'm1', 'Merchant One')], devices
            # Deliberately violate a source record key to trigger the native quality gate.
            connection.exec_driver_sql("INSERT INTO raw_devices VALUES ('d1','m1','inactive')")
        second = workflow.build(path, output=output, dialect='sqlite')
        assert second['status'] == 'failed', second['manifest']
        assert second['manifest']['tests'], second['manifest']
        with ExecutionBroker().transaction(url) as connection:
            assert connection.exec_driver_sql('SELECT device_id,merchant_name FROM canonical_devices').fetchall() == [
                ('d1', 'Merchant One')], 'Failed quality gate replaced the published device table'
            assert connection.exec_driver_sql('SELECT merchant_id FROM canonical_merchants').fetchall() == [
                ('m1',)], 'Failed build lost the previously published merchant table'
        print('PASS: two canonical tables published in order; failing rerun preserved prior views')


if __name__ == '__main__':
    run_demo()
