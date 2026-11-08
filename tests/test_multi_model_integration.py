"""Dependency-enabled integration: real transpilation and SQLAlchemy publication."""
import pytest


@pytest.mark.parametrize('partial_database', [False, True])
def test_tutorial_create_resumes_without_overwriting(tmp_path, partial_database):
    import json
    from cade.services.tutorial_service import TutorialService
    from tests.support import sqlalchemy_connection

    service = TutorialService()
    root = tmp_path / 'tutorial'
    (root / 'starter').mkdir(parents=True)
    if partial_database:
        with sqlalchemy_connection(root / 'tutorial.sqlite') as connection:
            connection.exec_driver_sql('CREATE TABLE input_devices (device_id TEXT, merchant_id TEXT, service_id TEXT, status TEXT)')
            connection.exec_driver_sql("INSERT INTO input_devices VALUES ('CUSTOM', 'M1', 'S1', 'LIVE')")
    assert not service.ready(root)
    service.create(root)
    assert service.ready(root)
    document = root / 'product/product.yaml'
    document.write_text(document.read_text() + '\n# My edited tutorial contract\n')
    schema = root / 'product.schema.json'
    content = json.loads(schema.read_text())
    content['title'] = 'My customized tutorial schema'
    schema.write_text(json.dumps(content))
    guide = root / 'README.md'
    guide.write_text('My tutorial notes\n')
    review = root / 'owner-review.json'
    review.write_text('{"reviewer": "Owner"}\n')
    original = {path: path.read_bytes() for path in (document, schema, guide, review)}
    missing = root / 'wrong-status/product.yaml'
    missing.unlink()
    assert not service.ready(root)
    service.create(root)
    service.create(root)
    assert missing.is_file()
    assert service.ready(root)
    assert all(path.read_bytes() == contents for path, contents in original.items())
    with sqlalchemy_connection(root / 'tutorial.sqlite') as connection:
        rows = connection.exec_driver_sql('SELECT device_id FROM input_devices ORDER BY device_id').fetchall()
        assert [row[0] for row in rows] == (['CUSTOM'] if partial_database else ['D1', 'D2', 'D3'])
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM input_merchants').scalar_one() == 2
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM input_services').scalar_one() == 1


def test_tutorial_init_endpoint_recovers_partial_example(tmp_path):
    from cade.exposers.web import create_app
    from cade.services.tutorial_service import TutorialService

    root = tmp_path / 'tutorial'
    (root / 'starter').mkdir(parents=True)
    (root / 'tutorial.sqlite').touch()
    client = create_app(tmp_path).test_client()
    token = client.get('/api/bootstrap').get_json()['token']
    assert client.get('/api/tutorial').get_json()['ready'] is False
    for _ in range(2):
        response = client.post('/api/tutorial', json={'action': 'init'},
                               headers={'X-Editor-Token': token})
        assert response.status_code == 200, response.get_json()
        assert client.get('/api/tutorial').get_json()['ready'] is True
    (root / 'starter/product.yaml').unlink()
    assert client.get('/api/tutorial').get_json()['ready'] is False
    response = client.post('/api/tutorial', json={'action': 'init'}, headers={'X-Editor-Token': token})
    assert response.status_code == 200
    assert TutorialService().ready(root)


@pytest.mark.parametrize('document', [None, [], {'schema': {'path': 'missing.schema.json', 'version': '0.8.0'}}])
def test_check_registry_rejection_logs_reason(tmp_path, caplog, document):
    from cade.exposers.web import create_app

    client = create_app(tmp_path).test_client()
    token = client.get('/api/bootstrap').get_json()['token']
    payload = [] if document == [] else {'path': 'broken.yaml', 'document': document}
    response = client.post('/api/check-registry', json=payload, headers={'X-Editor-Token': token})
    assert response.status_code == 422
    assert response.get_json()['error'] in caplog.text
    assert 'Check registry unavailable for' in caplog.text
    if document:
        assert 'broken.yaml' in caplog.text and 'missing.schema.json' in caplog.text


