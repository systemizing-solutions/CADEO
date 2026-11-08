"""One six-stage public YAML contract -> one internal build unit per model.

The data product's model IDs are referenced by the mapping, assurance and
publication registries. There is no legacy YAML shape and no alternate SQL path.
"""
import copy

STAGES = ('definition', 'model', 'sources', 'mapping', 'assurance', 'delivery')


def to_ir_fields(document: dict, model_id: str) -> dict:
    """Normalize one canonical model of a data product for existing compiler services."""
    define = document['definition']
    entity = document['model']['entities'][model_id]
    mapping = document['mapping']['entities'][model_id]
    assure = document['assurance']['entities'][model_id]
    deliver = document['delivery']
    # Test references are explicitly scoped: model_id/check_id. Internally,
    # check IDs continue to use the single canonical SqlTemplateService registry.
    business = copy.deepcopy(define)
    for feature in business['features']:
        for criterion in feature['acceptance_criteria']:
            criterion['check_ids'] = [ref.split('/',1)[1]
                for ref in criterion.get('check_ids',[]) if ref.startswith(model_id + '/')]
    normalized = {
        'spec_version': document['spec_version'],
        'product': {**{k: copy.deepcopy(define[k]) for k in ('owner','description','consumers') if k in define},
                    'name': f'{define["name"]}.{model_id}', 'target': deliver['targets'][model_id]['table']},
        'business': {k:business[k] for k in ('outcome','requirements','features')},
        'canonical': {'entity':entity['entity_name'],'grain':entity['record_grain'],
                      'key':copy.deepcopy(entity['unique_key_attributes']),
                      'attributes':copy.deepcopy(entity['attributes'])},
        'relationships':copy.deepcopy(entity.get('relationships',[])),
        'connections':copy.deepcopy(document['sources'].get('connections',{})),
        'sources':copy.deepcopy(document['sources']['datasets']),
        'mapping':copy.deepcopy(mapping['fields']),
        'reconciliation':copy.deepcopy(assure['reconciliation']),
        'preservation':copy.deepcopy(assure['preservation']),
        'tests':copy.deepcopy(assure.get('tests',{})),
        'operations':{'quality':copy.deepcopy(document['assurance']['quality']),
                      'refresh':copy.deepcopy(deliver['refresh']),
                      'recovery':copy.deepcopy(deliver['recovery'])},
    }
    for key in ('source','joins','filters','priority','unions','deduplicate'):
        if key in mapping: normalized[key]=copy.deepcopy(mapping[key])
    for key in ('output','dialect','execution','artifact_paths'):
        if key in deliver: normalized[key]=copy.deepcopy(deliver[key])
    for branch in normalized.get('unions',[]):
        if 'fields' in branch: branch['mapping']=branch.pop('fields')
    return normalized


def ordered_document(document: dict, public_schema: dict) -> dict:
    """Deterministic schema-order serialization without losing registry key order."""
    def arrange(data,shape):
        if not isinstance(data,dict) or not isinstance(shape,dict):
            return copy.deepcopy(data)
        if '$ref' in shape:
            node=public_schema
            for key in shape['$ref'][2:].split('/'):
                node=node[key.replace('~1','/').replace('~0','~')]
            shape=node if isinstance(node,dict) else {}
        if 'oneOf' in shape:
            for alt in shape['oneOf']:
                if isinstance(alt,dict) and all(k in data for k in alt.get('required',[])):
                    shape=alt
                    break
        properties=shape.get('properties',{})
        result={}
        for key,field in properties.items():
            if key in data: result[key]=arrange(data[key],field)
        for key,value in data.items():
            if key not in result:
                rule=shape.get('additionalProperties')
                result[key]=arrange(value,rule) if isinstance(rule,dict) else copy.deepcopy(value)
        return result
    return arrange(document,public_schema)
