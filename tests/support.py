"""Test database context uses the production SQLAlchemy execution broker."""

from contextlib import contextmanager
from pathlib import Path

from cadeo.brokers.execution_broker import ExecutionBroker


@contextmanager
def sqlalchemy_connection(database: Path | str = ":memory:"):
    if str(database) == ":memory:":
        url = "sqlite:///:memory:"
    else:
        path = Path(database)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        url = f"sqlite:///{path}"
    with ExecutionBroker().transaction(url) as connection:
        yield connection


def split_controlled_test_sql(sql: str, dialect: str = "sqlite") -> tuple[str, ...]:
    """Test-only stub for generated fixture scripts; never used for product SQL."""
    assert dialect == "sqlite"
    return tuple(part.strip() for part in sql.split(";\n") if part.strip())


def write_document(path: Path, document: dict) -> None:
    """Write a portable test YAML and the same versioned schema CLI init copies."""
    import copy
    import yaml
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    from cadeo.exposers.cli import VERSION
    reference = path.with_suffix(f'.schema-v{VERSION}.json')
    template = Path(__file__).resolve().parents[1] / 'src/cadeo/schemas/product.schema.json'
    reference.write_text(template.read_text())
    new_document = copy.deepcopy(document)
    new_document['schema'] = {'path': reference.name, 'version': VERSION}
    new_document['spec_version'] = VERSION
    path.write_text(yaml.safe_dump(new_document, sort_keys=False))
