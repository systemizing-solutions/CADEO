# Changelog

## 0.8.4 - Opt-in standalone SQL test packs (2026-11-08)

- Add `cade compile ... --test-pack sql` and the editor's **Compile test-pack export** selector for file and project compilation. Export one independent SQL query per authoritative check under `<output-root>/<product>/tests/<product.model>/`, with a versioned manifest, prerequisites and a compiler-independent SQLAlchemy runner. Default `--test-pack none` / **None** emits no extra test-pack files and does not delete existing exports. SQL Preview identifies exported queries as standalone tests and reports their model and dialect.
- Reuse the same SQL templates, check IDs, SQLGlot translation and SQLFluff validation as native quality gates. Live-data checks target published tables; `expected_rows` unit tests inline fixtures and run without staging, source setup or publication. Run all checks or select unit/data tests, returning a nonzero exit code for failures, missing prerequisites and malformed results.
- Keep exporter selection separate from SQL dialect selection and the native execution DAG, reserving future exporters without emitting dbt files or a parallel compiler. Align the application, built-in specification, pinned schemas, examples and editor defaults to v0.8.4; the specification structure is unchanged.
- Make tutorial setup safely repeatable and resumable in existing directories. Preserve edited files and source data, create and seed only missing resources, and check complete setup before enabling tutorial actions instead of treating a SQLite file alone as readiness.

## 0.8.3 - Product-owned generated deliverables (2026-11-08)

- Generate one named folder per data product under the configured output root, keeping all of its models together. Explicit `--out` uses that common root even when source YAML files are in different subfolders.
- Organize SQL into `sql/transformations`, `sql/quality`, `sql/preservation`, and `sql/publication`; separate `metadata`, `plans`, `lineage`, and `documentation` deliverables. Keep model-qualified filenames and update emitted plan and traceability references to their actual paths.
- Isolate native build artifacts in `<product>/runs/<run-id>/`, including a product-specific `evidence/acceptance-results.md` linked to the authoritative shared run manifest. Preserve shared project plans, run manifests, transaction safety, and the existing SQL generation/execution pipeline.
- Update SQLite demos, add compilation, cross-product, editor-preview and native-build regression coverage, and document the output-layout change. Existing flat generated files are not automatically moved or deleted.
- Align the built-in YAML specification and JSON Schema version with app v0.8.3. Update bundled templates, editor defaults, version-pinned schemas, examples and tutorials together; the specification structure is unchanged.
- Create missing editor workspace directories on `cade ui` startup, preserving existing files by default. Add `--overwrite` to explicitly restore the workspace's bundled schema without replacing product YAML, databases or generated outputs.

## 0.8.2 — Explicit proof states and local-first verification (2026-11-08)

- Explain what specification validation, SQL compilation, and a passing data run prove, and what each does not prove.
- Make the local SQLite tutorial the first-run path through all three proof states; a local data run is highly recommended, not a release gate, and missing evidence advises completing a data run in development before release.

## 0.8.1 — Local connection-support tutorial and onboarding (2026-11-08)

- Add a local connection-support tutorial for first-time users, with `cade tutorial init`, `guide`, `report`, and `exercise` workflows and a six-step editor walkthrough.
- Generate isolated SQLite sample data and starter/full-product contracts; guide users through source design, owner review, build and acceptance evidence, a support queue, and wrong-mapping exercises that verify failed builds preserve the published output.
- Reuse CADE's existing specification, build, and execution services for the tutorial, and document local setup, database-driver options, and expected business outcomes.

## 0.8.0 — Multiple canonical models and published tables in one product (2026-11-06)

**Breaking specification release.** A product now has keyed canonical models, per-model STTM mappings, per-model assurance rules, and per-model publication targets under one authoritative six-stage YAML specification. Source `model_ref` resolves another model published by the same product; cross-product `product_ref` names a product and model. All dependencies are compiled into the existing project DAG and passed through the existing SQLGlot, SQLFluff and SQLAlchemy services. Acceptance checks are qualified as `model_id/check_id`. The Flask editor has a model selector and synchronized Add/Remove controls; ERD, lineage, build plan, SQL preview, and evidence cover multiple models. The two-table Merchant+Device example and regression tests demonstrate this contract. Older single-canonical YAML is rejected; no compatibility parser or execution path was added.