@pytest.mark.parametrize('directory', [False, True])
def test_standalone_sql_test_pack_export(tmp_path, directory):
    import json
    from pathlib import Path
    from cade.exposers.cli import main
    from cade.brokers.file_broker import FileBroker
    from cade.services.product_service import ProductService
    from cade.services.specification_service import SpecificationService
    from cade.services.sql_service import SqlTemplateService
    from tests.support import write_document

    files = FileBroker()
    service = ProductService(files, SpecificationService(files))
    example = Path(__file__).resolve().parents[1] / 'examples/sqlite_business_tests.yaml'
    source = tmp_path / 'source/product.yaml'
    write_document(source, service.specifications.read_document(example))
    spec = service.validate(source).models[0]
    output = tmp_path / 'generated'
    argument = source.parent if directory else source
    base = ['compile', str(argument), '--out', str(output), '--dialect', 'sqlite']
    assert main(base) == 0
    assert not list(output.rglob('manifest.json'))
    assert main(base + ['--test-pack', 'sql']) == 0
    manifest_path, = output.rglob('manifest.json')
    manifest = json.loads(manifest_path.read_text())
    assert manifest_path.parent == output / spec.product.name.rsplit('.', 1)[0] / 'tests' / spec.product.name
    assert [check['id'] for check in manifest['checks']] == [
        identifier for identifier, _ in SqlTemplateService().check_statements(spec)]
    assert manifest['dialect'] == 'sqlite'
    for check in manifest['checks']:
        sql = (manifest_path.parent / check['file']).read_text()
        assert '__stage' not in sql
        assert sql.strip().endswith(';')
    assert main(base + ['--test-pack', 'none']) == 0
    assert manifest_path.is_file()
    with pytest.raises(Exception, match='Unknown test-pack exporter'):
        service.compile(source, output, test_pack='dbt')


@pytest.mark.parametrize('malformed_sql', [
    'SELECT NULL AS failures;', 'SELECT -1 AS failures;',
    'SELECT 0.5 AS failures;', 'SELECT 0 AS failures WHERE 1 = 0;',
    'SELECT 0 AS failures UNION ALL SELECT 0 AS failures;',
    'SELECT 0 AS unexpected;', 'SELECT 0 AS failures, 0 AS extra;',
])
def test_standalone_sql_test_pack_runner(tmp_path, malformed_sql):
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    from sqlalchemy import create_engine
    from cade.brokers.file_broker import FileBroker
    from cade.services.product_service import ProductService
    from cade.services.specification_service import SpecificationService

    files = FileBroker()
    service = ProductService(files, SpecificationService(files))
    example = Path(__file__).resolve().parents[1] / 'examples/sqlite_business_tests.yaml'
    service.compile(example, tmp_path / 'generated', dialect='sqlite', test_pack='sql')
    manifest_path, = (tmp_path / 'generated').rglob('manifest.json')
    runner = manifest_path.parent / 'run.py'
    database = tmp_path / 'test.sqlite'
    environment = {**os.environ, 'PACK_DATABASE_URL': f'sqlite:///{database}'}

    def run(*arguments):
        completed = subprocess.run([sys.executable, '-I', str(runner),
                                    '--url-env', 'PACK_DATABASE_URL', *arguments],
                                   cwd=tmp_path, env=environment, capture_output=True, text=True)
        return completed.returncode, json.loads(completed.stdout)

    code, report = run('--kind', 'unit')
    assert code == 0 and report['passed']
    assert report['checks'][0]['id'] == 'merchant_mapping_fixture'
    code, report = run()
    assert code == 1 and not report['passed']
    assert any('error' in check for check in report['checks'])
    engine = create_engine(environment['PACK_DATABASE_URL'])
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE input_devices (asset_id TEXT, merchant_account_number TEXT, device_status TEXT)')
        connection.exec_driver_sql('CREATE TABLE input_merchants (merchant_account_number TEXT, merchant_name TEXT)')
        connection.exec_driver_sql("INSERT INTO input_devices VALUES ('d1', 'm1', 'active')")
        connection.exec_driver_sql("INSERT INTO input_merchants VALUES ('m1', 'Merchant One')")
        connection.exec_driver_sql('CREATE TABLE device_business_tests AS SELECT asset_id AS source_device_id, input_devices.merchant_account_number, device_status, merchant_name FROM input_devices LEFT JOIN input_merchants USING (merchant_account_number)')
    code, report = run()
    assert code == 0 and report['passed']
    with engine.begin() as connection:
        connection.exec_driver_sql("UPDATE device_business_tests SET device_status = 'invalid'")
    code, report = run('--kind', 'data')
    assert code == 1
    assert next(check for check in report['checks'] if check['id'] == 'device_status_valid')['failures'] == 1
    manifest = json.loads(manifest_path.read_text())
    unit = next(check for check in manifest['checks'] if check['kind'] == 'unit')
    (manifest_path.parent / unit['file']).write_text(malformed_sql)
    code, report = run('--kind', 'unit')
    assert code == 1 and 'error' in report['checks'][0]
    engine.dispose()


