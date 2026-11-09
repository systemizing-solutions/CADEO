"""Project business assurance evidence, derived from one IR and an optional run manifest.

There are no authored acceptance or runtime definitions here. The specification is
authoritative for requirements, features and tests; ExecutionService is authoritative
for observed check results, reconciliation and publication. Missing evidence is NOT
considered a pass. A run for an older specification is NEVER represented as current.
"""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from typing import Any

from cadeo.models.specification import Specification, SpecificationValidationError
from cadeo.services.traceability_service import TraceabilityService


AUDIT_METRICS = (
    'duplicate_source_keys', 'null_source_keys', 'inner_unmatched',
    'filtered', 'deduplicated', 'join_fanout', 'multiplied',
)
NO_RUN_WARNING = ('No local/data run evidence supplied. A local CADEO run using the configured connection (e.g. SQLite) is highly recommended; '
                  'if skipped, ensure a data run is completed in development before release.')


def specification_fingerprint(spec: Specification) -> str:
    """Stable identity of the validated product IR, independent of YAML formatting."""
    payload = json.dumps(asdict(spec), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return sha256(payload.encode('utf-8')).hexdigest()


class EvidenceService:
    """Read-only projection; never executes a SQL statement or changes the YAML."""

    def build(self, spec: Specification, manifest: dict[str, Any] | None = None,
              manifest_path: str | None = None) -> dict[str, Any]:
        trace = TraceabilityService().build(spec)
        name = spec.product.name
        if manifest is not None:
            if not isinstance(manifest, dict) or manifest.get('format') != 'cadeo.run.v1':
                raise SpecificationValidationError('Evidence requires a valid CADEO run manifest')
            if name not in manifest.get('products', []):
                raise SpecificationValidationError(f'Run manifest does not contain product {name}')
        fingerprint = specification_fingerprint(spec)
        original = ((manifest or {}).get('specification_hashes') or {}).get(name)
        # Historical unversioned manifests cannot prove that the YAML is unchanged.
        current = manifest is not None and original == fingerprint
        stale = manifest is not None and not current
        result_by_id: dict[str, dict] = {}
        anomalies: list[str] = []
        if current:
            for item in manifest.get('tests', []):
                if item.get('product') == name:
                    check_id = item.get('id')
                    if check_id in result_by_id:
                        anomalies.append(f'Duplicate runtime result for {check_id}')
                    result_by_id[check_id] = item

        checks: list[dict[str, Any]] = []
        for check in trace['quality_checks']:
            check_id = check['id']
            observed = result_by_id.get(check_id)
            status = observed.get('status') if observed else 'not_run'
            failures = observed.get('failures') if observed else None
            if status not in ('passed', 'failed', 'not_run'):
                anomalies.append(f'Invalid runtime status for {check_id}')
                status, failures = 'not_run', None
            if status == 'passed' and (type(failures) is not int or failures != 0):
                anomalies.append(f'Contradictory pass result for {check_id}')
                status = 'failed'
            if status == 'failed' and (type(failures) is not int or failures < 0):
                anomalies.append(f'Missing failure count for {check_id}')
                failures = None
            if status == 'not_run':
                failures = None
            checks.append({'id': check_id, 'type': check['type'], 'status': status,
                           'failures': failures, 'asset': check['asset']})
        check_by_id = {check['id']: check for check in checks}
        for check_id in result_by_id:
            if check_id not in check_by_id:
                anomalies.append(f'Run contains an unknown check ID: {check_id}')

        rows: list[dict[str, Any]] = []
        used_checks: set[str] = set()
        for requirement in spec.business.requirements:
            linked_features = [f for f in spec.business.features if requirement.id in f.requirement_ids]
            if not linked_features:
                rows.append(self._row(requirement, None, None, None))
                continue
            for feature in linked_features:
                if not feature.acceptance_criteria:
                    rows.append(self._row(requirement, feature, None, None))
                for criterion in feature.acceptance_criteria:
                    if not criterion.check_ids:
                        rows.append(self._row(requirement, feature, criterion, None))
                    for check_id in criterion.check_ids:
                        used_checks.add(check_id)
                        rows.append(self._row(requirement, feature, criterion, check_by_id[check_id]))
        unlinked = [check for check in checks if check['id'] not in used_checks]

        reconciliation: list[dict[str, Any]] = []
        blockers: list[dict[str, Any]] = []
        if current:
            for step in manifest.get('steps', []):
                if step.get('node') == f'product:{name}:quality:audit':
                    for audit in step.get('audit', []):
                        for metric in AUDIT_METRICS:
                            count = audit.get(metric) or 0
                            if type(count) not in (int, float) or count < 0:
                                anomalies.append(f'Invalid reconciliation metric: {metric}')
                                continue
                            if not count:
                                continue
                            allowed = (metric in spec.reconciliation.allow or
                                       (metric == 'join_fanout' and 'multiplied' in spec.reconciliation.allow))
                            reconciliation.append({'branch': audit.get('branch'), 'metric': metric,
                                                   'count': count, 'allowed': allowed,
                                                   'status': 'allowed' if allowed else 'blocked'})
                            if not allowed:
                                blockers.append({'kind': 'reconciliation', 'id': metric,
                                                 'message': f'Branch {audit.get("branch")}: {metric} = {count} (not permitted)'})
        if stale:
            blockers.append({'kind': 'stale', 'id': 'specification',
                             'message': 'Run evidence cannot be matched to the current specification; rebuild to re-prove it.'})
        if current:
            for check in checks:
                if check['status'] == 'failed':
                    blockers.append({'kind': 'quality', 'id': check['id'],
                                     'message': f'Quality check {check["id"]} failed ({check["failures"]} failures)'})
            for step in manifest.get('steps', []):
                if step.get('status') == 'failed' and step.get('node', '').startswith(f'product:{name}:'):
                    blockers.append({'kind': 'execution', 'id': step['node'],
                                     'message': step.get('error') or f'Execution failed: {step["node"]}'})
            if manifest.get('status') == 'failed' and not blockers:
                blockers.append({'kind': 'build', 'id': 'build',
                                 'message': manifest.get('error') or 'Build did not complete successfully'})
            if manifest.get('status') == 'passed':
                missing = [item['id'] for item in checks if item['status'] == 'not_run']
                if missing:
                    blockers.append({'kind': 'evidence', 'id': 'missing_checks',
                                     'message': 'Run claims success without results for: ' + ', '.join(missing)})
                if not any(step.get('node') == f'product:{name}:quality:audit' and
                           step.get('status') == 'passed' for step in manifest.get('steps', [])):
                    blockers.append({'kind': 'evidence', 'id': 'missing_audit',
                                     'message': 'Run claims success without recorded reconciliation audit'})
                if name not in manifest.get('publication', []):
                    blockers.append({'kind': 'publication', 'id': name,
                                     'message': 'Build completed without publishing this product'})
        published = bool(current and manifest.get('status') == 'passed' and
                         name in manifest.get('publication', []) and not blockers)
        summary = {status: sum(check['status'] == status for check in checks)
                   for status in ('passed', 'failed', 'not_run')}
        return {
            'format': 'cadeo.evidence.v1', 'product': name, 'target': spec.product.target,
            'run_id': (manifest or {}).get('run_id'), 'run_status': (manifest or {}).get('status', 'not_run'),
            'manifest_path': manifest_path, 'started_at': (manifest or {}).get('started_at'),
            'current': bool(current), 'stale': bool(stale),
            'evidence_status': ('not_run' if manifest is None else
                                'stale' if stale else 'published' if published else 'blocked'),
            'publication': {'status': ('not_run' if manifest is None else 'stale' if stale else
                                       'published' if published else 'blocked'),
                            'target': spec.product.target},
            'summary': {'requirements': len(spec.business.requirements),
                        'features': len(spec.business.features),
                        'acceptance_criteria': sum(len(f.acceptance_criteria) for f in spec.business.features),
                        'checks': len(checks), **summary},
            'matrix': rows, 'checks': checks, 'unlinked_checks': unlinked,
            'reconciliation_exceptions': reconciliation, 'reconciliation_status': (
                'not_run' if not current or not any(step.get('node') == f'product:{name}:quality:audit' and
                    step.get('status') == 'passed' for step in manifest['steps']) else 'reviewed'),
            'publication_blockers': blockers,
            'traceability_warnings': trace['warnings'], 'evidence_warnings': anomalies + (
                ['The selected run predates or does not match the validated product definition.'] if stale else
                [NO_RUN_WARNING] if manifest is None else []),
        }

    @staticmethod
    def _row(requirement, feature, criterion, check) -> dict[str, Any]:
        return {
            'requirement_id': requirement.id, 'requirement': requirement.statement,
            'feature_id': feature.id if feature else None,
            'feature': feature.description if feature else None,
            'acceptance_id': criterion.id if criterion else None,
            'acceptance': criterion.statement if criterion else None,
            'check_id': check['id'] if check else None,
            'check_type': check['type'] if check else None,
            'status': check['status'] if check else 'not_run',
            'failures': check['failures'] if check else None,
        }

    def markdown(self, evidence: dict[str, Any]) -> str:
        """Human-readable companion to the JSON projection, never a separate result store."""
        def cell(value: Any) -> str:
            return str(value if value is not None else '—').replace('|', '\\|').replace('\n', ' ')
        lines = [f'# Business acceptance evidence — {evidence["product"]}', '',
                 f'Run: `{evidence["run_id"] or "not_run"}` · Publication: **{evidence["publication"]["status"]}**', '',
                 '| Requirement | Feature | Acceptance criterion | Check | Result | Failures |',
                 '|---|---|---|---|---|---|']
        for row in evidence['matrix']:
            lines.append('| ' + ' | '.join(cell(row[k]) for k in
                ('requirement_id','feature_id','acceptance_id','check_id','status','failures')) + ' |')
        lines.extend(['', '## Reconciliation exceptions', ''])
        for item in evidence['reconciliation_exceptions']:
            lines.append(f'- Branch {item["branch"]}: {item["metric"]} = {item["count"]} ({item["status"]})')
        if not evidence['reconciliation_exceptions']:
            lines.append('None recorded; reconciliation status: ' + evidence['reconciliation_status'])
        lines.extend(['', '## Publication blockers', ''])
        lines.extend(f'- {item["message"]}' for item in evidence['publication_blockers'])
        if not evidence['publication_blockers']:
            lines.append('None recorded.' if evidence['current'] else 'No current run evidence.')
        lines.extend(['', '## Evidence notes', ''])
        lines.extend(f'- {w}' for w in evidence['evidence_warnings'])
        if not evidence['evidence_warnings']:
            lines.append('Evidence is derived from the validated specification and run manifest.')
        return '\n'.join(lines) + '\n'

    def aggregate(self, product, manifest: dict | None, manifest_path: str | None = None) -> dict:
        """One evidence projection across all model-level gates in a data product.

        Individual check results remain in the authoritative execution manifest;
        the UI merely adds model-qualified labels so IDs never collide.
        """
        reports = [self.build(model, manifest, manifest_path) for model in product.models]
        combined = {
            'format':'cadeo.multi-model-evidence.v1', 'product':product.name,
            'models':[{'id':model_id, 'target':model.product.target}
                      for model_id,model in zip(product.model_ids,product.models)],
            'run_id':(manifest or {}).get('run_id'),
            'run_status':(manifest or {}).get('status','not_run'),
            'manifest_path':manifest_path,
            'current':all(report['current'] for report in reports),
            'stale':any(report['stale'] for report in reports),
            'matrix':[], 'checks':[], 'unlinked_checks':[],
            'reconciliation_exceptions':[], 'publication_blockers':[],
            'traceability_warnings':[], 'evidence_warnings':[],
            'summary':{key:sum(r['summary'].get(key,0) for r in reports)
                       for key in ('requirements','features','acceptance_criteria',
                                   'checks','passed','failed','not_run')},
        }
        # Requirements/features are shared across model units; count their
        # authoritative declarations once, not once per model.
        if reports:
            combined['summary']['requirements'] = reports[0]['summary']['requirements']
            combined['summary']['features'] = reports[0]['summary']['features']
            combined['summary']['acceptance_criteria'] = reports[0]['summary']['acceptance_criteria']
        for model_id, report in zip(product.model_ids, reports):
            for key in ('matrix','checks','unlinked_checks','reconciliation_exceptions'):
                for item in report[key]:
                    # An acceptance criterion is defined once at product scope.
                    # Another model's criterion was intentionally filtered out
                    # of this model's check registry; do not manufacture a
                    # second not_run matrix row for that unrelated model.
                    if key == 'matrix' and item.get('acceptance_id') and not item.get('check_id'):
                        original = next((ac for feature in product.definition['definition']['features']
                            for ac in feature['acceptance_criteria']
                            if ac['id'] == item['acceptance_id']), None)
                        if original and original.get('check_ids'):
                            continue
                        if model_id != product.model_ids[0]:
                            continue
                    entry = {**item,'model_id':model_id}
                    if 'check_id' in entry and entry['check_id']:
                        entry['check_id'] = model_id + '/' + entry['check_id']
                    if 'id' in entry and key in ('checks','unlinked_checks'):
                        entry['id'] = model_id + '/' + entry['id']
                    combined[key].append(entry)
            combined['publication_blockers'].extend({**blocker,'model_id':model_id}
                for blocker in report['publication_blockers'])
            combined['traceability_warnings'].extend(f'{model_id}: {w}' for w in report['traceability_warnings'])
            combined['evidence_warnings'].extend(f'{model_id}: {w}' for w in report['evidence_warnings']
                if w != NO_RUN_WARNING)
        if manifest is None:
            combined['evidence_warnings'].append(NO_RUN_WARNING)
        combined['publication'] = {'status':(
            'not_run' if manifest is None else 'stale' if combined['stale'] else
            'published' if all(r['publication']['status']=='published' for r in reports) else 'blocked'),
            'target':', '.join(m.product.target for m in product.models)}
        combined['evidence_status'] = combined['publication']['status']
        return combined