## 0.7.6 — Designed canonical ERD and three distinct engineering diagrams (2026-11-06)

- Add a canonical **entity-relationship diagram** (ERD) as the default view under Model → **View canonical ERD** and the central **Design diagrams** tab. This is distinct from source-to-target lineage and the build dependency DAG.
- Render canonical entity cards with business entity names, full attribute lists, types, logical key indicators, nullability, product target and record grain. Click or keyboard-select a field to inspect requirement links and declared conceptual relationships. Zoom, fit and scroll stay local to the UI.
- Render only explicitly declared `model.relationships`, with source attribute, related entity, optional `related_attribute`, and cardinality. Add schema-driven guidance for `related_attribute`. Never infer a business relation from an STTM join or a `product_ref`.
- Support a current-draft single-product ERD (including clearly identified external entities) and a saved-project ERD resolving declared relations across specifications. Ambiguous names, unknown target attributes and unresolved external entities are identified rather than silently linked to a guessed model.
- Reuse the same validated canonical IR and `/api/graph` endpoint. Extend `CanonicalModel` with immutable typed relationships. Do not add a second model, YAML format, SQL generator or execution path.
- Add a connected Device → Merchant cross-product example; update the bundled JSON Schemas, YAML examples, templates, versioned package resources, documentation and regression tests. Add Chromium field-selection coverage.

## 0.7.5 — Clear canonical modelling vocabulary (2026-11-06)

- Rename public model fields to `entity_name`, `record_grain`, and `unique_key_attributes` to make the meaning explicit. Rename physical source `key` to `record_key_columns`, distinguishing source identity from canonical output uniqueness. No legacy synonyms are accepted.
- Update the single JSON Schema with explanatory titles, descriptions, examples, key/attribute constraints and physical versus canonical key guidance; show titles and examples in the schema-driven editor.
- Use canonical-attribute selectors for `unique_key_attributes` and source-column selectors for `record_key_columns`, populated from the same YAML draft. Keep the single public-to-IR normalizer.
- Refresh every bundled YAML example, the starter template, the core JSON Schema, the version-pinned example schema and all regression fixtures. Version the custom SQL-functions schema as 2.0.0 because the model keys changed.
- Add regression tests for model guidance, invalid references, version pinning and UI binding, while retaining SQLGlot generation and SQLAlchemy execution unchanged.


## 0.7.4 — Authoritative check selectors (2026-11-06)

- Remove the browser-side check-ID reconstruction (which incorrectly offered nullable-field `not_null` checks and omitted join, snapshot and deduplication checks).
- Add a workspace-scoped, read-only `POST /api/check-registry` endpoint that inspects the **current unsaved specification** and returns the exact IDs and types from `SqlTemplateService.check_statements()`. This is the same ordered registry used by compilation, traceability, CLI `checks`, and SQLAlchemy execution.
- For candidate selection only, ignore existing acceptance-criterion `check_ids` references while resolving the registry, so an obsolete link does not prevent the user from choosing its replacement. All other structural and semantic rules still apply. Normal validation, compilation and build **continue to reject unknown references**. The authored YAML is not modified by this lookup.
- Debounce registry refreshes following form edits, reject stale HTTP responses, disable unverified choices and preserve obsolete user-provided IDs as clearly marked invalid (non-selectable) values rather than deleting them.
- Add regression coverage against the starter, full SQLite and joined-device examples, dynamic join-uniqueness changes, schema/path security, invalid drafts and a real headless-browser form smoke test with mocked backend responses.
- Keep the same six-stage YAML, JSON Schema, SQLGlot generation and SQLAlchemy execution paths. No new test format, SQL renderer or compatibility branch.

## 0.7.3 — Business acceptance and data-quality evidence (2026-11-05)