@pytest.mark.parametrize('dialect', ['sqlite', 'postgres', 'duckdb', 'spark'])
def test_standalone_sql_test_pack_dialects(tmp_path, dialect):
    import json
    from pathlib import Path
    from sqlglot import exp, parse
    from cade.brokers.file_broker import FileBroker
    from cade.services.product_service import ProductService
    from cade.services.specification_service import SpecificationService
    from cade.services.dialect_service import DialectService
    from cade.services.test_pack_service import TestPackService

    files = FileBroker()
    service = ProductService(files, SpecificationService(files))
    example = Path(__file__).resolve().parents[1] / 'examples/sqlite_business_tests.yaml'
    spec = service.validate(example).models[0]
    artifacts = TestPackService(files).render(spec, tmp_path, DialectService.create(dialect))
    for path, script in artifacts.items():
        if path.suffix == '.sql':
            statement, = parse(script, read=dialect)
            assert isinstance(statement, exp.Query)
    unit = next(check for check in json.loads(next(
        script for path, script in artifacts.items() if path.name == 'manifest.json'))['checks']
                if check['kind'] == 'unit')
    unit_sql = next(script for path, script in artifacts.items() if path.name == unit['file'])
    assert 'input_devices' not in unit_sql and 'input_merchants' not in unit_sql


def test_standalone_sql_test_pack_cross_product(tmp_path):
    import json
    from pathlib import Path
    from cade.brokers.file_broker import FileBroker
    from cade.services.product_service import ProductService
    from cade.services.project_service import ProjectService
    from cade.services.specification_service import SpecificationService

    files = FileBroker()
    specifications = SpecificationService(files)
    products = ProductService(files, specifications)
    projects = ProjectService(files, specifications, products)
    source = Path(__file__).resolve().parents[1] / 'examples/cross_product'
    project = projects.load(source)
    _, artifacts = projects.build(project, output=tmp_path, dialect='sqlite', test_pack='sql')
    manifests = [path for path in artifacts if path.name == 'manifest.json']
    assert len(manifests) == len(project.products)
    assert {json.loads(path.read_text())['product'] for path in manifests} == {
        name for name, _, _ in project.products}


def test_standalone_sql_test_pack_multi_model(tmp_path):
    import copy
    import json
    from pathlib import Path
    from cade.brokers.file_broker import FileBroker
    from cade.services.product_service import ProductService
    from cade.services.specification_service import SpecificationService
    from tests.support import write_document

    files = FileBroker()
    service = ProductService(files, SpecificationService(files))
    example = Path(__file__).resolve().parents[1] / 'examples/sqlite_business_tests.yaml'
    document = service.specifications.read_document(example)
    for stage in ('model', 'mapping', 'assurance'):
        document[stage]['entities']['second'] = copy.deepcopy(document[stage]['entities']['device'])
    document['delivery']['targets']['second'] = {'table': 'second_devices'}
    source = tmp_path / 'product.yaml'
    write_document(source, document)
    artifacts = service.compile(source, tmp_path / 'generated', dialect='sqlite', test_pack='sql')
    manifests = [path for path in artifacts if path.name == 'manifest.json']
    assert len(manifests) == 2
    assert {json.loads(path.read_text())['target'] for path in manifests} == {
        'device_business_tests', 'second_devices'}
    assert len({path.parent for path in manifests}) == 2


