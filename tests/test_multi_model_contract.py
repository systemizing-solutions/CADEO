"""v0.8.4 single-authoritative-contract regression tests.

Tests exercise multiple canonical tables in one product, their model-specific
source mappings, quality gates and project planning. No historical YAML shape.
"""
import copy
import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from jsonschema import Draft202012Validator

from cade.brokers.file_broker import FileBroker
from cade.models.specification import DataProduct, SpecificationValidationError
from cade.services.dependency_service import DependencyService
from cade.services.dialect_service import DialectService
from cade.services.evidence_service import EvidenceService
from cade.services.product_service import ProductService
from cade.services.project_service import ProjectService
from cade.services.schema_catalog_service import SchemaCatalogService
from cade.services.specification_layout_service import ordered_document
from cade.services.specification_service import SpecificationService, UniqueKeysLoader
from cade.services.sql_service import SqlTemplateService
from cade.services.traceability_service import TraceabilityService
from cade.services.visual_graph_service import VisualGraphService
from cade.services.verification_service import VerificationService
from tests.support import write_document

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'examples' / 'multi_model' / 'product.yaml'
SCHEMA = ROOT / 'src' / 'cade' / 'schemas' / 'product.schema.json'

@pytest.fixture(autouse=True)
def dialect_without_optional_transpiler():
    # Structural tests check the actual compiler IR. Translation/runtime
    # integration tests below require the real optional SQLGlot/SQLFluff deps.
    with patch.object(DialectService, 'validate', return_value='sqlite'):
        yield

@pytest.fixture
def services():
    files=FileBroker(); specification=SpecificationService(files)
    products=ProductService(files,specification)
    return specification,products,ProjectService(files,specification,products)

@pytest.fixture
def doc():
    return yaml.load(EXAMPLE.read_text(), Loader=UniqueKeysLoader)


def test_public_contract_version():
    schema=json.loads(SCHEMA.read_text())
    from cade.exposers.cli import VERSION
    assert schema['x-schema-version'] == schema['properties']['spec_version']['const'] == VERSION == '0.8.4'
    assert SchemaCatalogService.DEFAULT == f'schemas/product-v{VERSION}.schema.json'
    assert tuple(schema['properties']) == ('schema','spec_version','definition','model','sources','mapping','assurance','delivery')
    Draft202012Validator.check_schema(schema)


def test_contract_has_no_alternative_legacy_fields():
    schema=json.loads(SCHEMA.read_text())
    assert not schema.get('additionalProperties',True)
    for old in ('canonical','business','product','operations'):
        assert old not in schema['properties']


def test_one_product_two_models(services):
    spec=services[0].load(EXAMPLE)
    assert isinstance(spec,DataProduct)
    assert spec.name=='merchant_operations'
    assert spec.model_ids == ('merchants','devices')
    assert {m.canonical.entity for m in spec.models}=={'Device','Merchant'}
    assert spec.for_model('merchants').product.target=='canonical_merchants'
    assert spec.for_model('devices').product.target=='canonical_devices'


def test_models_get_own_grain_keys_and_fields(services):
    spec=services[0].load(EXAMPLE)
    merchant=spec.for_model('merchants'); device=spec.for_model('devices')
    assert merchant.canonical.key==('merchant_id',)
    assert device.canonical.key==('device_id',)
    assert {a.name for a in merchant.canonical.attributes}=={'merchant_id','merchant_name'}
    assert {a.name for a in device.canonical.attributes}=={'device_id','merchant_id','merchant_name','device_status'}


def test_internal_model_reference_inherits_table_and_contract(services):
    p=services[0].load(EXAMPLE)
    ref=next(s for s in p.for_model('devices').sources if s.id=='published_merchants')
    assert ref.product_ref=='merchant_operations.merchants'
    assert ref.table=='canonical_merchants'
    assert ref.key==('merchant_id',)
    assert ref.columns==('merchant_id','merchant_name')
    assert ref.contract is None