- Add a focused **Acceptance evidence** tab to the six-stage Flask editor, reached directly or from Prove/Deliver. Present requirement → feature → acceptance criterion → check as a complete traceability matrix, preserving the checks' actual IDs and execution status.
- Derive all links and check inventories from the existing validated IR and `TraceabilityService`. Read pass/fail/not-run, failure counts, reconciliation audit rows, transaction failures and publication outcomes from the native SQLAlchemy build run manifest. No results are stored in YAML and no second test engine is introduced.
- Record a SHA-256 fingerprint of each validated product IR in new run manifests. Historical/mismatched/missing evidence cannot be misrepresented as current results; such checks are shown as `not_run` with a stale-evidence warning. A claimed successful run lacking check/audit evidence is not considered proven publication.
- Display reconciliation exceptions by branch and metric, distinguishing explicitly permitted exclusions from blockers. Show failures from quality gates, execution steps and incomplete publication. Keep the latest workspace build discoverable and allow opening another workspace-relative run manifest.
- Expose the read-only, workspace-scoped `POST /api/evidence` endpoint, plus `dpcompile evidence <spec> [--manifest <path>] [--json]` from the same service. Generated `acceptance-results.md` now uses this evidence projection.
- Add unit and regression tests for both ends of the traceability chain, fail-fast check results, not-run inventory, stale manifests, reconciliation exceptions, publication blockers, CLI projection and Flask endpoint guards. Validate the new UI with a headless-browser smoke test.

## 0.7.2 — YAML diagnostics and SQL artifact preview (2026-11-05)

- Add a dedicated central **SQL Preview** tab, selecting any compiled artifact by product, artifact role and output dialect. Render read-only, line-numbered, highlighted SQL as the primary view. Copy the SQL or its actual workspace-relative generated path; retain a secondary expandable list of every generated file, including plans and documentation.
- Return SQL artifact metadata from the existing Flask compile action, deriving roles from emitted files and dialects from the same validated product specifications. Do not create a second SQL generator or hard-code SQL dialect names in the browser.
- Load only the selected SQL file from the existing workspace-scoped artifact endpoint. Mark the preview stale when the product changes, and surface compilation failures clearly rather than presenting old SQL as current.
- Add schema-context YAML completion for nested keys and permitted enum values, plus syntax highlighting and read-only, position-aware diagnostics for the unsaved YAML text. The diagnostic endpoint reuses `SpecificationService.findings` and the duplicate-key-safe YAML parser, rather than maintaining another validator.
- Preserve raw YAML, comments, selection and scroll across view switches, even when the YAML draft is not yet valid. Continue confirming any form-driven YAML regeneration that could alter formatting.
- Add tests for duplicate keys, YAML line/column findings, missing-field locations, shared validation, SQL artifact metadata and safe client syntax highlighting. Verify YAML ↔ SQL Preview ↔ YAML draft retention, schema suggestions and multiple artifacts in a headless browser smoke test.
- Keep the six-stage v0.7.0 contract, the v0.7.1 visual lineage graph, the one SQLGlot/SQLFluff generation path and the one SQLAlchemy execution broker. No compatibility formats or execution alternatives are introduced.

## 0.7.1 — Visual source lineage and execution dependencies (2026-11-05)

- Replace the static design table with an interactive, read-only source-to-target lineage graph in the existing three-region Flask editor. Display physical source datasets, ordered joins, filters, transformations, UNION ALL, deduplication and the canonical publication target.
- Let users select canonical attributes and individual union branches to highlight the exact path and inspect direct physical source columns, join/filter context, STTM expressions and requirement IDs. Branch-local alias resolution comes from the existing TraceabilityService (no global alias dictionary).
- Add a dependency-graph mode driven directly by the existing DependencyService and ProjectService plans, including cross-product references, quality gates and publication. Project scope reads saved YAML and is explicitly labelled; current-product graphs use the unsaved draft.
- Add a read-only `/api/graph` route that reuses the pinned JSON Schema, shared semantic validation, canonical IR and dependency/traceability services. Reject invalid drafts rather than drawing misleading lineage.
- Render with bundled, dependency-free SVG/JavaScript (keyboard-accessible graph nodes, field and branch selectors, zoom/fit and details). No external visualization service or second data product model.
- Add graph-specific regression tests for UNION alias reuse, joined-source identity, field-lineage equivalence, execution order, project edges and deterministic output. No changes to SQL generation or database execution.

## 0.7.0 — Six-stage specification and guided editor (2026-11-05)

