"""Immutable, platform-neutral intermediate representation."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Owner:
    team: str
    contact: str


@dataclass(frozen=True)
class Consumer:
    id: str
    purpose: str
    access: str


@dataclass(frozen=True)
class Product:
    name: str
    target: str
    description: str
    owner: Owner
    consumers: tuple[Consumer, ...]


@dataclass(frozen=True)
class Requirement:
    id: str
    statement: str


@dataclass(frozen=True)
class AcceptanceCriterion:
    id: str
    statement: str
    check_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Feature:
    id: str
    description: str
    requirement_ids: tuple[str, ...]
    acceptance_criteria: tuple[AcceptanceCriterion, ...]


@dataclass(frozen=True)
class Business:
    outcome: str
    requirements: tuple[Requirement, ...]
    features: tuple[Feature, ...]


@dataclass(frozen=True)
class Connection:
    id: str
    url_env: str


@dataclass(frozen=True)
class Execution:
    connection: str


@dataclass(frozen=True)
class SourceContract:
    owner: str
    freshness_hours: int


@dataclass(frozen=True)
class Refresh:
    schedule: str
    timezone: str


@dataclass(frozen=True)
class Quality:
    on_failure: str


@dataclass(frozen=True)
class Recovery:
    strategy: str
    max_attempts: int
    runbook: str


@dataclass(frozen=True)
class Operations:
    refresh: Refresh
    quality: Quality
    recovery: Recovery


@dataclass(frozen=True)
class Source:
    id: str
    table: str
    columns: tuple[str, ...]
    key: tuple[str, ...]
    alias: str
    column_contracts: tuple["Attribute", ...]
    contract: SourceContract | None
    product_ref: str | None = None
    depends_on: tuple[str, ...] = ()  # Authoritative source-to-source dependencies.
    connection: str | None = None


@dataclass(frozen=True)
class JoinCondition:
    left: Any  # Validated expression tree (JSON-compatible dictionaries)
    right: Any


@dataclass(frozen=True)
class Join:
    source: Source
    type: str
    on: tuple[JoinCondition, ...]
    expect_unique: bool = True

    @property
    def table(self) -> str:
        return self.source.table

    @property
    def alias(self) -> str:
        return self.source.alias

    @property
    def columns(self) -> tuple[str, ...]:
        return self.source.columns


@dataclass(frozen=True)
class Attribute:
    name: str
    type: str
    nullable: bool
    requirement_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanonicalRelationship:
    """A declared conceptual link, not an inferred physical join or constraint."""

    attribute: str
    entity: str
    cardinality: str
    related_attribute: str | None = None


@dataclass(frozen=True)
class CanonicalModel:
    entity: str
    grain: str
    key: tuple[str, ...]
    attributes: tuple[Attribute, ...]
    relationships: tuple[CanonicalRelationship, ...] = ()


@dataclass(frozen=True)
class Mapping:
    target: str
    expression: Any


@dataclass(frozen=True)
class Branch:
    source: Source
    joins: tuple[Join, ...]
    mappings: tuple[Mapping, ...]
    filters: tuple[dict, ...]
    priority: int


@dataclass(frozen=True)
class Sort:
    field: str
    direction: str


@dataclass(frozen=True)
class Deduplication:
    by: tuple[str, ...]
    order_by: tuple[Sort, ...]


@dataclass(frozen=True)
class Reconciliation:
    allow: tuple[str, ...]


@dataclass(frozen=True)
class Preservation:
    mode: str
    reference: str | None = None


@dataclass(frozen=True)
class BusinessTest:
    id: str
    type: str
    definition: dict[str, Any]  # One validated definition in the authoritative YAML registry.


@dataclass(frozen=True)
class Specification:
    spec_version: str
    product: Product
    canonical: CanonicalModel
    branches: tuple[Branch, ...]
    deduplicate: Deduplication | None
    reconciliation: Reconciliation
    preservation: Preservation
    business: Business
    sources: tuple[Source, ...]
    operations: Operations
    definition: dict[str, Any]  # Validated, authoritative spec; registry appears once.
    output: str | None = None
    dialect: str | None = None
    connections: tuple[Connection, ...] = ()
    execution: Execution | None = None
    tests: tuple[BusinessTest, ...] = ()

class SpecificationValidationError(ValueError):
    """The declarative contract is invalid or unsafe to compile."""

@dataclass(frozen=True)
class DataProduct:
    """The one public specification: a product containing published model units.

    A ``Specification`` is an internal, fully normalized *model build unit*;
    the compiler, dependency planner, SQL generator and executor all consume
    those same units. No extra authoring format or second SQL path is created.
    """
    name: str
    models: tuple[Specification, ...]
    definition: dict[str, Any]

    @property
    def model_ids(self) -> tuple[str, ...]:
        return tuple(spec.product.name[len(self.name) + 1:] for spec in self.models)

    def for_model(self, model_id: str) -> Specification:
        for spec in self.models:
            if spec.product.name == f'{self.name}.{model_id}':
                return spec
        raise SpecificationValidationError(f"Unknown model '{model_id}' in product '{self.name}'")