def test_model_dependency_is_explicit(services):
    project=services[2].load(EXAMPLE.parent)
    nodes=services[2].plan(project).nodes
    merchant='product:merchant_operations.merchants:publish:product'
    device='product:merchant_operations.devices:source:published_merchants'
    assert next(n for n in nodes if n.id==device).depends_on.count(merchant)==1
    ids=[n.id for n in nodes]
    assert ids.index(merchant)<ids.index(device)


def test_targeted_model_build_includes_upstream(services):
    project=services[2].load(EXAMPLE.parent)
    assert services[2].plan(project,('merchant_operations.devices',)).products == (
        'merchant_operations.merchants','merchant_operations.devices')
    assert services[2].plan(project,('merchant_operations.merchants',)).products == (
        'merchant_operations.merchants',)
    assert services[2].plan(project,('merchant_operations',)).products == project.names


@pytest.mark.parametrize('registry',('mapping','assurance','delivery'))
def test_missing_model_registry_rejected(services,doc,registry):
    entries=doc[registry]['targets' if registry=='delivery' else 'entities']
    del entries['devices']
    with pytest.raises(SpecificationValidationError,match='model IDs must match'):
        services[0].parse(doc)


@pytest.mark.parametrize('registry',('mapping','assurance','delivery'))
def test_extra_model_registry_rejected(services,doc,registry):
    entries=doc[registry]['targets' if registry=='delivery' else 'entities']
    entries['orphan']=copy.deepcopy(next(iter(entries.values())))
    with pytest.raises(SpecificationValidationError,match='model IDs must match'):
        services[0].parse(doc)


def test_duplicate_physical_target_rejected(services,doc):
    doc['delivery']['targets']['devices']['table']='canonical_merchants'
    with pytest.raises(SpecificationValidationError,match='same table'):
        services[0].parse(doc)


def test_unknown_model_reference_rejected(services,doc):
    doc['sources']['datasets']['published_merchants']['model_ref']='missing_model'
    with pytest.raises(SpecificationValidationError,match='model_ref .*does not exist'):
        services[0].parse(doc)


def test_circular_model_dependencies_rejected(services,doc):
    doc['sources']['datasets']['returned_devices']={'model_ref':'devices','alias':'z'}
    doc['mapping']['entities']['merchants']['source']='returned_devices'
    del doc['sources']['datasets']['raw_merchants']
    with pytest.raises(SpecificationValidationError,match='Circular dependency|cycle'):
        services[0].parse(doc)


def test_undeclared_model_mapping_source_rejected(services,doc):
    doc['mapping']['entities']['devices']['source']='missing'
    with pytest.raises(SpecificationValidationError,match='unknown source'):
        services[0].parse(doc)


def test_unused_physical_source_rejected(services,doc):
    doc['sources']['datasets']['orphan']=copy.deepcopy(doc['sources']['datasets']['raw_merchants'])
    doc['sources']['datasets']['orphan']['alias']='unused'
    with pytest.raises(SpecificationValidationError,match='unused source'):
        services[0].parse(doc)


def test_missing_canonical_target_mapping_rejected(services,doc):
    del doc['mapping']['entities']['devices']['fields']['device_status']
    with pytest.raises(SpecificationValidationError,match='device_status|mapping'):
        services[0].parse(doc)


def test_extra_canonical_target_mapping_rejected(services,doc):
    doc['mapping']['entities']['devices']['fields']['no_such_field']={'column':'d.device_id'}
    with pytest.raises(SpecificationValidationError,match='no_such_field|mapping'):
        services[0].parse(doc)


def test_unknown_model_scoped_check_rejected(services,doc):
    doc['definition']['features'][0]['acceptance_criteria'][0]['check_ids'][0]='garbage/canonical_key_unique'
    with pytest.raises(SpecificationValidationError,match="unknown model 'garbage'"):
        services[0].parse(doc)


