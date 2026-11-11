"""Emit one Spark-syntax intermediate SQL program from the validated product IR.

This is NOT a dialect renderer. SQLGlot is the sole SQL output generator.
"""

from dataclasses import replace

from cadeo.models.specification import Branch, Specification

SPARK_TYPES = {
    "string": "STRING", "integer": "INT", "float": "DOUBLE", "boolean": "BOOLEAN",
    "date": "DATE", "timestamp": "TIMESTAMP",
}


class SqlTemplateService:
    """One dialect-neutral SQL program, expressed in the Spark input grammar.

    A build ID selects fresh, immutable staging and snapshot names per execution.
    This keeps a previously published view working until the quality gate passes.
    """

    def __init__(self, build_id: str | None = None) -> None:
        self.build_id = build_id

    def staging_target(self, spec: Specification) -> str:
        from cadeo.services.dependency_service import DependencyService
        base = DependencyService.staging_target(spec)
        return base + ("_" + self.build_id if self.build_id else "")

    def snapshot_target(self, spec: Specification, branch: int, alias: str) -> str:
        qualified = spec.product.target.split(".")
        qualified[-1] += f"__raw_{branch}_{alias}"
        if self.build_id:
            qualified[-1] += "_" + self.build_id
        return ".".join(qualified)


    @staticmethod
    def quote(name: str) -> str:
        return ".".join("`" + part + "`" for part in name.split("."))

    def literal(self, value) -> str:
        if value is None:
            return "NULL"
        if type(value) is bool:
            return "TRUE" if value else "FALSE"
        if isinstance(value, str):
            return "'" + value.replace("'", "''") + "'"
        return str(value)

    def expression(self, expression: dict) -> str:
        op, value = next(iter(expression.items()))
        if op == "column":
            return self.quote(value)
        if op == "literal":
            return self.literal(value)
        if op == "call":
            return value["name"] + "(" + ", ".join(self.expression(item) for item in value["args"]) + ")"
        if op == "registry":
            from cadeo.services.function_registry_service import FunctionRegistryService
            return FunctionRegistryService.render(value, self)
        if op == "concat":
            args = [self.expression(item) for item in value]
            return "CONCAT(" + ", ".join(args) + ")"
        if op == "coalesce":
            return "COALESCE(" + ", ".join(self.expression(item) for item in value) + ")"
        if op == "case":
            parts = [f"WHEN {self.predicate(item['if'])} THEN {self.expression(item['then'])}"
                     for item in value["when"]]
            return "(CASE " + " ".join(parts) + f" ELSE {self.expression(value['else'])} END)"
        raise ValueError(f"Unexpected expression: {op}")

    def predicate(self, predicate: dict) -> str:
        op, value = next(iter(predicate.items()))
        operators = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
        if op in operators:
            return f"({self.expression(value[0])} {operators[op]} {self.expression(value[1])})"
        if op in ("and", "or"):
            return "(" + (f" {op.upper()} ").join(self.predicate(item) for item in value) + ")"
        if op == "not":
            return f"(NOT {self.predicate(value)})"
        if op in ("is_null", "is_not_null"):
            return f"({self.expression(value)} IS {'NOT ' if op == 'is_not_null' else ''}NULL)"
        raise ValueError(f"Unexpected predicate: {op}")

    def _source(self, branch: Branch) -> str:
        return self.quote(branch.source.table) + " AS " + self.quote(branch.source.alias)

    def _join_from(self, branch: Branch) -> str:
        text = self._source(branch)
        for join in branch.joins:
            clauses = [f"{self.expression(c.left)} = {self.expression(c.right)}" for c in join.on]
            text += (f"\n    {join.type.upper()} JOIN {self.quote(join.table)} AS {self.quote(join.alias)}"
                     f" ON " + " AND ".join(clauses))
        return text

    def _source_key_column(self, branch: Branch, field: str) -> str:
        return self.quote(f"{branch.source.alias}.{field}")

    def _source_key(self, branch: Branch) -> str:
        """Collision-free, nullable composite key, using only ordinary SQL functions.

        Each component uses a null marker or a character-count-prefixed string.
        Unlike delimiter-only CONCAT, this cannot collapse distinct key tuples.
        """
        parts = []
        for field in branch.source.key:
            column = self._source_key_column(branch, field)
            rendered = f"CAST({column} AS STRING)"
            parts.append(
                f"(CASE WHEN {column} IS NULL THEN 'N' ELSE "
                f"CONCAT('V', CAST(LENGTH({rendered}) AS STRING), ':', {rendered}) END)"
            )
        return parts[0] if len(parts) == 1 else "CONCAT(" + ", ".join(parts) + ")"

    def _ctes(self, spec: Specification) -> str:
        ctes = []
        names = [self.quote(a.name) for a in spec.canonical.attributes]
        for index, branch in enumerate(spec.branches):
            nulls = " OR ".join(self._source_key_column(branch, field) + " IS NULL"
                                for field in branch.source.key)
            ctes.append(f"b{index}_raw AS (\n"
                        f"    SELECT {index} AS __dp_branch, {self._source_key(branch)} AS __dp_source_key,"
                        f" CASE WHEN {nulls} THEN 1 ELSE 0 END AS __dp_null_key\n"
                        f"    FROM {self._source(branch)}\n)")
            joined_from = self._join_from(branch)
            ctes.append(f"b{index}_joined AS (\n"
                        f"    SELECT {index} AS __dp_branch, {self._source_key(branch)} AS __dp_source_key\n"
                        f"    FROM {joined_from}\n)")
            projections = []
            for attr, mapping in zip(spec.canonical.attributes, branch.mappings):
                value = self.expression(mapping.expression)
                projections.append(f"CAST({value} AS {SPARK_TYPES[attr.type]}) AS {self.quote(attr.name)}")
            fields = ",\n        ".join(projections)
            where = ("\n    WHERE " + " AND ".join(self.predicate(p) for p in branch.filters)) if branch.filters else ""
            ctes.append(f"b{index}_selected AS (\n    SELECT\n        {fields},\n"
                        f"        {index} AS __dp_branch,\n"
                        f"        {branch.priority} AS __dp_priority,\n"
                        f"        {self._source_key(branch)} AS __dp_source_key\n"
                        f"    FROM {joined_from}{where}\n)")
        ctes.append("all_rows AS (\n    " + "\n    UNION ALL\n    ".join(
            f"SELECT * FROM b{i}_selected" for i in range(len(spec.branches))) + "\n)")
        if spec.deduplicate:
            by = ", ".join(self.quote(name) for name in spec.deduplicate.by)
            order = ["__dp_priority ASC"] + [f"{self.quote(s.field)} {s.direction.upper()}"
                                                   for s in spec.deduplicate.order_by]
            window = f"PARTITION BY {by} ORDER BY " + ", ".join(order)
            ctes.append("ranked AS (\n    SELECT *,\n"
                        f"        ROW_NUMBER() OVER ({window}) AS __dp_row_number,\n"
                        f"        DENSE_RANK() OVER ({window}) AS __dp_tie_rank\n"
                        "    FROM all_rows\n)")
            ctes.append("final_rows AS (SELECT * FROM ranked WHERE __dp_row_number = 1)")
        else:
            ctes.append("final_rows AS (SELECT * FROM all_rows)")
        return "WITH\n" + ",\n".join(ctes)

    def generate(self, spec: Specification) -> str:
        """Materialize a stable staging table; publication is a separate gated step."""
        relation = self.staging_target(spec)
        attrs = ", ".join(self.quote(attr.name) for attr in spec.canonical.attributes)
        return (f"CREATE TABLE {self.quote(relation)} AS\n{self._ctes(spec)}\n"
                f"SELECT {attrs} FROM final_rows;\n")

    def generate_publication(self, spec: Specification, replace: bool = False) -> str:
        columns = ", ".join(self.quote(a.name) for a in spec.canonical.attributes)
        return (f"CREATE {'OR REPLACE ' if replace else ''}VIEW {self.quote(spec.product.target)} AS\n"
                f"SELECT {columns} FROM {self.quote(self.staging_target(spec))};\n")

    def _summary_ctes(self, spec: Specification) -> str:
        """Trace individual source keys through joins, filters, and final selection."""
        ctes = [self._ctes(spec)]
        pairs = [("raw", "b{}_raw"), ("joined", "b{}_joined"),
                 ("selected", "b{}_selected")]
        for name, template in pairs:
            union = "\n    UNION ALL\n    ".join(
                f"SELECT __dp_branch, __dp_source_key{', __dp_null_key' if name == 'raw' else ''} FROM {template.format(i)}"
                for i in range(len(spec.branches)))
            if name == "raw":
                ctes.append("raw_counts AS (SELECT __dp_branch, __dp_source_key,"
                            " COUNT(*) AS n, MAX(__dp_null_key) AS null_key\n"
                            f" FROM ({union}) AS original_rows GROUP BY __dp_branch, __dp_source_key)")
            else:
                ctes.append(f"{name}_counts AS (SELECT __dp_branch, __dp_source_key, COUNT(*) AS n\n"
                            f" FROM ({union}) AS rows_at_stage GROUP BY __dp_branch, __dp_source_key)")
        ctes.append("final_counts AS (SELECT __dp_branch, __dp_source_key, COUNT(*) AS n\n"
                    "    FROM final_rows GROUP BY __dp_branch, __dp_source_key)")
        ctes.append("key_audit AS (\n"
                    "    SELECT r.__dp_branch, r.__dp_source_key, r.n AS raw_n, r.null_key,\n"
                    "           COALESCE(j.n, 0) AS joined_n, COALESCE(s.n, 0) AS selected_n,"
                    " COALESCE(f.n, 0) AS final_n\n"
                    "    FROM raw_counts AS r\n"
                    "    LEFT JOIN joined_counts AS j ON r.__dp_branch = j.__dp_branch"
                    " AND r.__dp_source_key = j.__dp_source_key\n"
                    "    LEFT JOIN selected_counts AS s ON r.__dp_branch = s.__dp_branch"
                    " AND r.__dp_source_key = s.__dp_source_key\n"
                    "    LEFT JOIN final_counts AS f ON r.__dp_branch = f.__dp_branch"
                    " AND r.__dp_source_key = f.__dp_source_key\n)")
        ctes.append("audit_summary AS (\n    SELECT __dp_branch AS branch,\n"
                    "      SUM(raw_n) AS source_rows, SUM(joined_n) AS joined_rows,\n"
                    "      SUM(selected_n) AS selected_rows, SUM(final_n) AS final_rows,\n"
                    "      SUM(CASE WHEN raw_n > 1 THEN 1 ELSE 0 END) AS duplicate_source_keys,\n"
                    "      SUM(CASE WHEN null_key = 1 THEN raw_n ELSE 0 END) AS null_source_keys,\n"
                    "      SUM(CASE WHEN joined_n = 0 THEN raw_n ELSE 0 END) AS inner_unmatched,\n"
                    "      SUM(CASE WHEN joined_n > 0 AND selected_n = 0 THEN raw_n ELSE 0 END) AS filtered,\n"
                    "      SUM(CASE WHEN selected_n > 0 AND final_n = 0 THEN raw_n ELSE 0 END) AS deduplicated,\n"
                    "      SUM(CASE WHEN joined_n > raw_n THEN joined_n - raw_n ELSE 0 END) AS join_fanout,\n"
                    "      SUM(CASE WHEN final_n > raw_n THEN final_n - raw_n ELSE 0 END) AS multiplied\n"
                    "    FROM key_audit GROUP BY __dp_branch\n)")
        return ",\n".join(ctes)

    def generate_audit(self, spec: Specification) -> str:
        return ("-- Source-key reconciliations. Nonzero loss/fanout is always visible here.\n"
                f"{self._summary_ctes(spec)}\nSELECT * FROM audit_summary ORDER BY branch;\n")

    def _join_check(self, join) -> str:
        keys = [self.expression(condition.right) for condition in join.on]
        # Same key expressions must be used for GROUP BY and null checks.
        group = ", ".join(keys)
        not_null = " AND ".join(key + " IS NOT NULL" for key in keys)
        return ("SELECT COUNT(*) AS failures FROM (\n"
                f"    SELECT {group} FROM {self.quote(join.table)} AS {self.quote(join.alias)}\n"
                f"    WHERE {not_null} GROUP BY {group} HAVING COUNT(*) > 1\n"
                ") AS duplicate_join_keys;")

    def _fixture_relation(self, attributes, rows) -> str:
        """Typed fixture rows for a source or the expected canonical result.

        No physical tables are modified. One generated transformation is evaluated
        against inline fixture relations inside the quality gate.
        """
        fields = [(attr.name, SPARK_TYPES[attr.type]) for attr in attributes]
        if not rows:
            return "SELECT " + ", ".join(
                f"CAST(NULL AS {kind}) AS {self.quote(name)}" for name, kind in fields
            ) + " WHERE 1 = 0"
        return "\nUNION ALL\n".join(
            "SELECT " + ", ".join(
                f"CAST({self.literal(row[name])} AS {kind}) AS {self.quote(name)}"
                for name, kind in fields
            ) for row in rows
        )

    def _expected_rows_check(self, spec: Specification, definition: dict) -> str:
        """Compare unordered multisets, including duplicate counts and NULLs.

        Reuse the real canonical STTM CTE builder with re-bound source objects.
        Fixtures are inlined as SQL CTEs, never inserted into actual source tables.
        """
        source_definitions = {src.id: src for src in spec.sources}
        fixtures = {sid: replace(source_definitions[sid], table=f"__dp_fixture_{sid}")
                    for sid in definition["inputs"]}
        branches = tuple(replace(branch, source=fixtures[branch.source.id],
            joins=tuple(replace(join, source=fixtures[join.source.id]) for join in branch.joins))
            for branch in spec.branches)
        fixture_spec = replace(spec, branches=branches)
        fixture_ctes = [
            f"{self.quote(fixtures[sid].table)} AS (\n"
            f"{self._fixture_relation(source_definitions[sid].column_contracts, rows)}\n)"
            for sid, rows in definition["inputs"].items()
        ]
        fixture_ctes.append(
            f"__dp_expected AS (\n{self._fixture_relation(spec.canonical.attributes, definition['expected'])}\n)"
        )
        ctes = self._ctes(fixture_spec).replace("WITH\n", "WITH\n" + ",\n".join(fixture_ctes) + ",\n", 1)
        fields = ", ".join(self.quote(attr.name) for attr in spec.canonical.attributes)
        ctes += (",\n__dp_actual_counts AS (SELECT " + fields + ", COUNT(*) AS __dp_n FROM final_rows GROUP BY " + fields + ")"
                 ",\n__dp_expected_counts AS (SELECT " + fields + ", COUNT(*) AS __dp_n FROM __dp_expected GROUP BY " + fields + ")")
        return (ctes + "\nSELECT (SELECT COUNT(*) FROM (SELECT * FROM __dp_actual_counts "
                "EXCEPT SELECT * FROM __dp_expected_counts) AS unexpected_rows) + "
                "(SELECT COUNT(*) FROM (SELECT * FROM __dp_expected_counts "
                "EXCEPT SELECT * FROM __dp_actual_counts) AS missing_rows) AS failures;")

    def _business_check(self, spec: Specification, test, target_name: str | None = None) -> str:
        d = test.definition
        target = self.quote(target_name or self.staging_target(spec))
        column = self.quote("t." + d.get("column", "")) if "column" in d else None
        if test.type == "accepted_values":
            values = ", ".join(self.literal(value) for value in d["values"])
            return (f"SELECT COALESCE(SUM(CASE WHEN {column} IN ({values}) THEN 0 ELSE 1 END), 0) "
                    f"AS failures FROM {target} AS {self.quote('t')};")
        if test.type == "relationships":
            source = next(source for source in spec.sources if source.id == d["source"])
            field = self.quote("r." + d["field"])
            return ("SELECT COALESCE(SUM(CASE WHEN EXISTS (SELECT 1 FROM "
                    f"{self.quote(source.table)} AS {self.quote('r')} WHERE "
                    f"{field} = {column}) THEN 0 ELSE 1 END), 0) AS failures "
                    f"FROM {target} AS {self.quote('t')};")
        if test.type == "expression":
            predicate = self.predicate(d["predicate"])
            return (f"SELECT COALESCE(SUM(CASE WHEN {predicate} THEN 0 ELSE 1 END), 0) "
                    f"AS failures FROM {target} AS {self.quote('t')};")
        if test.type == "expected_rows":
            return self._expected_rows_check(spec, d)
        raise ValueError(f"Unexpected business test type: {test.type}")

    def check_statements(self, spec: Specification, target_name: str | None = None) -> tuple[tuple[str, str], ...]:
        """Single authoritative registry of generated SQL checks and their stable IDs.

        Traceability, runtime results and SQL output all use these same IDs.
        """
        target = self.quote(target_name or self.staging_target(spec))
        checks: list[tuple[str, str]] = []
        for attr in spec.canonical.attributes:
            if not attr.nullable:
                checks.append((f"not_null:{attr.name}",
                               f"SELECT COUNT(*) AS failures FROM {target} "
                               f"WHERE {self.quote(attr.name)} IS NULL;"))
        key = ", ".join(self.quote(k) for k in spec.canonical.key)
        checks.append(("canonical_key_unique",
                       f"SELECT COUNT(*) AS failures FROM (SELECT {key} FROM {target} "
                       f"GROUP BY {key} HAVING COUNT(*) > 1) AS duplicate_keys;"))
        for i, branch in enumerate(spec.branches):
            for join in branch.joins:
                if join.expect_unique:
                    checks.append((f"join_unique:{i}:{join.source.id}", self._join_check(join)))
        if spec.preservation.mode == "snapshot":
            for i, branch in enumerate(spec.branches):
                tables = [(branch.source.table, branch.source.alias)]
                tables += [(join.table, join.alias) for join in branch.joins]
                for table, alias in tables:
                    archive = self.snapshot_target(spec, i, alias)
                    checks.append((f"snapshot:{i}:{alias}",
                                   f"SELECT ABS((SELECT COUNT(*) FROM {self.quote(table)}) - "
                                   f"(SELECT COUNT(*) FROM {self.quote(archive)})) AS failures;"))
        disallowed = [name for name in ("inner_unmatched", "filtered", "deduplicated", "multiplied")
                      if name not in spec.reconciliation.allow]
        disallowed += ["join_fanout"] if "multiplied" not in spec.reconciliation.allow else []
        failures = ["duplicate_source_keys", "null_source_keys"] + disallowed
        formula = " + ".join(f"COALESCE(SUM({name}), 0)" for name in failures)
        checks.append(("reconciliation", f"{self._summary_ctes(spec)}\n"
                       f"SELECT {formula} AS failures FROM audit_summary;"))
        if spec.deduplicate:
            by = ", ".join(self.quote(name) for name in spec.deduplicate.by)
            checks.append(("deduplication_ties",
                           f"{self._ctes(spec)}\nSELECT COUNT(*) AS failures FROM (\n"
                           f"    SELECT {by} FROM ranked WHERE __dp_tie_rank = 1\n"
                           f"    GROUP BY {by} HAVING COUNT(*) > 1\n) AS ambiguous_winners;"))
        # One registry for generated checks and declarative business checks.
        # ExecutionService, CLI checks, traceability and verification read this list.
        checks.extend((test.id, self._business_check(spec, test, target_name)) for test in spec.tests)
        result = tuple(checks)
        if len({identifier for identifier, _ in result}) != len(result):
            raise ValueError("Duplicate generated quality-check identifiers")
        return result

    def generate_checks(self, spec: Specification) -> str:
        """Every check has a stable ID; every SELECT returns zero on success."""
        sections = ["-- Each check must return exactly one row with failures = 0.",
                    "-- Execute against materialized staging BEFORE publication."]
        for check_id, statement in self.check_statements(spec):
            sections.append(f"-- check_id: {check_id}\n{statement}")
        return "\n\n".join(sections) + "\n"

    def generate_preservation(self, spec: Specification) -> str:
        if spec.preservation.mode == "external":
            return ("-- Raw retention is external, NOT verified by this compiler.\n"
                    f"-- Required retention reference: {spec.preservation.reference.replace(chr(10), ' ').replace(chr(13), ' ')}\n")
        sections = ["-- Run ONCE before publishing transformations; archives include ALL source columns.",
                    "-- CREATE TABLE fails if the archive already exists: choose a new product/snapshot name.",
                    "-- This is a point-in-time copy, NOT ongoing Bronze ingestion or CDC."]
        for i, branch in enumerate(spec.branches):
            tables = [(branch.source.table, branch.source.alias)]
            tables += [(join.table, join.alias) for join in branch.joins]
            for table, alias in tables:
                archive = self.snapshot_target(spec, i, alias)
                sections.append(f"CREATE TABLE {self.quote(archive)} AS SELECT * FROM {self.quote(table)};")
        return "\n".join(sections) + "\n"
