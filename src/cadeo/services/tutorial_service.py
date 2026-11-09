"""Local sample data and contracts for the CLI and editor tutorial."""

import copy
import os
from pathlib import Path
import shlex

import yaml
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from cadeo.brokers.execution_broker import ExecutionBroker
from cadeo.brokers.file_broker import FileBroker
from cadeo.services.product_service import ProductService
from cadeo.services.specification_service import SpecificationService


class TutorialService:
    ENV = 'CADEO_TUTORIAL_DATABASE_URL'
    CASES = ('wrong-key', 'wrong-status', 'inner-join')
    RESOURCES = ('product.schema.json', 'README.md', 'tutorial.sqlite',
                 'starter/product.yaml', 'product/product.yaml', 'wrong-key/product.yaml',
                 'wrong-status/product.yaml', 'inner-join/product.yaml')

    def __init__(self):
        self.files = FileBroker()

    def ready(self, root: Path) -> bool:
        if not all((root / resource).is_file() for resource in self.RESOURCES):
            return False
        try:
            with ExecutionBroker().transaction(f'sqlite:///{root.resolve() / "tutorial.sqlite"}') as connection:
                return all(inspect(connection).has_table(name)
                           for name in ('input_devices', 'input_merchants', 'input_services'))
        except (SQLAlchemyError, ValueError, OSError):
            return False

    def activate(self, root: Path) -> str:
        database = root.resolve() / 'tutorial.sqlite'
        if not database.is_file():
            raise FileNotFoundError('Create the tutorial first: cadeo tutorial init')
        url = f'sqlite:///{database}'
        os.environ[self.ENV] = url
        return url

    @staticmethod
    def dataset(table, alias, columns, key, owner):
        return {'connection': 'local', 'table': table, 'alias': alias,
                'columns': {name: {'type': 'string', 'nullable': False} for name in columns},
                'record_key_columns': [key],
                'contract': {'owner': owner, 'freshness_hours': 24}}

    def document(self, starter=False):
        entities = {}
        mappings = {}
        assurance = {}
        targets = {}
        datasets = {'devices': self.dataset('input_devices', 'd',
                    ('device_id', 'merchant_id', 'service_id', 'status'), 'device_id', 'Operations')}

        def model(name, source, fields, key, grain):
            entities[name] = {'entity_name': name.title(), 'record_grain': grain,
                'unique_key_attributes': [key], 'attributes': {
                    field: {'type': 'string', 'nullable': field != key,
                            'requirement_ids': ['REQ_001']} for field in fields}}
            mappings[name] = {'source': source, 'fields': fields}
            assurance[name] = {'reconciliation': {'allow': []}, 'preservation': {'mode': 'snapshot'}}
            targets[name] = {'table': f'tutorial_{name}'}

        if starter:
            model('device', 'devices', {'device_id': {'column': 'd.device_id'}},
                  'device_id', 'One row per device ID in this source system')
        else:
            datasets['merchants'] = self.dataset('input_merchants', 'm',
                ('merchant_id', 'merchant_name'), 'merchant_id', 'Merchant Operations')
            datasets['services'] = self.dataset('input_services', 's',
                ('service_id', 'service_name'), 'service_id', 'Service Operations')
            model('merchant', 'merchants', {'merchant_id': {'column': 'm.merchant_id'},
                'merchant_name': {'column': 'm.merchant_name'}}, 'merchant_id', 'One row per merchant ID')
            model('service', 'services', {'service_id': {'column': 's.service_id'},
                'service_name': {'column': 's.service_name'}}, 'service_id', 'One row per service ID')
            datasets['canonical_merchants'] = {'model_ref': 'merchant', 'alias': 'cm'}
            datasets['canonical_services'] = {'model_ref': 'service', 'alias': 'cs'}
            model('connection', 'devices', {
                'connection_id': {'column': 'd.device_id'},
                'merchant_id': {'column': 'd.merchant_id'},
                'service_id': {'column': 'd.service_id'},
                'merchant_name': {'column': 'cm.merchant_name'},
                'service_name': {'column': 'cs.service_name'},
                'connection_status': {'case': {'when': [{'if': {'eq': [
                    {'column': 'd.status'}, {'literal': 'LIVE'}]}, 'then': {'literal': 'active'}}],
                    'else': {'literal': 'inactive'}}}},
                'connection_id', 'One current device-to-merchant-to-service connection per device ID')
            entities['connection']['relationships'] = [
                {'attribute': 'merchant_id', 'entity': 'Merchant', 'related_attribute': 'merchant_id',
                 'cardinality': 'many_to_one'},
                {'attribute': 'service_id', 'entity': 'Service', 'related_attribute': 'service_id',
                 'cardinality': 'many_to_one'},
            ]
            mappings['connection']['joins'] = [
                {'type': 'left', 'source': source, 'expect_unique': True, 'on': [
                    {'left': {'column': f'd.{key}'}, 'right': {'column': f'{alias}.{key}'}}]}
                for source, alias, key in (('canonical_merchants', 'cm', 'merchant_id'),
                                           ('canonical_services', 'cs', 'service_id'))]
            assurance['connection']['tests'] = {
                'status_codes': {'type': 'accepted_values', 'column': 'connection_status',
                                 'values': ['active', 'inactive']},
                'owner_examples': {'type': 'expected_rows', 'inputs': {
                    'devices': [
                        {'device_id': 'D1', 'merchant_id': 'M1', 'service_id': 'S1', 'status': 'LIVE'},
                        {'device_id': 'D2', 'merchant_id': 'M1', 'service_id': 'S1', 'status': 'OFF'},
                        {'device_id': 'D3', 'merchant_id': 'M2', 'service_id': 'S9', 'status': 'LIVE'}],
                    'canonical_merchants': [
                        {'merchant_id': 'M1', 'merchant_name': 'Harbour Foods'},
                        {'merchant_id': 'M2', 'merchant_name': 'City Pharmacy'}],
                    'canonical_services': [{'service_id': 'S1', 'service_name': 'Card payments'}]},
                    'expected': [
                        {'connection_id': 'D1', 'merchant_id': 'M1', 'service_id': 'S1',
                         'merchant_name': 'Harbour Foods', 'service_name': 'Card payments', 'connection_status': 'active'},
                        {'connection_id': 'D2', 'merchant_id': 'M1', 'service_id': 'S1',
                         'merchant_name': 'Harbour Foods', 'service_name': 'Card payments', 'connection_status': 'inactive'},
                        {'connection_id': 'D3', 'merchant_id': 'M2', 'service_id': 'S9',
                         'merchant_name': 'City Pharmacy', 'service_name': None, 'connection_status': 'active'}]}}
        doc = {'schema': {'path': 'product.schema.json', 'version': '0.8.5'}, 'spec_version': '0.8.5',
            'definition': {'name': 'connection_tutorial',
                'description': 'Current payment connections for service support',
                'owner': {'team': 'Service Operations', 'contact': 'service-owner@example.org'},
                'consumers': [{'id': 'support', 'purpose': 'Find active connections with no service record', 'access': 'read'}],
                'outcome': 'Keep every device visible. Route missing service records to support.',
                'requirements': [{'id': 'REQ_001', 'statement': 'Preserve one row per device. LIVE means active. Keep missing service records visible.'}],
                'features': [{'id': 'FEAT_001', 'description': 'Publish a shared connection model',
                    'requirement_ids': ['REQ_001'], 'acceptance_criteria': [
                        {'id': 'AC_001', 'statement': 'Keys, row counts, and owner examples pass.', 'check_ids': []}]}]},
            'model': {'entities': entities}, 'sources': {'connections': {'local': {'url_env': self.ENV}}, 'datasets': datasets},
            'mapping': {'entities': mappings}, 'assurance': {'quality': {'on_failure': 'block'}, 'entities': assurance},
            'delivery': {'dialect': 'sqlite', 'output': 'generated', 'execution': {'connection': 'local'},
                'refresh': {'schedule': '0 5 * * *', 'timezone': 'UTC'},
                'recovery': {'strategy': 'rerun', 'max_attempts': 1, 'runbook': '../README.md'}, 'targets': targets}}
        check_ids = []
        for name in entities:
            check_ids.extend([f'{name}/not_null:{entities[name]["unique_key_attributes"][0]}',
                              f'{name}/canonical_key_unique', f'{name}/reconciliation'])
        check_ids.append('device/snapshot:0:d' if starter else 'merchant/snapshot:0:m')
        if not starter:
            check_ids.extend(['service/snapshot:0:s', 'connection/snapshot:0:d',
                'connection/snapshot:0:cm', 'connection/snapshot:0:cs',
                'connection/join_unique:0:canonical_merchants',
                'connection/join_unique:0:canonical_services', 'connection/status_codes',
                'connection/owner_examples'])
        doc['definition']['features'][0]['acceptance_criteria'][0]['check_ids'] = check_ids
        return doc

    def create(self, root: Path) -> dict:
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        schema = self.files.read_bundled_text('schemas/product.schema.json')
        if not self.files.exists(root / 'product.schema.json'):
            self.files.write_text(root / 'product.schema.json', schema, overwrite=False)
        full = self.document()
        variants = {'starter': self.document(starter=True), 'product': full}
        for case in self.CASES:
            wrong = copy.deepcopy(full)
            mapping = wrong['mapping']['entities']['connection']
            if case == 'wrong-key':
                mapping['fields']['connection_id'] = {'column': 'd.merchant_id'}
            elif case == 'wrong-status':
                mapping['fields']['connection_status']['case']['when'][0]['if']['eq'][1]['literal'] = 'OFF'
            else:
                mapping['joins'][1]['type'] = 'inner'
            variants[case] = wrong
        products = ProductService(self.files, SpecificationService(self.files))
        for name, doc in variants.items():
            path = root / name / 'product.yaml'
            doc['schema']['path'] = '../product.schema.json'
            if not self.files.exists(path):
                self.files.write_text(path, yaml.safe_dump(doc, sort_keys=False), overwrite=False)
            products.validate(path)
        url = f'sqlite:///{root / "tutorial.sqlite"}'
        if not self.files.exists(root / 'tutorial.sqlite'):
            self.files.write_text(root / 'tutorial.sqlite', '', overwrite=False)
        tables = (
            ('input_devices',
             'CREATE TABLE input_devices (device_id TEXT NOT NULL, merchant_id TEXT NOT NULL, service_id TEXT NOT NULL, status TEXT NOT NULL)',
             "INSERT INTO input_devices VALUES ('D1','M1','S1','LIVE'), ('D2','M1','S1','OFF'), ('D3','M2','S9','LIVE')"),
            ('input_merchants',
             'CREATE TABLE input_merchants (merchant_id TEXT NOT NULL, merchant_name TEXT NOT NULL)',
             "INSERT INTO input_merchants VALUES ('M1','Harbour Foods'), ('M2','City Pharmacy')"),
            ('input_services',
             'CREATE TABLE input_services (service_id TEXT NOT NULL, service_name TEXT NOT NULL)',
             "INSERT INTO input_services VALUES ('S1','Card payments')"),
        )
        with ExecutionBroker().transaction(url) as connection:
            for name, definition, seed in tables:
                if not inspect(connection).has_table(name):
                    connection.exec_driver_sql(definition)
                    connection.exec_driver_sql(seed)
        guide = self.files.read_bundled_text('templates/tutorial.md')
        if not self.files.exists(root / 'README.md'):
            self.files.write_text(root / 'README.md', guide, overwrite=False)
        self.activate(root)
        return {'root': str(root), 'starter': 'starter/product.yaml', 'product': 'product/product.yaml',
                'environment': f'export {self.ENV}={shlex.quote(url)}', 'guide': guide}

    def exercise(self, root: Path, case: str) -> dict:
        if case not in self.CASES:
            raise ValueError('Choose wrong-key, wrong-status, or inner-join')
        before = self.publication(root)
        from cadeo.services.build_workflow_service import BuildWorkflowService
        from cadeo.services.project_service import ProjectService
        products = ProductService(self.files, SpecificationService(self.files))
        projects = ProjectService(self.files, SpecificationService(self.files), products)
        result = BuildWorkflowService(self.files, products, projects).build(root / case / 'product.yaml')
        failed = [test['id'] for test in result['manifest']['tests'] if test['status'] == 'failed']
        expected = {'wrong-key': {'canonical_key_unique', 'owner_examples'},
                    'wrong-status': {'owner_examples'}, 'inner-join': {'reconciliation', 'owner_examples'}}
        after = self.publication(root)
        return {**result, 'expected_failure': result['status'] == 'failed' and bool(set(failed) & expected[case]),
                'failed_checks': failed, 'publication_unchanged': before == after, 'support_queue': self.report(root)}

    def publication(self, root: Path) -> dict:
        with ExecutionBroker().transaction(self.activate(root)) as connection:
            if not all(inspect(connection).has_table(f'tutorial_{name}') for name in ('merchant', 'service', 'connection')):
                raise ValueError('Build the full product first. Use step 4 in Tutorial or cadeo build <tutorial-path>/product/product.yaml.')
            return {name: [tuple(row) for row in connection.exec_driver_sql(
                f'SELECT * FROM tutorial_{name} ORDER BY {key}').fetchall()]
                for name, key in (('merchant', 'merchant_id'), ('service', 'service_id'),
                                  ('connection', 'connection_id'))}

    def report(self, root: Path) -> dict:
        with ExecutionBroker().transaction(self.activate(root)) as connection:
            if not inspect(connection).has_table('tutorial_connection'):
                raise ValueError('Build the full product first. Use step 4 in Tutorial or cadeo build <tutorial-path>/product/product.yaml.')
            rows = connection.exec_driver_sql(
                'SELECT connection_id, merchant_name, service_id FROM tutorial_connection '
                "WHERE connection_status = 'active' AND service_name IS NULL ORDER BY connection_id").fetchall()
        return {'action_queue': [dict(row._mapping) for row in rows],
                'meaning': 'Route D3 at City Pharmacy to Service Operations. Check service S9. Do not mark the device inactive.'}