def test_wrong_model_scoped_check_rejected(services,doc):
    doc['definition']['features'][0]['acceptance_criteria'][0]['check_ids']=['merchants/merchant_exists']
    with pytest.raises(SpecificationValidationError,match='merchant_exists|check'):
        services[0].parse(doc)


def test_unscoped_check_id_fails_validation(services,doc):
    doc['definition']['features'][0]['acceptance_criteria'][0]['check_ids']=['canonical_key_unique']
    with pytest.raises(SpecificationValidationError):
        services[0].parse(doc)


def test_core_schema_uses_single_explicit_model_registry(doc):
    schema=json.loads(SCHEMA.read_text())
    for key in ('model','mapping','assurance'):
        assert 'entities' in schema['properties'][key]['properties']
    assert 'targets' in schema['properties']['delivery']['properties']
    assert not list(Draft202012Validator(schema).iter_errors(doc))


def test_erd_contains_both_entities_and_relationship(services):
    p=services[0].load(EXAMPLE)
    graph=VisualGraphService.erd(p.models)
    assert {e['name'] for e in graph['entities']}=={'Merchant','Device'}
    assert len(graph['relationships'])==1
    assert graph['relationships'][0]['status']=='resolved'
    assert graph['relationships'][0]['attribute']=='merchant_id'


def test_lineage_contains_both_models(services):
    p=services[0].load(EXAMPLE)
    graph=VisualGraphService().multi_lineage(p.models,p.name)
    assert {'merchants.merchant_id','devices.device_id'}.issubset({a['attribute'] for a in graph['fields']})
    assert any(e.get('kind')=='model-reference' and e['from']=='merchants/target:canonical'
               and e['to']=='devices/source:published_merchants' for e in graph['edges'])


def test_traceability_source_column_correct_by_model(services):
    p=services[0].load(EXAMPLE)
    merchant=TraceabilityService().build(p.for_model('merchants'))
    devices=TraceabilityService().build(p.for_model('devices'))
    assert merchant['fields'] and devices['fields']
    assert len(devices['fields'])==4


def test_checks_model_qualified_and_real_registry(services):
    p=services[0].load(EXAMPLE)
    checks={mid+'/'+key for mid,unit in zip(p.model_ids,p.models)
            for key,_ in SqlTemplateService().check_statements(unit)}
    expected={cid for feature in p.definition['definition']['features']
              for ac in feature['acceptance_criteria'] for cid in ac['check_ids']}
    assert expected.issubset(checks)
    assert 'merchants/canonical_key_unique' in checks
    assert 'devices/canonical_key_unique' in checks
    assert 'devices/merchant_exists' in checks


def test_no_manifest_has_no_passing_evidence(services):
    p=services[0].load(EXAMPLE)
    service=EvidenceService()
    result=service.aggregate(p,None)
    assert result['publication']['status']=='not_run'
    assert result['summary']['passed']==0
    assert result['summary']['not_run']>=5
    assert len(result['evidence_warnings'])==1
    assert 'highly recommended' in result['evidence_warnings'][0]
    assert 'configured connection' in result['evidence_warnings'][0]
    assert 'SQLite' in result['evidence_warnings'][0]
    assert 'before release' in service.markdown(service.build(p.models[0],None))


def test_single_source_example_is_one_model_in_same_shape(services):
    p=services[0].load(ROOT/'examples'/'sqlite_device_full.yaml')
    assert len(p.models)==1
    assert len(p.definition['model']['entities'])==1


def test_initializer_creates_versioned_complete_product(services,tmp_path):
    yaml_path,schema_path=services[1].init(tmp_path/'new.yaml')
    assert yaml_path.exists() and schema_path.exists()
    p=services[0].load(yaml_path)
    assert p.models and p.model_ids
    assert p.definition['spec_version']=='0.8.4'


