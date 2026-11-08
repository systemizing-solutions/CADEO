"""Deterministic dependency planning for a single data product.

All references are IDs from the canonical specification. No duplicate definitions
or execution engine dependencies are introduced by the planner.
"""

from dataclasses import dataclass
import heapq
from typing import Any

from cade.models.specification import ArtifactPaths, Specification, SpecificationValidationError


@dataclass(frozen=True)
class DependencyNode:
    id: str
    kind: str
    depends_on: tuple[str, ...]
    artifact: str | None = None
    action: str = "logical"  # external | logical | sql | gate

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "depends_on": list(self.depends_on),
                "artifact": self.artifact, "action": self.action}


@dataclass(frozen=True)
class DependencyPlan:
    nodes: tuple[DependencyNode, ...]  # stable topological order

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "cade.dependency-plan.v1",
            "nodes": [node.to_dict() for node in self.nodes],
            "execution_order": [node.id for node in self.nodes],
            "steps": [node.to_dict() for node in self.nodes if node.action in ("sql", "gate")],
            "publication_gate": "quality:checks",
            "note": "Execute steps in order. Each quality-check SELECT must return zero before publication. "
                    "The staging table is materialized and quality gates block publication during cade build. "
                    "The plan alone does not execute SQL.",
        }

    def mermaid(self) -> str:
        """Safe Mermaid identifiers; labels are quoted and escaped."""
        indices = {node.id: f"n{index}" for index, node in enumerate(self.nodes)}
        lines = ["flowchart TD"]
        for node in self.nodes:
            label = f"{node.id} ({node.kind})".replace('"', "'")
            lines.append(f'    {indices[node.id]}["{label}"]')
        for node in self.nodes:
            for dependency in node.depends_on:
                lines.append(f"    {indices[dependency]} --> {indices[node.id]}")
        return "\n".join(lines) + "\n"


class DependencyService:
    """Build one DAG: registered sources → preservation → transformations → QA → publish."""

    @staticmethod
    def staging_target(specification: Specification) -> str:
        pieces = specification.product.target.split(".")
        pieces[-1] += "__dp_stage"
        return ".".join(pieces)

    @staticmethod
    def _sort(nodes: dict[str, DependencyNode]) -> tuple[DependencyNode, ...]:
        for node in nodes.values():
            missing = sorted(set(node.depends_on) - set(nodes))
            if missing:
                raise SpecificationValidationError(
                    f"Dependency {node.id}: missing dependencies {missing}")
        following = {name: [] for name in nodes}
        degree = {name: len(set(node.depends_on)) for name, node in nodes.items()}
        for node in nodes.values():
            for dependency in set(node.depends_on):
                following[dependency].append(node.id)
        ready = [name for name, count in degree.items() if count == 0]
        heapq.heapify(ready)
        ordered = []
        while ready:
            name = heapq.heappop(ready)
            ordered.append(nodes[name])
            for successor in following[name]:
                degree[successor] -= 1
                if degree[successor] == 0:
                    heapq.heappush(ready, successor)
        if len(ordered) != len(nodes):
            remaining = {name for name, count in degree.items() if count}
            # Identify and print an actual cycle, not just the blocked downstream nodes.
            def cycle_path() -> list[str]:
                active: dict[str, int] = {}
                visited = set()
                path: list[str] = []
                def walk(name: str) -> list[str] | None:
                    active[name] = len(path)
                    path.append(name)
                    for dependency in sorted(nodes[name].depends_on):
                        if dependency not in remaining:
                            continue
                        if dependency in active:
                            return path[active[dependency]:] + [dependency]
                        if dependency not in visited:
                            result = walk(dependency)
                            if result:
                                return result
                    path.pop()
                    active.pop(name)
                    visited.add(name)
                    return None
                for name in sorted(remaining):
                    if name not in visited:
                        result = walk(name)
                        if result:
                            return result
                return sorted(remaining)
            raise SpecificationValidationError(
                "Circular dependency: " + " -> ".join(cycle_path()))
        return tuple(ordered)

    def build(self, specification: Specification) -> DependencyPlan:
        product = specification.product.name
        paths = specification.artifact_paths or ArtifactPaths()
        nodes: dict[str, DependencyNode] = {}
        def add(name: str, kind: str, deps: tuple[str, ...],
                artifact: str | None = None, action: str = "logical") -> None:
            if name in nodes:
                raise SpecificationValidationError(f"Duplicate dependency node: {name}")
            nodes[name] = DependencyNode(name, kind, deps, artifact, action)

        for source in sorted(specification.sources, key=lambda s: s.id):
            add("source:" + source.id, "source", tuple("source:" + key for key in source.depends_on),
                action="external")
        source_dependencies = tuple("source:" + source.id for source in specification.sources)
        add("preserve:raw", "preservation", source_dependencies, f"{paths.directory('preservation')}/{product}.preserve.sql",
            "sql" if specification.preservation.mode == "snapshot" else "external")
        for index, branch in enumerate(specification.branches):
            source_ids = {branch.source.id, *(join.source.id for join in branch.joins)}
            dependencies = ("preserve:raw",) + tuple("source:" + key for key in sorted(source_ids))
            add(f"transform:branch:{index}", "transformation", dependencies)
        add("transform:canonical", "transformation",
            tuple(f"transform:branch:{i}" for i in range(len(specification.branches))),
            f"{paths.directory('transformations')}/{product}.sql", "sql")
        add("quality:audit", "quality-check", ("transform:canonical",), f"{paths.directory('quality')}/{product}.audit.sql", "sql")
        add("quality:checks", "quality-check", ("quality:audit",), f"{paths.directory('quality')}/{product}.checks.sql", "gate")
        add("publish:product", "publication", ("quality:checks",), f"{paths.directory('publication')}/{product}.publish.sql", "sql")
        return DependencyPlan(self._sort(nodes))
