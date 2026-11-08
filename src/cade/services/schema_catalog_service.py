"""Workspace-scoped catalogue for the one versioned schema reference in YAML.

The YAML ``schema.path`` and ``schema.version`` are authoritative.  A browser
selection edits those fields; it does not override validation at execution time.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path, PureWindowsPath
import re
from typing import Any

from jsonschema import Draft202012Validator

from cade.models.specification import SpecificationValidationError


class SchemaCatalogService:
    DEFAULT = "schemas/product-v0.8.3.schema.json"
    VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
    DRAFT = "https://json-schema.org/draft/2020-12/schema"

    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)

    @staticmethod
    def relative_path(value: str) -> Path:
        if (not isinstance(value, str) or not value.strip() or "\\" in value
                or "://" in value or Path(value).is_absolute()
                or PureWindowsPath(value).drive or not value.lower().endswith(".json")):
            raise SpecificationValidationError(
                "schema.path must be a local relative .json file path (no URLs or absolute paths)")
        return Path(value)

    def path(self, choice: str) -> Path:
        candidate = (self.root / self.relative_path(choice)).resolve()
        if not candidate.is_relative_to(self.root):
            raise SpecificationValidationError("JSON Schema path escapes the editor workspace")
        return candidate

    @classmethod
    def validate_schema(cls, data: Any) -> str:
        if not isinstance(data, dict) or data.get('type') != 'object':
            raise SpecificationValidationError("A product JSON Schema must describe a root object")
        if data.get('$schema') != cls.DRAFT:
            raise SpecificationValidationError("Only JSON Schema Draft 2020-12 is supported")
        version = data.get('x-schema-version')
        if not isinstance(version, str) or not cls.VERSION_PATTERN.fullmatch(version):
            raise SpecificationValidationError("JSON Schema requires x-schema-version (semver, e.g. 1.0.0)")
        identity = data.get('$id')
        if not isinstance(identity, str) or not identity.endswith(version):
            raise SpecificationValidationError("JSON Schema $id must end with x-schema-version")
        if ('schema' not in data.get('required', []) or
                'schema' not in data.get('properties', {})):
            raise SpecificationValidationError("Selected JSON Schema must require the root schema reference")

        def check_local_refs(value: Any) -> None:
            if isinstance(value, dict):
                for key, entry in value.items():
                    if key in ('$ref', '$dynamicRef', '$recursiveRef'):
                        if key != '$ref' or not isinstance(entry, str) or not entry.startswith('#/'):
                            raise SpecificationValidationError("Only local #/ JSON Schema references are supported")
                        target = data
                        for token in entry[2:].split('/'):
                            token = token.replace('~1', '/').replace('~0', '~')
                            if not isinstance(target, dict) or token not in target:
                                raise SpecificationValidationError(f"Unresolved JSON Schema reference: {entry}")
                            target = target[token]
                    check_local_refs(entry)
            elif isinstance(value, list):
                for entry in value:
                    check_local_refs(entry)

        check_local_refs(data)
        try:
            Draft202012Validator.check_schema(data)
        except Exception as error:
            raise SpecificationValidationError(f"Invalid JSON Schema: {error}") from error
        return json.dumps(data, indent=2, ensure_ascii=False) + '\n'

    def load(self, choice: str, files: Any) -> str:
        path = self.path(choice)
        if not path.is_file():
            raise SpecificationValidationError(f"Referenced JSON Schema does not exist: {choice}")
        try:
            return self.validate_schema(json.loads(files.read_text(path)))
        except (ValueError, UnicodeError) as error:
            if isinstance(error, SpecificationValidationError):
                raise
            raise SpecificationValidationError(f"Invalid JSON Schema file {choice}: {error}") from error

    def ensure_builtin(self, files: Any, *, overwrite: bool = False) -> str:
        """Install the bundled schema, preserving existing revisions unless requested."""
        from cade.services.specification_service import SpecificationService
        destination = self.path(self.DEFAULT)
        bundled = files.read_bundled_text(SpecificationService.SCHEMA_PATH)
        if overwrite or not destination.exists():
            files.write_text(destination, bundled, overwrite=overwrite)
        self.load(self.DEFAULT, files)
        return self.DEFAULT

    def list(self) -> list[str]:
        from cade.brokers.file_broker import FileBroker
        files = FileBroker()
        choices = []
        for path in self.root.rglob('*.json'):
            if not path.is_file() or not path.resolve().is_relative_to(self.root):
                continue
            name = path.relative_to(self.root).as_posix()
            try:
                self.load(name, files)
            except (OSError, ValueError, SpecificationValidationError):
                continue
            choices.append(name)
        return sorted(choices)

    def save(self, choice: str, data: dict, files: Any) -> str:
        path = self.path(choice)
        text = self.validate_schema(data)
        files.write_text(path, text, overwrite=False)
        return path.relative_to(self.root).as_posix()

    def from_document(self, yaml_path: Path, document: dict, files: Any) -> str:
        """Resolve the single YAML reference within the workspace and check its pin."""
        reference = document.get('schema') if isinstance(document, dict) else None
        if not isinstance(reference, dict):
            raise SpecificationValidationError("schema.path and schema.version are required")
        relative = self.relative_path(reference.get('path'))
        target = (yaml_path.parent / relative).resolve()
        if not target.is_relative_to(self.root):
            raise SpecificationValidationError("JSON Schema reference escapes the editor workspace")
        choice = target.relative_to(self.root).as_posix()
        loaded = json.loads(self.load(choice, files))
        if loaded['x-schema-version'] != reference.get('version'):
            raise SpecificationValidationError(
                f"schema.version mismatch: YAML pins {reference.get('version')!r}, "
                f"but {choice} declares {loaded['x-schema-version']!r}")
        return choice

    def to_document(self, yaml_path: Path, choice: str, document: dict, files: Any) -> dict:
        """Apply an explicit editor selection to YAML, including its pinned version."""
        selected = json.loads(self.load(choice, files))
        path = self.path(choice)
        relative = Path(os.path.relpath(path, yaml_path.parent)).as_posix()
        updated = copy.deepcopy(document)
        updated['schema'] = {'path': relative, 'version': selected['x-schema-version']}
        return updated