def test_all_examples_share_contract(services):
    failed=[]
    for path in sorted((ROOT/'examples').rglob('*.yaml')):
        try:
            # Product-reference examples require whole-project context.
            if 'product_ref:' in path.read_text():
                continue
            services[0].load(path)
        except Exception as error:
            failed.append((str(path.relative_to(ROOT)),str(error)))
    assert not failed,failed


def test_project_cross_product_refs(services):
    root=ROOT/'examples'/'cross_product'
    project=services[2].load(root)
    assert len(project.names)>=2
    nodes=services[2].plan(project).nodes
    assert any(n.kind=='product-reference' for n in nodes)


def test_reused_alias_joins_resolve_per_branch(services):
    p=services[0].load(ROOT/'examples'/'sqlite_device_full.yaml')
    trace=TraceabilityService().build(p.models[0])
    assert trace['fields']


def test_schema_ordered_authoring(services,doc):
    s=json.loads(SCHEMA.read_text())
    ordered=ordered_document(doc,s)
    assert list(ordered) == ['schema','spec_version','definition','model','sources','mapping','assurance','delivery']


def test_schema_version_enforced(services,doc):
    doc['schema']['version']='0.7.6'
    with pytest.raises(SpecificationValidationError,match='mismatch'):
        services[0].parse(doc)


def test_source_contract_inherited_without_copying_in_yaml(doc):
    source=doc['sources']['datasets']['published_merchants']
    assert source=={'model_ref':'merchants','alias':'m'}
    assert not set(source)&{'columns','record_key_columns','table'}


@pytest.mark.parametrize('field',('entity_name','record_grain','unique_key_attributes','attributes'))
def test_model_schema_requires_complete_canonical_design(services,doc,field):
    del doc['model']['entities']['merchants'][field]
    with pytest.raises(SpecificationValidationError):
        services[0].parse(doc)


def test_new_model_ids_cannot_use_sql_unsafe_characters(services,doc):
    doc['model']['entities']['bad;table']=copy.deepcopy(doc['model']['entities']['merchants'])
    with pytest.raises(SpecificationValidationError):
        services[0].parse(doc)


def test_duplicate_model_keys_malformed_document_fails(services,doc):
    doc['model']['entities']['merchants']['unique_key_attributes']=['not_declared']
    with pytest.raises(SpecificationValidationError,match='unknown canonical'):
        services[0].parse(doc)


def test_different_model_attributes_are_not_misidentified(services,doc):
    doc['model']['entities']['devices']['attributes']['merchant_id']['type']='integer'
    with pytest.raises(SpecificationValidationError,match='type|incompatible'):
        services[0].parse(doc)


def test_product_target_default_cli_independent(services):
    p=services[0].load(EXAMPLE)
    path=EXAMPLE
    targets={ProductService.output_directory(path,model,None,None,[]) for model in p.models}
    assert len(targets)==1


def test_dependency_order_stable(services):
    project=services[2].load(EXAMPLE.parent)
    assert services[2].plan(project).to_dict()==services[2].plan(project).to_dict()


def test_all_specs_use_new_version():
    specifications = SpecificationService(FileBroker())
    for path in (ROOT/'examples').rglob('*.yaml'):
        document = specifications.read_document(path)
        assert document['spec_version']=='0.8.4',path


def test_no_old_renderer_in_package():
    base=ROOT/'src'/'cade'
    assert not list(base.rglob('sqlite_broker.py'))
    for path in list(base.rglob('*.py')):
        assert not re.search(r'^\s*import sqlite3\b|^\s*from sqlite3\b',path.read_text(),re.M)


def test_generated_example_yaml_has_one_source_of_truth(services):
    p=services[0].load(EXAMPLE)
    ids=list(p.definition['model']['entities'])
    assert set(p.definition['mapping']['entities'])==set(ids)
    assert set(p.definition['assurance']['entities'])==set(ids)
    assert set(p.definition['delivery']['targets'])==set(ids)


