"""Dependency-enabled integration: real transpilation and SQLAlchemy publication."""
import pytest


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
    assert (tmp_path/'merchant_operations.devices.sql').exists()
    assert (tmp_path/'merchant_operations.merchants.sql').exists()


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
    copy2(example/'product.schema-v0.8.0.json',tmp_path/'product.schema-v0.8.0.json')
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
    assert main(['tutorial', 'init', str(root)]) == 1
    assert (root / 'product/product.yaml').read_bytes() == original
    assert main(['build', str(root / 'starter/product.yaml')]) == 0
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
    assert client.post('/api/tutorial', json={'action': 'init'}, headers=headers).status_code == 409
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
