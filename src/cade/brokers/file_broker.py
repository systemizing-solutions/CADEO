"""Isolate filesystem and packaged resource access from business logic."""

from importlib.resources import files as package_files
from pathlib import Path
from typing import Protocol


class FileBrokerContract(Protocol):
    def read_text(self, path: Path) -> str: ...
    def read_bundled_text(self, relative_path: str) -> str: ...
    def exists(self, path: Path) -> bool: ...
    def find_yaml_files(self, path: Path) -> tuple[Path, ...]: ...
    def write_text(self, path: Path, content: str, overwrite: bool = True) -> None: ...


class FileBroker:
    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def read_bundled_text(self, relative_path: str) -> str:
        return package_files("cade").joinpath(relative_path).read_text(encoding="utf-8")

    def exists(self, path: Path) -> bool:
        return path.exists()

    def find_yaml_files(self, path: Path) -> tuple[Path, ...]:
        """Discover YAML files in deterministic order; do not traverse linked directories."""
        if path.is_file():
            return (path,)
        if not path.is_dir():
            raise FileNotFoundError(f"No such specification file or directory: {path}")
        matches = tuple(sorted(
            item for item in path.rglob("*")
            if item.is_file() and item.suffix.lower() in (".yaml", ".yml")
        ))
        if not matches:
            raise FileNotFoundError(f"No .yaml or .yml specification files found in: {path}")
        return matches

    def write_text(self, path: Path, content: str, overwrite: bool = True) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not overwrite:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
            return
        path.write_text(content, encoding="utf-8", newline="\n")