- Establish one public, versioned YAML/JSON Schema contract: `definition`, `model`, `sources`, `mapping`, `assurance`, `delivery` in that exact order. No legacy format aliases or duplicate specification system.
- Reuse the existing validated compiler IR; internal normalization is the single boundary between the new public contract and existing SQLGlot/SQLFluff and SQLAlchemy services.
- Move physical and upstream-product source definitions into one `sources.datasets` keyed registry and connection information into `sources.connections`; keep data product output and build configuration in `delivery`.
- Align examples, bundled YAML template, custom SQL-function schema, CLI version and cross-product resolution with the new contract.
- Replace the generic all-fields editor with six-step navigation, a focused schema-defined form, contextual guidance and actions, design health, source-to-target mapping coverage and a canonical design table.
- Retain unsaved YAML, confirm destructive or reformatting operations, preserve expanded registry items, scroll and focus, and retain YAML comments when values are unchanged.
- Add project-versus-current-draft validation with actionable field paths and stale-result marking; source the same semantic validation service as the CLI.
- Preview SQL, lineage, checks and dependency plans before invoking the shared build workflow. Keep driver and schema installation in Files & Settings.
- Add v0.7.0 regression tests for the new contract, schema pinning, mapping gaps, cross-product dependencies, editor resources and YAML/state roundtrips.
- Deliberately reject pre-v0.7.0 public YAML; version 0.7.0 is breaking and does not maintain a secondary compatibility layer.

## 0.6.7 — DuckDB end-to-end dialect and execution support (2026-11-05)

- Add the optional `duckdb-sqlalchemy` package as `dpcompiler[duckdb]`, available through the existing UI driver installer.
- Register `duckdb` as the common SQLGlot output, SQLFluff validation and SQLAlchemy driver/backend name.
- Generate transactional DuckDB `CREATE OR REPLACE VIEW` publication SQL through the single SQLGlot pipeline.
- Require a live CREATE / REPLACE / ROLLBACK canary probe via SQLAlchemy before allowing a DuckDB build, protecting previously published views from an unsafe DDL implementation.
- Include a versioned DuckDB product example, a fail-gate publication-preservation demo, DuckDB integration tests and a dependency-enabled CI job.
- Do not enable unverified external database backends merely because their SQLAlchemy drivers load.


## 0.6.6 — Install optional SQLAlchemy drivers from the local editor (2026-11-05)

- Add **Install selected driver** beside the schema-independent SQLAlchemy driver catalogue in the Flask UI.
- Resolve every installation solely from the bundled 51-entry driver catalogue; reject arbitrary package names, flags, or URLs from the browser.
- Require explicit browser confirmation showing the target Python interpreter and virtual-environment status.
- Run `sys.executable -m pip install` for the catalogue's optional driver requirement, without a shell, prompting, database credentials, or output of potentially sensitive pip logs.
- Track one installation job at a time, poll for completion, and refresh the actual SQLAlchemy dialect/DBAPI load check. A successful pip exit is not treated as driver readiness until load verification succeeds.
- Keep driver installation distinct from database connection configuration and transactional publication certification. No additional SQL execution or compiler implementation is added.
- Add tests for unknown packages, missing confirmation, concurrent installation, pip failure, install-success-but-driver-not-loadable, and Flask endpoint security. Package manager access requires a working pip environment and package index.


## 0.6.5 — Optional SQLAlchemy dialect catalogue and runtime driver verification (2026-11-05)

- Include all documented SQLAlchemy 2.1 included database families and all 45 external dialect projects in one shipped catalogue.
- Add separately selectable optional package extras for every listed non-bundled driver; install none by default. Some external packages may be incompatible with SQLAlchemy 2.x or particular Python/system environments.
- Add `dpcompile engines [--installed] [--json] [--url-env NAME] [--connect]`, reporting dialect/DBAPI load results and optional live connectivity without exposing credentials.
- Expose the same catalogue in the local Flask editor for choosing optional installations; the YAML's configured execution URL remains authoritative.
- Separate output-dialect grammar support, runtime SQLAlchemy driver loading and safe transactional publication. Only SQLite and PostgreSQL currently have the compiler's certified native publication strategy; other installed drivers are eligible for discovery, not implicitly safe `build`.
- Keep one SQLGlot / SQLFluff SQL path and one SQLAlchemy database execution broker. Add regression tests for missing DBAPIs, package-vs-driver status, runtime URL probing, and the publication gate.

