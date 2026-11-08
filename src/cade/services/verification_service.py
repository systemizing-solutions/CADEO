"""Read-only quality verification through the single SQLAlchemy execution broker."""

from pathlib import Path
from sqlalchemy.engine import URL

from cade.brokers.execution_broker import ExecutionBroker
from cade.models.specification import SpecificationValidationError
from cade.services.specification_service import SpecificationService
from cade.services.dialect_service import DialectService
from cade.services.sql_service import SqlTemplateService


class VerificationService:
    def __init__(self, specifications: SpecificationService, broker: ExecutionBroker):
        self.specifications = specifications
        self.broker = broker

    def verify(self, spec_path: Path, database: Path) -> list[dict]:
        """Verify every model in DAG order, stop on first failed check.

        No SQLite-specific DBAPI path; SQLAlchemy executes all statements.
        Future unexecuted models are explicitly not_run, never treated as passed.
        """
        if not database.is_file():
            raise SpecificationValidationError(f'Verification database does not exist: {database}')
        product = self.specifications.load(spec_path)
        compiler = DialectService.create('sqlite')
        url = URL.create('sqlite',database=str(database.resolve())).render_as_string(hide_password=False)
        blocked = False
        reports: list[dict] = []
        with self.broker.transaction(url) as connection:
            for model, model_id in zip(product.models, product.model_ids):
                ids = tuple(identifier for identifier, _ in SqlTemplateService().check_statements(model))
                counts: list[int | None] = [None] * len(ids)
                audit: list[tuple] = []
                if not blocked:
                    for index, (_, count) in enumerate(self.broker.check_failures(
                            connection, compiler.generate_checks(model), 'sqlite', ids)):
                        counts[index] = count
                        if count:
                            blocked = True
                            break
                    if not blocked:
                        for statement in self.broker.statements(compiler.generate_audit(model),'sqlite'):
                            audit.extend(tuple(row) for row in connection.exec_driver_sql(statement).fetchall())
                reports.append({'model':model_id,'product':model.product.name,
                    'checks':[{'id':model_id+'/'+id,'failures':count,
                               'status':'not_run' if count is None else 'failed' if count else 'passed',
                               'passed':None if count is None else count == 0}
                              for id,count in zip(ids,counts)],
                    'audit':audit})
        return reports
