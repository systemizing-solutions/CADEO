"""Read-only visual projections of the existing canonical IR, lineage and DAG.

No additional mapping, dependency or execution model is maintained here. The
source references and STTM rules come from TraceabilityService and Specification;
the build graph is a direct projection of DependencyService's plan.
"""

from __future__ import annotations

import json
from typing import Any

from cade.models.specification import Specification
from cade.services.dependency_service import DependencyPlan, DependencyService
from cade.services.traceability_service import TraceabilityService


class VisualGraphService:
    """Render specification-derived graph data for the local browser editor."""

    @staticmethod
    def _expression(value: Any) -> str:
        """Compact human-readable STTM description; never used to generate SQL."""
        if isinstance(value, dict):
            if 'column' in value:
                return str(value['column'])
            if 'literal' in value:
                return repr(value['literal'])
            if 'call' in value:
                call = value['call']
                return f"{call['name']}(" + ', '.join(
                    VisualGraphService._expression(arg) for arg in call['args']) + ')'
            if len(value) == 1:
                key, operand = next(iter(value.items()))
                if isinstance(operand, list):
                    return f"{key.upper()}(" + ', '.join(
                        VisualGraphService._expression(part) for part in operand) + ')'
                if isinstance(operand, dict):
                    return f"{key.upper()}({VisualGraphService._expression(operand)})"
        return json.dumps(value, sort_keys=True, ensure_ascii=False)

    def lineage(self, spec: Specification) -> dict[str, Any]:
        """One source-aware graph for all mapping branches and canonical fields."""
        trace = TraceabilityService().build(spec)
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        seen: set[str] = set()
        paths: dict[int, list[str]] = {}

        def node(key: str, kind: str, label: str, detail: str = '', branch: int | None = None) -> None:
            if key not in seen:
                item = {'id': key, 'kind': kind, 'label': label, 'detail': detail}
                if branch is not None:
                    item['branch'] = branch
                nodes.append(item)
                seen.add(key)

        def edge(start: str, end: str, kind: str, label: str = '') -> None:
            edges.append({'from': start, 'to': end, 'kind': kind, 'label': label})

        # Include physical and product-ref source identities even when not mapped.
        for source in sorted(spec.sources, key=lambda x: x.id):
            detail = f"Published product: {source.product_ref}" if source.product_ref else source.table
            node(f'source:{source.id}', 'source', source.id, detail)
        for index, branch in enumerate(spec.branches):
            current = f'source:{branch.source.id}'
            visited = [current]
            for number, join in enumerate(branch.joins):
                key = f'join:{index}:{number}'
                predicates = ' AND '.join(
                    f'{self._expression(cond.left)} = {self._expression(cond.right)}'
                    for cond in join.on)
                node(key, 'join', f'{join.type.upper()} JOIN', predicates, index)
                edge(current, key, 'pipeline', 'left input')
                edge(f'source:{join.source.id}', key, 'join-input', 'joined source')
                current = key
                visited.extend((f'source:{join.source.id}', key))
            if branch.filters:
                key = f'filter:{index}'
                detail = ' AND '.join(self._expression(pred) for pred in branch.filters)
                node(key, 'filter', 'Filter', detail, index)
                edge(current, key, 'pipeline')
                current = key
                visited.append(key)
            key = f'transform:{index}'
            node(key, 'transformation', f'Transform · branch {index + 1}',
                 f'{len(branch.mappings)} field mappings · priority {branch.priority}', index)
            edge(current, key, 'pipeline')
            visited.append(key)
            paths[index] = list(dict.fromkeys(visited))

        final = 'transform:0'
        if len(spec.branches) > 1:
            node('union:all', 'union', 'UNION ALL', f'{len(spec.branches)} branches')
            for number in range(len(spec.branches)):
                edge(f'transform:{number}', 'union:all', 'union-input', f'branch {number + 1}')
            final = 'union:all'
        if spec.deduplicate is not None:
            node('dedup:canonical', 'deduplication', 'Deduplicate',
                 'Keys: ' + ', '.join(spec.deduplicate.by))
            edge(final, 'dedup:canonical', 'pipeline')
            final = 'dedup:canonical'
        node('target:canonical', 'target', spec.canonical.entity, spec.product.target)
        edge(final, 'target:canonical', 'pipeline', 'canonical output')

        # The traceability service is authoritative for branch-local physical columns.
        # Walk only the branch being described: aliases can be reused across unions.
        fields: list[dict[str, Any]] = []
        for field in trace['fields']:
            rules = []
            for rule in field['rules']:
                branch_number = rule['branch']
                branch = spec.branches[branch_number]
                sources_by_alias = {branch.source.alias: branch.source}
                sources_by_alias.update({join.alias: join.source for join in branch.joins})
                direct = [{
                    'alias_column': column,
                    'physical_column': physical,
                    'source_id': sources_by_alias[column.split('.', 1)[0]].id,
                } for column, physical in zip(rule['source_columns'], rule['physical_source_columns'])]
                contextual = [{
                    'alias_column': column,
                    'physical_column': physical,
                    'source_id': sources_by_alias[column.split('.', 1)[0]].id,
                } for column, physical in zip(rule['context_columns'], rule['physical_context_columns'])]
                rules.append({
                    'id': rule['id'], 'branch': branch_number,
                    'expression': rule['expression'],
                    'summary': self._expression(rule['expression']),
                    'derivation': rule['derivation'],
                    'direct_columns': direct, 'context_columns': contextual,
                    'node_ids': list(dict.fromkeys(
                        paths[branch_number]
                        + [f'source:{col["source_id"]}' for col in direct + contextual]
                        + (['union:all'] if len(spec.branches) > 1 else [])
                        + (['dedup:canonical'] if spec.deduplicate else [])
                        + ['target:canonical'])),
                })
            fields.append({'attribute': field['attribute'], 'type': field['type'],
                           'requirement_ids': field['requirement_ids'], 'rules': rules})
        return {
            'format': 'cade.visual-graph.v1', 'mode': 'lineage',
            'product': spec.product.name, 'target': spec.product.target,
            'nodes': nodes, 'edges': edges, 'fields': fields,
            'warnings': trace['warnings'], 'unmapped_source_columns': trace['unmapped_source_columns'],
        }

    @staticmethod
    def dependencies(plan: DependencyPlan | Any) -> dict[str, Any]:
        """Project and single-product views share the actual dependency plan."""
        def display(node: Any) -> dict[str, Any]:
            parts = node.id.split(':')
            # Project IDs use product:<product_id>:<local_node_id>.
            # Keep the local execution step legible, with its product as context.
            product = parts[1] if len(parts) > 3 and parts[0] == 'product' else None
            label = ':'.join(parts[2:]) if product is not None else node.id
            detail = ' · '.join(filter(None, (product, node.artifact or node.action)))
            return {'id': node.id, 'kind': node.kind, 'label': label,
                    'detail': detail}

        return {
            'format': 'cade.visual-graph.v1', 'mode': 'dependencies',
            'nodes': [display(n) for n in plan.nodes],
            'edges': [{'from': dependency, 'to': node.id, 'kind': 'dependency', 'label': ''}
                      for node in plan.nodes for dependency in node.depends_on],
            'execution_order': [n.id for n in plan.nodes],
            'products': list(getattr(plan, 'products', ())),
        }

    @staticmethod
    def erd(specifications: tuple[Specification, ...] | list[Specification], *,
            scope: str = 'file') -> dict[str, Any]:
        """Project or draft canonical ERD, solely from the validated canonical IR.

        Relationships are only drawn when explicitly declared in model.relationships.
        There is no inference from a physical join, similarly named attribute, or
        cross-product source reference. Missing/ambiguous entities remain visible
        rather than silently resolving to the wrong data product.
        """
        specs = sorted(specifications, key=lambda item: item.product.name)
        entities: list[dict[str, Any]] = []
        links: list[dict[str, Any]] = []
        warnings: list[str] = []
        by_name: dict[str, list[str]] = {}

        for spec in specs:
            model = spec.canonical
            entity_id = f'product:{spec.product.name}'
            by_name.setdefault(model.entity, []).append(entity_id)
            entities.append({
                'id': entity_id, 'name': model.entity,
                'product': spec.product.name, 'target': spec.product.target,
                'grain': model.grain, 'external': False,
                'attributes': [{
                    'name': a.name, 'type': a.type, 'nullable': a.nullable,
                    'key': a.name in model.key, 'requirement_ids': list(a.requirement_ids),
                } for a in model.attributes],
                'key_attributes': list(model.key),
            })

        external: set[str] = set()
        for spec in specs:
            start = f'product:{spec.product.name}'
            for index, relation in enumerate(spec.canonical.relationships):
                matches = by_name.get(relation.entity, [])
                if len(matches) == 1:
                    end = matches[0]
                    target = next(entity for entity in entities if entity['id'] == end)
                    if relation.related_attribute and relation.related_attribute not in {
                            attr['name'] for attr in target['attributes']}:
                        warnings.append(
                            f'{spec.product.name}: model.relationships.{index}.related_attribute '
                            f"'{relation.related_attribute}' not present on {relation.entity}")
                        status = 'unresolved_attribute'
                    else:
                        status = 'resolved'
                else:
                    end = f'external:{relation.entity}'
                    external.add(relation.entity)
                    status = 'external' if not matches else 'ambiguous'
                    if len(matches) > 1:
                        warnings.append(
                            f'{spec.product.name}: relationship to {relation.entity} is ambiguous '
                            f'({len(matches)} products define that entity)')
                links.append({
                    'id': f'relationship:{spec.product.name}:{index}',
                    'from': start, 'to': end,
                    'attribute': relation.attribute,
                    'related_attribute': relation.related_attribute,
                    'cardinality': relation.cardinality,
                    'status': status,
                })

        for name in sorted(external):
            entities.append({
                'id': f'external:{name}', 'name': name,
                'product': None, 'target': None, 'grain': None,
                'external': True, 'attributes': [], 'key_attributes': [],
            })
        return {
            'format': 'cade.canonical-erd.v1', 'mode': 'erd',
            'scope': scope,
            'entities': entities, 'relationships': links,
            'warnings': warnings,
            'notes': ('Relationships come only from model.relationships. '
                      'External entities are placeholders, not inferred data products. '
                      'A conceptual relationship does not create a physical join or foreign-key constraint.'),
        }

    def multi_lineage(self, models: tuple[Specification, ...] | list[Specification],
                      product_name: str) -> dict[str, Any]:
        """Display the complete data-product flow without inventing new mappings.

        Each node and field is model-qualified. The upstream output-to-source
        links use Source.product_ref, which was resolved by the same parser and
        model dependency planner used for execution.
        """
        result: dict[str, Any] = {
            'format': 'cade.visual-graph.v2', 'mode': 'lineage',
            'product': product_name, 'nodes': [], 'edges': [], 'fields': [],
            'warnings': [], 'unmapped_source_columns': [],
        }
        targets = {model.product.name: model for model in models}
        for model in models:
            model_id = model.product.name[len(product_name) + 1:] if model.product.name.startswith(product_name+'.') else model.product.name
            prefix = model_id + '/'
            graph = self.lineage(model)
            result['nodes'].extend({**node, 'id': prefix + node['id'],
                                    'label': node['label'] if node['kind'] != 'target' else
                                    f'{model.canonical.entity} ({model_id})'}
                                   for node in graph['nodes'])
            result['edges'].extend({**edge,'from':prefix+edge['from'],'to':prefix+edge['to']}
                                   for edge in graph['edges'])
            for field in graph['fields']:
                result['fields'].append({**field, 'model_id':model_id,
                    'attribute':model_id + '.' + field['attribute'],
                    'rules':[{**rule,'node_ids':[prefix+node for node in rule['node_ids']]}
                             for rule in field['rules']]})
            result['warnings'].extend(f'{model_id}: {w}' for w in graph['warnings'])
            result['unmapped_source_columns'].extend(graph['unmapped_source_columns'])
            for source in model.sources:
                if source.product_ref and source.product_ref in targets:
                    upstream = targets[source.product_ref]
                    up_id = upstream.product.name[len(product_name)+1:]
                    result['edges'].append({'from':up_id+'/target:canonical',
                                             'to':prefix+'source:'+source.id,
                                             'kind':'model-reference','label':'published model'})
        result['unmapped_source_columns'] = sorted(set(result['unmapped_source_columns']))
        return result