## 0.6.4 — Fail-fast quality checks (2026-11-05)

- Stream SQLAlchemy quality-check results to the execution service and record each result immediately; stop at the first nonzero failure count.
- Preserve the exact failed test ID, failure count, requirement/acceptance-criterion links and publication block reason in the run manifest.
- Explicitly mark every unexecuted check as `not_run` with null results, including checks in dependent products.
- Apply the same fail-fast behavior to `verify` and its CLI/Flask reports; skip audits following a failed test.
- Keep the existing SQLGlot/SQLFluff generation path, SQLAlchemy executor, transaction safety, JSON Schema contract, and CLI.
- Add regressions for a failing first query followed by invalid SQL, publication preservation, verification short circuit, and all-checks-pass execution.


## 0.6.3 — Explicit, pinned JSON Schema references (2026-11-05)

- Require `schema.path` and `schema.version` inside every data-product YAML file.
- Resolve JSON Schemas relative to each YAML file for all CLI operations, project builds, and the Flask UI; there is no hidden default or directive-based override at execution time.
- Require schema files to declare `$id` and `x-schema-version`; reject missing files, invalid local paths, or version mismatches before compiling.
- Version the bundled local contract as 0.6.3, copy it when `dpcompile init` runs, and install a versioned workspace copy when the local editor starts.
- Save schema selections inside the YAML, including YAML import and export. The UI's selector updates the explicit YAML reference and all build operations resolve it independently.
- Retain the shared JSON Schema → canonical IR → SQLGlot/SQLFluff → SQLAlchemy pipeline. No new compiler or database execution path.
- Add regression tests for missing references, stale version pins, invalid metadata, workspace escapes, and custom SQL-function schemas.

## 0.6.1 — Generic project identity (2026-11-05)

- Replace organization-specific names, email addresses, environment-variable names, and sample transformation prefixes with neutral examples.
- Rename the distribution to `dpcompiler`, retaining the existing `dpcompiler` Python imports and `dpcompile` CLI command.
- Update the bundled JSON Schema URI, version, starter specification and editor branding.
- Update examples, test fixtures and documentation to use consistent neutral names.
- Preserve the existing single SQLGlot SQL generation path and SQLAlchemy execution workflow; this release does not introduce new compatibility or execution paths.


## 0.6.0 — Local JSON Schema-driven editor (2026-11-05)

- Add `dpcompile ui [workspace] --port 8765`: a loopback-only Flask backend
  with bundled responsive form/YAML editor, generated from the existing JSON Schema.
- Import, export and save YAML via the same safe/duplicate-key-aware loader;
  preserve drafts independently of validation and guard against stale overwrites.
- Connect Validate, Trace, Checks, Plan, Compile, Discover, Build and Verify
  actions directly to existing Python services, not a CLI subprocess.
- Extract `BuildWorkflowService` for the CLI and UI to share one project build,
  execution, run manifest, acceptance report, and SQLAlchemy execution path.
- Keep project-level `compile` consistent between the CLI and editor, including
  `product_ref` resolution.
- Restrict the web editor to the selected workspace with loopback binding,
  CSRF token, safe file paths and an explicit build confirmation.
- Add schema-driven client-side form logic, packaged UI assets and Flask route tests.
- Update the single specification version to `0.6.0`; no legacy parser added.


## 0.5.3 — Single SQLAlchemy Execution Path (2026-11-04)

- Removed the separate `sqlite_broker.py` implementation and its direct
  `sqlite3.connect` / SQLite-only SQL splitter. No compatibility facade remains.
- Routed `dpcompile verify` through the existing SQLAlchemy `ExecutionBroker`
  and SQLGlot statement splitter. Quality checks for build and verify share
  strict result-cardinality validation.
- Migrated in-memory, SQLite and integration test execution and all runnable
  examples to SQLAlchemy transactions. Direct database access no longer occurs
  through the `sqlite3` module.
- Added a repository-wide regression guard against reintroducing direct
  SQLite DBAPI execution, plus SQLAlchemy verification and rollback tests.
- Kept the canonical IR, DAG, JSON Schema, transformation and testing contract
  unchanged except for the required specification version `0.5.3`.

## 0.5.2 — Declarative business and transformation tests (2026-11-04)

