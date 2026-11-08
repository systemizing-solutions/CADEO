"""Read-only, position-aware diagnostics for a YAML editor.

Always delegates contract and semantic validation to SpecificationService;
only document-position lookup lives here.
"""
from __future__ import annotations

import re
import yaml

from cade.services.specification_service import UniqueKeysLoader


def locate_path(text: str, path: str) -> tuple[int, int]:
    """Best-effort mark of the schema field or its closest existing parent."""
    try:
        root = yaml.compose(text, Loader=UniqueKeysLoader)
    except yaml.YAMLError:
        return 1, 1
    if root is None:
        return 1, 1
    node = root
    mark = root.start_mark
    # Jsonschema paths use dot syntax and numeric array selectors.
    parts = re.findall(r"[^.\[\]]+|\[([0-9]+)\]", path.removeprefix('$.').removeprefix('$'))
    # re.findall with a capture yields only indices; use finditer instead.
    parts = [m.group(0).strip('[]') for m in re.finditer(r"[^.\[\]]+|\[[0-9]+\]", path.removeprefix('$.').removeprefix('$'))]
    for part in parts:
        if isinstance(node, yaml.MappingNode):
            hit = next(((k, v) for k, v in node.value if isinstance(k, yaml.ScalarNode) and k.value == part), None)
            if hit is None:
                break
            mark = hit[0].start_mark
            node = hit[1]
        elif isinstance(node, yaml.SequenceNode) and part.isdigit() and int(part) < len(node.value):
            node = node.value[int(part)]
            mark = node.start_mark
        else:
            break
    return mark.line + 1, mark.column + 1


def syntax_findings(text: str):
    try:
        document = yaml.load(text, Loader=UniqueKeysLoader)
    except yaml.YAMLError as error:
        mark = getattr(error, 'problem_mark', None) or getattr(error, 'context_mark', None)
        line, column = ((mark.line + 1, mark.column + 1) if mark else (1, 1))
        return None, [{'path': '$', 'line': line, 'column': column, 'severity': 'error',
                       'message': str(getattr(error, 'problem', None) or error),
                       'suggestion': 'Correct the YAML syntax or duplicate key, then validate again.'}]
    if not isinstance(document, dict):
        return None, [{'path': '$', 'line': 1, 'column': 1, 'severity': 'error',
                       'message': 'The YAML document must contain a mapping at its root.',
                       'suggestion': 'Start with schema, spec_version and the six workflow sections.'}]
    return document, []


def positioned_findings(text: str, findings: list[dict]):
    return [{**finding, 'line': locate_path(text, finding.get('path', '$'))[0],
             'column': locate_path(text, finding.get('path', '$'))[1]}
            for finding in findings]
