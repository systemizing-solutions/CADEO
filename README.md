# CADE
> Contract-Driven Automation for Data Engineering v0.8.3

A **data product contains one or more published canonical models**, not just one table. One versioned YAML specification defines business requirements, multiple canonical entities, source contracts, one STTM per entity, assurance, and one publication target per entity. The JSON Schema drives both validation and the local Flask UI.

**This is a breaking six-stage specification release.** A data product is the unit of ownership; a *model ID* is the unit of transformation, quality testing and publication. There is no second SQL engine, no second database execution engine, and no compatibility mode for the old single-entity YAML shape.

## Install

```bash
python -m pip install -e .
cade --version
# Versioned YAML and its local schema
cade init product.yaml
# Open local visual editor
cade ui examples/multi_model --port 8765
```

`cade ui test/ --port 8765` creates the workspace directory, including missing
parents, and installs the bundled schema if absent. Existing schema edits,
product YAML, databases, and generated outputs are preserved on startup.
To explicitly restore the bundled schema, use
`cade ui test/ --port 8765 --overwrite`. This flag replaces only the bundled
schema file; it does not reset the workspace or overwrite authored products.

Open `http://127.0.0.1:8765` and select `product.yaml` in the multi-model folder. The editor provides a model selector in **Model**, **Map**, **Prove** and **Deliver**, plus Canonical ERD, source-to-target lineage, dependency diagrams, SQL preview, and acceptance evidence. Select **Add model** to create synchronized entries across the canonical, mapping, assurance and target registries. The YAML view edits the same document. The application only listens on localhost.

## First run: local tutorial

Start with the local connection-support tutorial. It creates isolated SQLite
sample data and guides you from a valid specification through compiled SQL to
a passing data run. No external database or credentials are needed.

```bash
cade tutorial init
# Run the export command printed by init to select the local SQLite database.
cade validate tutorial/starter/product.yaml
cade compile tutorial/starter/product.yaml --out tutorial/build --dialect sqlite
cade build tutorial/starter/product.yaml --out tutorial/build
cade tutorial guide
cade ui .
```

These are three distinct proof states:

| State | Proves | Does not prove |
| --- | --- | --- |
| **Specification valid** (`cade validate`) | The contract passes schema and semantic validation. | The business rules match the owner's intent or any source data. |
| **SQL compiled** (`cade compile`) | CADE generated SQL for the selected dialect and validated its syntax. | The SQL has run, the data passes checks, or publication works. |
| **Data run passed** (tutorial's local SQLite `cade build`) | Generated SQL ran against the local sample data, configured checks passed, and the run's publication steps completed. | The sample represents production, business meaning is approved, or a production release is safe. |

In the editor, select **Tutorial**, then **Open starter**. Use **Validate**,
**Preview SQL**, and **Build & publish** to reach the same states in order. The
six tutorial steps continue with owner review, source contracts, mappings,
checks, publication, and the support queue. The CLI and UI use the same
contracts and execution services. The [full guide](src/cade/templates/tutorial.md)
includes all commands, wrong-mapping exercises and their business effects,
owner-review questions, expected results, and instructions for other database
connections. Use current-file scope. The starter, full product, and wrong
mappings are separate exercises, not one project to build together.

A local CADE data run using the connection configured in YAML (e.g. SQLite) is highly
recommended, not a release requirement. If you skip it,
`cade evidence tutorial/starter/product.yaml` reports that no data-run evidence
was supplied; ensure a data run is completed in development before releasing.
Validation and compilation do not connect to a database. SQLite is the
tutorial's bundled sample only: CLI `cade build` and editor **Build & publish**
use the execution connection selected in `delivery.execution.connection`,
resolved through `sources.connections.<id>.url_env`. Set that environment
variable before running CADE. The editor's **Verification database** path is
for its separate SQLite verification action, not a build-connection override.

## One product, multiple tables

The complete runnable example is in [`examples/multi_model/product.yaml`](examples/multi_model/product.yaml), with its pinned local [`product.schema-v0.8.3.json`](examples/multi_model/product.schema-v0.8.3.json). The excerpt below demonstrates the contract (other mandatory fields omitted):

```yaml
schema: {path: product.schema-v0.8.3.json, version: "0.8.3"}
spec_version: "0.8.3"

definition:
  name: merchant_operations
  # owner, outcome, features, acceptance criteria, consumers...

model:
  entities:
    merchants:
      entity_name: Merchant
      record_grain: One row per merchant
      unique_key_attributes: [merchant_id]
      attributes:
        merchant_id: {type: string, nullable: false}
        merchant_name: {type: string, nullable: true}
    devices:
      entity_name: Device
      record_grain: One row per device
      unique_key_attributes: [device_id]
      relationships:
        - {attribute: merchant_id, entity: Merchant,
           related_attribute: merchant_id, cardinality: many_to_one}
      attributes:
        device_id: {type: string, nullable: false}
        merchant_id: {type: string, nullable: false}
        merchant_name: {type: string, nullable: true}

sources:
  datasets:
    raw_merchants: # physical source with table, alias, columns, key and contract
    raw_devices:   # physical source with table, alias, columns, key and contract
    published_merchants:
      model_ref: merchants   # source schema + table inherited from same YAML
      alias: m

mapping:
  entities:
    merchants:
      source: raw_merchants
      fields:
        merchant_id: {column: m0.merchant_id}
        merchant_name: {column: m0.merchant_name}
    devices:
      source: raw_devices
      joins:
        - type: left
          source: published_merchants
          on:
            - left: {column: d.merchant_id}
              right: {column: m.merchant_id}
      fields:
        device_id: {column: d.device_id}
        merchant_id: {column: d.merchant_id}
        merchant_name: {column: m.merchant_name}

assurance:
  quality: {on_failure: block}
  entities:
    merchants:
      reconciliation: {allow: []}
      preservation: {mode: external, reference: raw_merchants}
    devices:
      reconciliation: {allow: []}
      preservation: {mode: external, reference: raw_devices}
      # optional model-specific declarative acceptance tests

delivery:
  targets:
    merchants: {table: canonical_merchants}
    devices: {table: canonical_devices}
  dialect: sqlite
  # execution, refresh and recovery settings...
```

**Registry IDs must agree:** `model.entities`, `mapping.entities`, `assurance.entities`, and `delivery.targets` use the same model IDs. A missing mapping or orphan target is invalid. `model_ref` inherits an upstream model's **published** schema and target; it does not repeat their definitions. For a different product defined in another YAML file, use `product_ref: upstream_product.model_id` and build/validate the project folder.

Business criteria refer to quality checks as **`model_id/check_id`** (e.g. `merchants/canonical_key_unique` or `devices/merchant_exists`). The compiler's `SqlTemplateService.check_statements()` is the only source of check IDs. The web form retrieves those IDs from the backend rather than constructing them in JavaScript.

## CLI

```bash
# Validate all models and check references
cade validate examples/multi_model/product.yaml
# See model-specific IDs
cade checks examples/multi_model/product.yaml
# One combined DAG, including intra-product dependencies
cade plan examples/multi_model/product.yaml
# Compile every model, checks, audit and publication assets
cade compile examples/multi_model/product.yaml --out ./build --dialect sqlite
# Native SQLAlchemy execution, with a configured connection in YAML
cade build examples/multi_model/product.yaml --out ./build
# Target an individual model (and all upstream models)
cade build examples/multi_model/ --target merchant_operations.devices
# Human-readable traceability and evidence
cade trace examples/multi_model/product.yaml
cade evidence examples/multi_model/product.yaml
```

For native builds, configure an SQLAlchemy connection URL in the environment referenced by `sources.connections` and `delivery.execution`. `cade compile` produces assets without requiring a database connection. `cade build` executes the model DAG using **SQLAlchemy**, after compilation through **SQLGlot** and dialect validation through **SQLFluff**. Source readiness, preservation, staging, reconciliation, quality checks and publication follow the plan. A failed check prevents subsequent publication, and the run manifest stores check results and step statuses. Transaction-safe publication remains restricted to certified engines; an installed DBAPI driver does not imply safe deployment. `cade engines` reports actual driver availability.

To verify a real two-model end-to-end build and deliberately failed rerun against disposable SQLite data:

```bash
python examples/run_multi_model_demo.py
```

All database operations, including SQLite, are executed through SQLAlchemy. The test creates **Merchant** and **Device** outputs and checks that a failing rerun does not replace previously published data.

## Generated deliverables

The output setting is a **root directory**, not a flat collection of files.
CADE v0.8.3 creates one folder named after each product's `definition.name`.
All models belonging to that product stay together, with model-qualified
filenames such as `merchant_operations.devices.checks.sql`.

```text
generated/
  merchant_operations/
    sql/
      transformations/  # Canonical staging SQL
      quality/          # Quality checks and reconciliation audit SQL
      preservation/     # Source snapshots or external-retention references
      publication/      # Published tables/views
    metadata/           # Validated model build-unit definitions (JSON)
    plans/              # Model dependency plans and combined product DAG
    lineage/            # Requirement and field traceability (JSON)
    documentation/      # Model documentation (Markdown)
    runs/
      <run-id>/         # Isolated build SQL and supporting deliverables
        sql/
        metadata/
        plans/
        lineage/
        documentation/
        evidence/       # Product acceptance results, linked to run manifest
```

`delivery.output` remains relative to its YAML file. Explicit `--out` overrides
it and puts each product directly under that common root, regardless of source
subdirectories. Without either setting, the output root is `build`.
Compilation does not create run or evidence folders. Emitted JSON plans contain
artifact paths relative to the plan file's directory; traceability asset paths
are relative to the product or run directory.

Cross-product coordination stays separate: project compilation writes
`project.plan.json` and `project.plan.mmd` in the project output root. Native
builds keep their shared project plans, authoritative `run-manifest.json` and
combined `acceptance-results.md` in `<project-output-root>/<run-id>/` (by default,
`<project>/build/<run-id>/`). Each product's run folder contains its own acceptance
report with a relative link to that manifest; `--report` can still select a
different manifest location.

The built-in YAML/JSON Schema version now matches app v0.8.3; its structure is
unchanged. Existing specifications must update `spec_version`, `schema.version`,
and `schema.path` to the corresponding v0.8.3 schema. Recompile
to produce the new layout and update consumers that hard-code flat paths. Use
the artifact paths returned by compilation or emitted plans where possible.
Existing flat output files and historical build runs are not moved or deleted.

## Architecture

```
Versioned YAML + JSON Schema
   ├── Definition (requirements/features/acceptance)
   ├── Model: entities[model_id] (grain/attributes/key/relations)
   ├── Sources: datasets[source_id] (physical, model_ref, product_ref)
   ├── Mapping: entities[model_id] (STTM/joins/filters/unions)
   ├── Assurance: entities[model_id] (quality/reconciliation/tests)
   └── Delivery: targets[model_id] (publication/dialect/execution)
             ↓
  Shared specification parser + semantic validator
             ↓
  DataProduct (models: canonical build units)
             ↓
  One dependency DAG / SQLGlot + SQLFluff / SQLAlchemy executor
             ↓
  Independently published related tables + run evidence
```

The canonical ERD displays all entities within one YAML, and can also resolve relationships across YAML files at project scope. The lineage graph shows model-specific source tables, joins, union branches, transformations and the links from upstream published model outputs. Neither diagram is an independent model or editable source of truth.

## Validation and tests

```bash
python -m pip install -e ".[duckdb]" pytest
python -m pytest -q
node --check src/cade/web/editor.js
```

The v0.8.3 suite covers matching registries, model references, source contracts, mapping completeness, cycle rejection, targeted builds, project references, qualified checks, ERD/lineage and the SQLAlchemy execution broker. SQLGlot, SQLFluff and Flask integration tests run when their dependencies are installed. The included GitHub Actions workflow installs them and runs the real two-table SQLite build and failure-preservation demonstration.

### Notes

- Version `0.8.3` is intentionally a **breaking** specification format; older YAML must be restructured into the four model-keyed registries. No dual parser is supplied.
- Each `schema.path` is **relative to the YAML** and its version must equal the referenced JSON Schema's `x-schema-version`. Keep them together when sharing a product.
- Runtime test results, credentials and publication history are never authored into YAML; they live in environment variables and run manifests.
- Defining two canonical entities in one product does not automatically create a relationship. Declare logical relationships in `model.entities[model_id].relationships`, and explicitly implement physical joins in the model's STTM.