- Add a versioned `tests` registry to the authoritative JSON Schema and Python IR.
- Support `accepted_values`, `relationships`, `expression`, and `expected_rows` tests.
- Reuse the current structured expression/predicate language. No raw SQL test escape hatch or second SQL renderer.
- Generate every business assertion in the existing SQL quality-check asset, through the same SQLGlot and SQLFluff services as other generated checks.
- Execute all assertions in the existing `dpcompile build` quality gate before publication; fail immediately and preserve the prior published view on transaction-safe engines.
- Fixture-based `expected_rows` runs the real canonical STTM logic against inline typed source fixtures and compares **unordered row multisets**, including duplicate counts. It does not alter warehouse tables.
- Validate test IDs, required columns, types, source references, expression aliases and exact fixture input/output shapes; reject dangling acceptance-test references.
- Include declarative test IDs/types in generated lineage and documentation, and link runtime test results to requirement IDs and acceptance criterion IDs in the build manifest.
- Add a joined device/merchant example, a full native-build demonstration for dependency-enabled CI, and regression tests for invalid data, fixture mismatches and publication rollback.

No alternative SQL generation paths or compatibility scaffolding were introduced. No new CLI command is needed.

**Verification:** The local unit and SQLite transaction tests run without SQLGlot or SQLFluff; dependency-enabled SQL translation and full-build examples require these packages and are configured in CI.

## 0.5.1 — Native build and execution (2026-11-04)

- Introduced SQLAlchemy execution of the generated DAG with staging, reconciliation, quality gates, publication and a run manifest.
- Added run-specific staging/snapshots and transaction-based publication safeguards for SQLite.
- Added build-time execution configuration through the existing connection registry.

## 0.5.0 — Compile-time orchestration and publication planning (2026-11-04)

- Kept specification validation and SQL artifact generation separate from database execution, providing a compile-time foundation for the native build introduced in v0.5.1.
- Organised transformation, quality, reconciliation and preservation SQL as generated artifacts with explicit dependencies.
- Prepared execution and publication ordering from the dependency graph rather than embedding a separate orchestration path in each SQL renderer.

## 0.4.4 — Correct physical-source lineage across UNION branches (2026-11-03)

- Addressed the traceability defect caused by treating source aliases as globally unique across UNION branches.
- Resolve aliases within their own branch when reporting physical source tables and columns, so a reused alias cannot redirect lineage to another branch's source.
- Use multi-branch examples to check that lineage corresponds to the source actually used by each mapping rule.

## 0.4.3 — Multi-source mapping traceability (2026-11-03)

- Expanded the traceability view of source-to-target mappings across source aliases, joins and UNION branches.
- Linked canonical attributes and mapping rules to their physical source context, establishing the lineage model subsequently corrected in v0.4.4.
- Extended validation and traceability diagnostics for composite data-product transformations.

## 0.3.2 — One SQLGlot translation service (2026-11-03)

- Consolidated SQL generation and dialect translation through one shared SQLGlot service.
- Applied the same translation path to canonical transformation SQL, data-quality checks, reconciliation and source-preservation SQL.
- Removed separate Spark- and SQLite-specific rendering paths rather than retaining duplicate implementations for backward compatibility.
- Prioritised deterministic output and a single implementation over compatibility scaffolding during the pre-release phase.

## 0.3.0 — Declarative compilation foundation (2026-11-03)

- Established a structured, declarative product specification as input to the compiler.
- Separated canonical modelling and source-to-target transformation intent from generated SQL artifacts.
- Added validation of the specification before compilation, rather than relying only on SQL execution failures.

## 0.2.0 — Canonical modelling and mapping foundations (2026-11-02)

- Introduced core concepts for canonical attributes, source datasets and mapping rules in the product specification.
- Defined how a data product's target attributes should be derived from source fields and transformation expressions.
- Continued developing the specification-first approach rather than hardcoding product-specific transformations.

## 0.1.0 — Initial specification-driven compiler prototype (2026-11-02)

- Began the compiler as a repeatable way to define a data product in a specification and generate implementation artifacts from that definition.
- Established the initial separation between authored product intent and generated code.
- Provided the foundation for subsequent modelling, mapping, SQL translation, validation and build workflows.