def test_ui_creates_missing_workspace(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from flask import Flask
    from cade.exposers.cli import main
    from cade.services.schema_catalog_service import SchemaCatalogService

    run = Mock()
    monkeypatch.setattr(Flask, 'run', run)
    root = tmp_path / 'nested/test'
    assert not root.exists()
    assert main(['ui', str(root), '--port', '8765']) == 0
    assert root.is_dir()
    assert (root / SchemaCatalogService.DEFAULT).is_file()
    run.assert_called_once_with(host='127.0.0.1', port=8765, debug=False, use_reloader=False)


@pytest.mark.parametrize('overwrite', [False, True])
def test_ui_preserves_files_unless_schema_overwrite_requested(tmp_path, monkeypatch, overwrite):
    import json
    from unittest.mock import Mock
    from flask import Flask
    from cade.brokers.file_broker import FileBroker
    from cade.exposers.cli import main
    from cade.exposers.web import create_app
    from cade.services.schema_catalog_service import SchemaCatalogService

    create_app(tmp_path)
    schema_path = tmp_path / SchemaCatalogService.DEFAULT
    schema = json.loads(schema_path.read_text())
    schema['title'] = 'My customized workspace schema'
    schema_path.write_text(json.dumps(schema))
    original_schema = schema_path.read_bytes()
    product = tmp_path / 'product.yaml'
    product.write_text('# My existing draft\n')
    database = tmp_path / 'local.sqlite'
    database.write_bytes(b'Existing database contents')
    run = Mock()
    monkeypatch.setattr(Flask, 'run', run)
    arguments = ['ui', str(tmp_path)] + (['--overwrite'] if overwrite else [])
    assert main(arguments) == 0
    assert product.read_bytes() == b'# My existing draft\n'
    assert database.read_bytes() == b'Existing database contents'
    if overwrite:
        assert schema_path.read_text() == FileBroker().read_bundled_text('schemas/product.schema.json')
    else:
        assert schema_path.read_bytes() == original_schema
    run.assert_called_once()


@pytest.mark.parametrize('overwrite', [False, True])
def test_ui_invalid_schema_requires_explicit_overwrite(tmp_path, monkeypatch, overwrite):
    import json
    from unittest.mock import Mock
    from flask import Flask
    from cade.exposers.cli import main
    from cade.exposers.web import create_app
    from cade.services.schema_catalog_service import SchemaCatalogService

    create_app(tmp_path)
    schema_path = tmp_path / SchemaCatalogService.DEFAULT
    schema_path.write_text('Invalid schema contents')
    run = Mock()
    monkeypatch.setattr(Flask, 'run', run)
    if overwrite:
        assert main(['ui', str(tmp_path), '--overwrite']) == 0
        assert json.loads(schema_path.read_text())['type'] == 'object'
        run.assert_called_once()
    else:
        with pytest.raises(SystemExit) as error:
            main(['ui', str(tmp_path)])
        assert error.value.code == 2
        assert schema_path.read_text() == 'Invalid schema contents'
        run.assert_not_called()


def test_ui_rejects_file_workspace_without_overwriting(tmp_path):
    from cade.exposers.cli import main

    path = tmp_path / 'test'
    path.write_text('Keep this file')
    with pytest.raises(SystemExit) as error:
        main(['ui', str(path), '--overwrite'])
    assert error.value.code == 2
    assert path.read_text() == 'Keep this file'


def test_init_and_editor_versions_match_app(tmp_path):
    import json
    from cade.brokers.file_broker import FileBroker
    from cade.exposers.cli import VERSION
    from cade.exposers.web import create_app
    from cade.models.specification import SpecificationValidationError
    from cade.services.product_service import ProductService
    from cade.services.schema_catalog_service import SchemaCatalogService
    from cade.services.specification_service import SpecificationService

    files = FileBroker()
    specifications = SpecificationService(files)
    products = ProductService(files, specifications)
    path, schema_path = products.init(tmp_path / 'product.yaml')
    bundle = products.validate(path)
    assert schema_path.name == f'product.schema-v{VERSION}.json'
    assert bundle.definition['spec_version'] == VERSION
    schema = json.loads(schema_path.read_text())
    assert schema['x-schema-version'] == VERSION
    document = specifications.read_document(path)
    assert document['schema']['version'] == VERSION
    document['spec_version'] = '0.8.0'
    with pytest.raises(SpecificationValidationError):
        specifications.parse(document)
    client = create_app(tmp_path).test_client()
    assert client.get('/api/bootstrap').get_json()['version'] == VERSION
    installed_schema = json.loads((tmp_path / SchemaCatalogService.DEFAULT).read_text())
    assert installed_schema['x-schema-version'] == VERSION


def test_generated_product_layout(tmp_path):
    from pathlib import Path
    from unittest.mock import Mock
    from cade.brokers.file_broker import FileBroker
    from cade.services.specification_service import SpecificationService
    from cade.services.product_service import ProductService
    from cade.services.dependency_service import DependencyService

    files = FileBroker()
    service = ProductService(files, SpecificationService(files))
    example = Path(__file__).resolve().parents[1] / 'examples/sqlite_devices.yaml'
    bundle = service.validate(example)
    spec = bundle.models[0]
    destination = service.output_directory(example, spec, None, tmp_path, [])
    assert destination == tmp_path / bundle.name
    sql = Mock()
    for method in ('generate_preservation', 'generate', 'generate_audit',
                   'generate_checks', 'generate_publication'):
        getattr(sql, method).return_value = 'SELECT 1;\n'
    artifacts = service._render(spec, destination, sql)
    for node in DependencyService().build(spec).nodes:
        if node.artifact:
            assert destination / node.artifact in artifacts
            assert node.artifact.startswith('sql/')
    assert {path.relative_to(destination).parts[0] for path in artifacts} == {
        'sql', 'metadata', 'plans', 'lineage', 'documentation'}
    import json
    from cade.services.traceability_service import TraceabilityService
    plan_path = destination / 'plans' / f'{spec.product.name}.plan.json'
    for node in json.loads(artifacts[plan_path])['nodes']:
        if node['artifact']:
            assert (plan_path.parent / node['artifact']).resolve() in {
                path.resolve() for path in artifacts}
    trace = TraceabilityService().build(spec)
    for field in trace['fields']:
        assert all(destination / asset in artifacts for asset in field['assets'])


@pytest.mark.parametrize('dialect', ['spark', 'sqlite', 'postgres', 'duckdb'])
def test_generated_sql_is_formatted_without_changing_statements(dialect):
    sqlglot = pytest.importorskip('sqlglot')
    pytest.importorskip('sqlfluff')
    from cade.services.sqlglot_service import SqlGlotSqlService
    script = (
        '/* Source snapshot */ CREATE TABLE gold.device_stage AS '
        'WITH selected AS (SELECT CAST(s.asset_id AS STRING) AS device_id, '
        "CASE WHEN s.asset_id IS NULL THEN 'N' ELSE 'V' END AS source_key "
        'FROM silver.devices AS s) SELECT device_id, source_key FROM selected;'
    )
    result = SqlGlotSqlService(dialect)._translate(script, 'formatting test')
    assert result.count('\n') >= 8
    assert '\n  SELECT' in result
    assert 'Source snapshot' in result
    assert result.endswith(';\n')
    assert sqlglot.transpile(result, read=dialect, write=dialect) == sqlglot.transpile(
        script, read='spark', write=dialect)


def test_real_sqlglot_sqlfluff_sqlalchemy_two_model_build():
    pytest.importorskip('sqlglot')
    pytest.importorskip('sqlfluff')
    from examples.run_multi_model_demo import run_demo
    run_demo()


def test_real_sqlfluff_multitable_generation(tmp_path):
    pytest.importorskip('sqlglot')
    pytest.importorskip('sqlfluff')
    from cade.brokers.file_broker import FileBroker
    from cade.services.specification_service import SpecificationService
    from cade.services.product_service import ProductService
    from pathlib import Path
    files=FileBroker()
    service=ProductService(files,SpecificationService(files))
    example=Path(__file__).resolve().parents[1]/'examples/multi_model/product.yaml'
    artifacts=service.compile(example,tmp_path,'sqlite')
    assert len([p for p in artifacts if p.name.endswith('.publish.sql')])==2
    assert (tmp_path/'merchant_operations/sql/transformations/merchant_operations.devices.sql').exists()
    assert (tmp_path/'merchant_operations/sql/transformations/merchant_operations.merchants.sql').exists()


def test_generated_multi_model_and_project_layout(tmp_path):
    import json
    from pathlib import Path
    from cade.brokers.file_broker import FileBroker
    from cade.services.specification_service import SpecificationService
    from cade.services.product_service import ProductService
    from cade.services.project_service import ProjectService
    from cade.services.tutorial_service import TutorialService
    from tests.support import write_document

    files = FileBroker()
    specifications = SpecificationService(files)
    products = ProductService(files, specifications)
    projects = ProjectService(files, specifications, products)
    path = tmp_path / 'specifications' / 'product.yaml'
    write_document(path, TutorialService().document())
    bundle = products.validate(path)
    output = tmp_path / 'generated'
    generated = products.compile(path, output, 'sqlite')
    assert len(bundle.models) > 1
    assert {artifact.relative_to(output).parts[0] for artifact in generated} == {bundle.name}
    for plan_path in (output / bundle.name / 'plans').glob('*.plan.json'):
        assert all((plan_path.parent / node['artifact']).is_file()
                   for node in json.loads(plan_path.read_text())['nodes'] if node['artifact'])

    example = Path(__file__).resolve().parents[1] / 'examples/cross_product'
    project = projects.load(example)
    output = tmp_path / 'project-generated'
    plan, generated = projects.build(project, output=output, dialect='sqlite', build_id='layout-test')
    for node in plan.nodes:
        if node.artifact:
            artifact = output / 'layout-test' / node.artifact
            assert artifact.is_file()
            assert artifact.resolve() in generated
    for name, spec_path, spec in project.products:
        destination = products.output_directory(spec_path, spec, project.root, output, [])
        assert destination == output / name.rsplit('.', 1)[0]
        assert (destination / 'runs/layout-test/sql/transformations' / f'{name}.sql').is_file()


@pytest.mark.parametrize('test_pack', [None, 'none', 'sql'])
@pytest.mark.parametrize('directory', [False, True])
def test_generated_layout_flask_compile_preview(tmp_path, test_pack, directory):
    from cade.exposers.web import create_app
    from cade.services.tutorial_service import TutorialService
    from tests.support import write_document

    document = TutorialService().document()
    write_document(tmp_path / 'product.yaml', document)
    client = create_app(tmp_path).test_client()
    token = client.get('/api/bootstrap').get_json()['token']
    headers = {'X-Editor-Token': token}
    payload = {'action': 'compile', 'path': '.' if directory else 'product.yaml',
               'out': 'generated', 'dialect': 'sqlite'}
    if test_pack is not None:
        payload['test_pack'] = test_pack
    response = client.post('/api/action', headers=headers, json=payload)
    assert response.status_code == 200, response.get_json()
    result = response.get_json()
    product = document['definition']['name']
    shared_plans = {'generated/project.plan.json', 'generated/project.plan.mmd'} if directory else set()
    assert all(path.startswith(f'generated/{product}/') or path in shared_plans
               for path in result['generated'])
    expected_kinds = {'preservation', 'transformation', 'reconciliation', 'quality checks', 'publication'}
    if test_pack == 'sql':
        expected_kinds.add('standalone test')
        assert len(result['test_packs']) == len(document['model']['entities'])
        for pack in result['test_packs']:
            assert (tmp_path / pack / 'manifest.json').is_file()
            assert (tmp_path / pack / 'run.py').is_file()
    else:
        assert result['test_packs'] == []
        assert not list(tmp_path.rglob('manifest.json'))
    assert {artifact['kind'] for artifact in result['artifacts']} == expected_kinds
    for artifact in result['artifacts']:
        assert artifact['product'].startswith(product + '.')
        assert artifact['dialect'] == 'sqlite'
        if artifact['kind'] == 'standalone test':
            import json
            check_path = tmp_path / artifact['path']
            manifest = json.loads((check_path.parent / 'manifest.json').read_text())
            assert artifact['check_id'] == next(check['id'] for check in manifest['checks']
                                               if check['file'] == check_path.name)
        preview = client.get('/api/artifact', query_string={'path': artifact['path']}, headers=headers)
        assert preview.status_code == 200
        assert preview.get_json()['content'] == (tmp_path / artifact['path']).read_text()


@pytest.mark.parametrize('test_pack', ['dbt', '', [], {}])
def test_flask_rejects_unknown_test_pack(tmp_path, test_pack):
    from cade.exposers.web import create_app
    from cade.services.tutorial_service import TutorialService
    from tests.support import write_document

    write_document(tmp_path / 'product.yaml', TutorialService().document())
    client = create_app(tmp_path).test_client()
    token = client.get('/api/bootstrap').get_json()['token']
    response = client.post('/api/action', headers={'X-Editor-Token': token}, json={
        'action': 'compile', 'path': 'product.yaml', 'out': 'generated',
        'dialect': 'sqlite', 'test_pack': test_pack})
    assert response.status_code == 422
    assert response.get_json()['error'] == 'test_pack must be none or sql'
    assert not (tmp_path / 'generated').exists()


def test_flask_draft_check_registry_matches_compiler(tmp_path):
    pytest.importorskip('flask')
    pytest.importorskip('sqlglot')
    from cade.exposers.web import create_app
    from cade.brokers.file_broker import FileBroker
    from cade.services.specification_service import SpecificationService
    from cade.services.sql_service import SqlTemplateService
    from pathlib import Path
    from shutil import copy2
    import yaml
    example=Path(__file__).resolve().parents[1]/'examples/multi_model'
    copy2(example/'product.yaml',tmp_path/'product.yaml')
    copy2(example/'product.schema-v0.8.4.json',tmp_path/'product.schema-v0.8.4.json')
    app=create_app(tmp_path)
    client=app.test_client()
    doc=yaml.safe_load((tmp_path/'product.yaml').read_text())
    # Use the production YAML 1.2-style loader so 'on' remains a string.
    from cade.services.specification_service import UniqueKeysLoader
    doc=yaml.load((tmp_path/'product.yaml').read_text(), Loader=UniqueKeysLoader)
    response=client.get('/api/bootstrap')
    if response.status_code!=200: pytest.skip('Flask editor bootstrap requires browser token setup')
    token=response.get_json().get('token')
    response=client.post('/api/check-registry',json={'path':'product.yaml','document':doc},
                         headers={'X-Editor-Token':token})
    assert response.status_code==200, response.get_json()
    identifiers={item['id'] for item in response.get_json()['checks']}
    product=SpecificationService(FileBroker()).load(tmp_path/'product.yaml')
    expected={mid+'/'+check_id for mid,model in zip(product.model_ids,product.models)
              for check_id,_ in SqlTemplateService().check_statements(model)}
    assert identifiers==expected


def test_tutorial_cli_setup_build_exercises_and_value(tmp_path, monkeypatch, capsys):
    from cade.exposers.cli import main
    from cade.services.tutorial_service import TutorialService
    from cade.services.specification_service import SpecificationService
    from cade.services.sql_service import SqlTemplateService
    from cade.brokers.file_broker import FileBroker
    import json
    import yaml
    monkeypatch.setenv(TutorialService.ENV, '')
    root = tmp_path / 'tutorial'
    assert main(['tutorial', 'init', str(root)]) == 0
    assert 'export CADE_TUTORIAL_DATABASE_URL=' in capsys.readouterr().out
    original = (root / 'product/product.yaml').read_bytes()
    assert main(['tutorial', 'init', str(root)]) == 0
    assert (root / 'product/product.yaml').read_bytes() == original
    assert main(['build', str(root / 'starter/product.yaml')]) == 0
    product_reports = list((root / 'starter/generated').glob('*/runs/*/evidence/acceptance-results.md'))
    assert len(product_reports) == 1
    assert 'run-manifest.json' in product_reports[0].read_text()
    assert main(['build', str(root / 'product/product.yaml')]) == 0
    service = TutorialService()
    assert service.report(root)['action_queue'] == [
        {'connection_id': 'D3', 'merchant_name': 'City Pharmacy', 'service_id': 'S9'}]
    spec = SpecificationService(FileBroker()).load(root / 'product/product.yaml')
    assert spec.model_ids == ('merchant', 'service', 'connection')
    document = yaml.safe_load((root / 'product/product.yaml').read_text())
    linked = {
        check for feature in document['definition']['features'] for criterion in feature['acceptance_criteria']
        for check in criterion['check_ids']}
    generated = {model_id + '/' + check_id for model_id, model in zip(spec.model_ids, spec.models)
                 for check_id, _ in SqlTemplateService().check_statements(model)}
    assert linked == generated
    for case, failed_check in zip(service.CASES, ('canonical_key_unique', 'owner_examples', 'reconciliation')):
        assert main(['tutorial', 'exercise', case, '--path', str(root)]) == 0
        reports = list((root / case / 'build').glob('*/run-manifest.json'))
        manifest = json.loads(reports[-1].read_text())
        assert manifest['status'] == 'failed'
        assert failed_check in [item['id'] for item in manifest['tests'] if item['status'] == 'failed']
    assert main(['tutorial', 'report', '--path', str(root)]) == 0
    assert main(['tutorial']) == 0
    assert 'Ask Service Operations' in capsys.readouterr().out


def test_tutorial_ui_shared_workflow_and_local_guards(tmp_path, monkeypatch):
    from cade.exposers.web import create_app
    from cade.services.tutorial_service import TutorialService
    import json
    monkeypatch.setenv(TutorialService.ENV, '')
    client = create_app(tmp_path).test_client()
    headers = {'X-Editor-Token': client.get('/api/bootstrap').json['token']}
    assert b'Tutorial' in client.get('/').data
    assert client.get('/tutorial').status_code == 200
    assert client.get('/tutorial/guide').status_code == 200
    assert not client.get('/api/tutorial').json['ready']
    assert client.post('/api/tutorial', json={'action': 'init'}).status_code == 403
    assert client.post('/api/tutorial', json={'action': 'init'}, headers=headers).status_code == 200
    assert client.post('/api/tutorial', json={'action': 'init'}, headers=headers).status_code == 200
    assert client.get('/api/tutorial').json['ready']
    response = client.post('/api/tutorial', json={'action': 'report'}, headers=headers)
    assert response.status_code == 422 and 'Build the full product first' in response.json['error']
    for variant in ('starter', 'product'):
        response = client.post('/api/action', json={'action': 'build', 'confirm': True,
                               'path': f'tutorial/{variant}/product.yaml'}, headers=headers)
        assert response.status_code == 200, response.json
        assert response.json['status'] == 'passed'
    assert client.post('/api/tutorial', json={'action': 'exercise', 'case': 'wrong-status'}, headers=headers).status_code == 422
    for case in TutorialService.CASES:
        response = client.post('/api/tutorial', json={'action': 'exercise', 'case': case, 'confirm': True}, headers=headers)
        assert response.status_code == 200, response.json
        assert response.json['expected_failure'] and response.json['publication_unchanged']
    assert client.post('/api/tutorial', json={'action': 'exercise', 'case': '../product', 'confirm': True}, headers=headers).status_code == 422
    review = {'action': 'review', 'reviewer': 'Sample owner', 'decisions': 'Sample assumptions: LIVE is enabled. Keep D3.'}
    assert client.post('/api/tutorial', json=review, headers=headers).status_code == 200
    assert json.loads((tmp_path / 'tutorial/owner-review.json').read_text())['reviewer'] == 'Sample owner'
    assert client.post('/api/tutorial', json={'action': 'review'}, headers=headers).status_code == 422
    assert client.post('/api/tutorial', json=review, headers={**headers, 'Origin': 'https://outside.example'}).status_code == 403
    client = create_app(tmp_path / 'tutorial').test_client()
    assert client.get('/api/tutorial').json == {'ready': True, 'path': '.', 'cases': list(TutorialService.CASES)}