def test_circular_project_refs_rejected(tmp_path,services,doc):
    a=copy.deepcopy(doc);b=copy.deepcopy(doc)
    a['definition']['name']='project_a'; b['definition']['name']='project_b'
    b['delivery']['targets']['merchants']['table']='other_merchants'
    b['delivery']['targets']['devices']['table']='other_devices'
    # One product's source points to the other product, then back.
    a['sources']['datasets']['published_merchants']={'product_ref':'project_b.merchants','alias':'m'}
    b['sources']['datasets']['published_merchants']={'product_ref':'project_a.merchants','alias':'m'}
    write_document(tmp_path/'a.yaml',a);write_document(tmp_path/'b.yaml',b)
    with pytest.raises(SpecificationValidationError,match='Circular dependency|cycle'):
        services[2].load(tmp_path)


def test_duplicate_publication_across_products_rejected(tmp_path,services,doc):
    a=copy.deepcopy(doc);b=copy.deepcopy(doc); b['definition']['name']='other_product'
    write_document(tmp_path/'a.yaml',a);write_document(tmp_path/'b.yaml',b)
    with pytest.raises(SpecificationValidationError,match='Duplicate publication target'):
        services[2].load(tmp_path)


def test_dangling_cross_product_ref_rejected(tmp_path,services,doc):
    doc['sources']['datasets']['published_merchants']={'product_ref':'missing.merchants','alias':'m'}
    write_document(tmp_path/'a.yaml',doc)
    with pytest.raises(SpecificationValidationError,match='missing product'):
        services[2].load(tmp_path)


def test_web_frontend_contains_model_scope():
    js=(ROOT/'src/cade/web/editor.js').read_text()
    html=(ROOT/'src/cade/web/index.html').read_text()
    assert 'modelSelector' in html
    assert 'model_ref' in js
    assert 'mappingOverview' in js
    assert 'activeModelId' in js
    assert '/api/check-registry' in js
    assert 'sqlite3.connect' not in js


def test_specification_schema_pinned_on_disk(services,tmp_path,doc):
    write_document(tmp_path/'spec.yaml',doc)
    assert services[0].load(tmp_path/'spec.yaml').model_ids==('merchants','devices')


def test_unrecognized_source_reference_rejected(services,doc):
    doc['sources']['datasets']['published_merchants']['model_ref']='nonexistent'
    with pytest.raises(SpecificationValidationError):
        services[0].parse(doc)


def test_existing_sql_generator_gets_per_model_build_units(services):
    p=services[0].load(EXAMPLE)
    for m in p.models:
        assert len(DependencyService().build(m).nodes)>=5
        assert SqlTemplateService().check_statements(m)


def test_file_target_build_planning_accepts_single_model(services):
    from cade.services.project_service import Project
    product=services[0].load(EXAMPLE)
    project=Project(EXAMPLE.parent, tuple((m.product.name, EXAMPLE, m) for m in product.models))
    assert services[2].plan(project,('devices',)).products==(
        'merchant_operations.merchants','merchant_operations.devices')


def test_target_model_shorthand_is_ambiguous_across_products(services,tmp_path,doc):
    a=copy.deepcopy(doc); b=copy.deepcopy(doc)
    b['definition']['name']='other'
    b['delivery']['targets']['merchants']['table']='other_merchant_table'
    b['delivery']['targets']['devices']['table']='other_device_table'
    write_document(tmp_path/'first.yaml',a);write_document(tmp_path/'second.yaml',b)
    project=services[2].load(tmp_path)
    with pytest.raises(SpecificationValidationError,match='Ambiguous target'):
        services[2].plan(project,('devices',))


def test_evidence_matrix_has_one_row_per_referenced_check(services):
    product=services[0].load(EXAMPLE)
    evidence=EvidenceService().aggregate(product,None)
    ids=[row['check_id'] for row in evidence['matrix']]
    assert sorted(ids)==sorted(product.definition['definition']['features'][0]
                              ['acceptance_criteria'][0]['check_ids'])
