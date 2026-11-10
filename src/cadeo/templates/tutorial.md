# Tutorial: Find payment connections that need support

Allow 15 minutes. Use local sample data only. You need Python 3.10 or later.
SQLite needs no extra driver. Do not use a production database for this exercise.

In the editor, **Save** persists unfinished drafts at any stage. A new draft
shows **Save specification as**; enter a workspace-relative YAML path or use
the filename suggested from the product name. You can save Define before
adding sources or mappings, reopen the draft, and move between stages freely.
**Validate**, SQL generation, and builds still require a complete contract;
pending generated checks do not prevent saving.

## 1. Start with one source and one output

Install CADEO from the repository: `python -m pip install -e .`.
For an installed release, use `python -m pip install cadeo`.
Run `cadeo tutorial init`. This creates a `tutorial` folder or completes an
existing partial setup. Existing contracts, notes, source tables and data are
preserved; only missing resources are created. Running setup again is safe.
Run the printed `export` commands in your terminal.
They set separate source and destination SQLite connections for the CLI commands below.

| Role | Connection ID | Database file | URL environment variable |
| --- | --- | --- | --- |
| Read input tables | `source` | `tutorial/source.sqlite` | `CADEO_TUTORIAL_SOURCE_DATABASE_URL` |
| Build, check and publish outputs | `destination` | `tutorial/destination.sqlite` | `CADEO_TUTORIAL_DESTINATION_DATABASE_URL` |

Each physical dataset selects `connection: source`. Its table name includes
the connection ID, for example `source.input_devices`. Publication separately
selects `delivery.execution.connection: destination`:

```yaml
sources:
	connections:
		source: {url_env: CADEO_TUTORIAL_SOURCE_DATABASE_URL}
		destination: {url_env: CADEO_TUTORIAL_DESTINATION_DATABASE_URL}
	datasets:
		devices:
			connection: source
			table: source.input_devices
			# alias, columns, source record key and contract follow
delivery:
	execution: {connection: destination}
	targets:
		device: {table: tutorial_device}
	# dialect, refresh and recovery follow
```

The build opens the destination and attaches the source database read-only as
`source`. Generated SQL reads it directly; there is no manual import step.
Snapshots, staging tables and published views are created only in the destination.
Published views read destination staging tables, so consumers can query them
without attaching the source database. This direct cross-database path currently
supports local SQLite files only, not arbitrary remote database federation.

Run these commands in order to reach the three proof states:

```bash
cadeo validate tutorial/starter/product.yaml
cadeo compile tutorial/starter/product.yaml --out tutorial/build --dialect sqlite
cadeo build tutorial/starter/product.yaml --out tutorial/build
```

One source, `input_devices`, produces one output, `tutorial_device`.
The output has three device IDs: D1, D2, and D3. Its key is `device_id`.
Two devices belong to M1. A merchant ID is not a device ID.

UI: Run `cadeo ui .`. Open the printed local URL. Select **Tutorial**.
Select **Create local example** if you have not used the CLI setup.
Select **Open starter**. Use **Validate**, **Preview SQL**, then
**Build & publish** in order. Keep the validation scope set to
**Current specification**.

In **Connect**, the shared connection registry defines both database URLs.
Expand **Datasets > devices**: its **Connection** is `source`, not `destination`.
The **Destination database** selection is `destination`. **Model** shows that
same destination alongside the selected model's publication table. Changing
a dataset connection does not change the destination, or vice versa.

Project diagrams include the starter, full product and wrong-mapping exercises
as separate products with distinct IDs and publication targets. Build each
exercise individually: the wrong mappings are deliberately designed to fail.
In the project ERD, `Connection.connection_id` links one-to-one to
`Device.device_id` in the starter product. In current-product scope, Device is
shown as external. This conceptual relationship does not add a join or build
dependency; the full product still reads `input_devices` directly.
Setup preserves existing contracts. Tutorials created with the earlier single
`local` connection are not rewritten: initialize a new directory, for example
`cadeo tutorial init tutorial-separated`, to get the two-database example.

Read results as three distinct proof states:

| State | Proves | Does not prove |
| --- | --- | --- |
| **Specification valid** | The contract passes schema and semantic validation. | The rules match owner intent or source data. |
| **SQL compiled** | SQL was generated and checked for the selected dialect. | It ran against data or passed quality checks. |
| **Data run passed** | The selected data ran through the build and its required checks and publication passed. | Sample data represents production or the product is release-approved. |

The starter build above is the first full run, reading local SQLite sample
data from the source database and publishing to the destination database.
This fixture is tutorial-specific; CADEO builds are not
SQLite-only. For other products, CLI `cadeo build` and editor **Build & publish**
use the execution connection selected in `delivery.execution.connection`,
resolved through `sources.connections.<id>.url_env`.

A local CADEO data run using the configured connection is highly recommended,
not required. If skipped, run `cadeo evidence tutorial/starter/product.yaml`; the
evidence report says no data-run evidence was supplied and advises completing a
data run in development before release.

## 2. Check the meaning with the data owner

Ask Service Operations to review these rules before you change a mapping.
The sample owner address is not a real contact. Replace it for a real product.

| Question for the owner | Sample decision | Evidence to keep |
| --- | --- | --- |
| What does one connection row mean? | One current device-to-merchant-to-service connection per device ID. | Approved grain and key. |
| Does LIVE mean a successful payment? | No. It means the device connection is enabled. OFF means inactive. | Status definitions. |
| What happens if a service record is absent? | Keep the device. Keep its service ID. Leave the service name null. | D3 must remain in the output. |
| Can a merchant have two devices? | Yes. M1 owns D1 and D2. | These are two separate connections. |
| What action will the consumer take? | Send active connections with no service record to Service Operations. | D3 is the sample action queue. |

Review all three expected rows in `assurance.entities.connection.tests.owner_examples`.
Record the owner's name, date, decisions, and approval in your change record.
The supplied tests represent sample decisions. A green test is not owner approval.
Do not change an expected row only to make a failed test pass.

## 3. Add sources and design the shared product

Open `tutorial/product/product.yaml`. The full contract has three physical
sources and three canonical outputs. Canonical means one agreed model for all
consumers, not a copy of each team's source format.

| Source and owner | Canonical output | Grain and use |
| --- | --- | --- |
| input_merchants: Merchant Operations | tutorial_merchant | One merchant ID. Shared merchant names. |
| input_services: Service Operations | tutorial_service | One service ID. Shared service names. |
| input_devices: Operations | tutorial_connection | One device ID. Link the merchant and service. |

The connection model reads the two canonical models through explicit
`model_ref` dependencies. CADEO builds them first. LEFT joins retain D3 when
service S9 is absent. Unique join-key checks prevent row multiplication.
The full model replaces the starter design; the starter table is not an input.
Use a separate source-system key if you later combine systems with overlapping IDs.

CLI: Run `cadeo plan tutorial/product/product.yaml`, then
`cadeo discover tutorial/product/product.yaml --profile`, then
`cadeo build tutorial/product/product.yaml`.
Discovery can skip canonical inputs before their first build. Run it again
after the build. Inspect the physical sources in both reports.

UI: Select **Open full product**. Review Define, Model, Connect, Map,
Prove, and Deliver. Select **Build & publish** in Deliver.
Open **Acceptance evidence**. Inspect `connection/owner_examples`, the key
checks, reconciliation, and the run manifest. Results must come from a real run.

The schedule is contract metadata. CADEO does not start a scheduler. These
sample rows do not prove a real source meets the 24-hour freshness promise.

## 4. Use the product

CLI: Run `cadeo tutorial report`. UI: Select **Show support queue**.
Both read the published output. Expect one item: D3, City Pharmacy, S9.

The consumer query is:

```sql
SELECT connection_id, merchant_name, service_id
FROM tutorial_connection
WHERE connection_status = 'active' AND service_name IS NULL
ORDER BY connection_id;
```

Support can find a missing service record without joining three source tables.
The data engineer has explicit dependencies, source snapshots, repeatable SQL,
quality gates, and run evidence. The owner can trace the output to agreed rules.
This is a support queue, not a revenue report. It does not prove payment success.

### Unit-test the transformation

The contract's `connection/owner_examples` test is a unit test for the model
transformation: its inline source rows are passed through the real mapping and
joins, then compared with all three expected output rows. In particular, D3
checks that a missing service stays visible with a null service name. This does
not read the tutorial databases or prove that live data is correct; the build
checks in steps 1 and 3 cover those separate concerns.

Export and run just the self-contained tests:

```bash
cadeo compile tutorial/product/product.yaml --out tutorial/unit-tests/correct --dialect sqlite --test-pack sql
DATABASE_URL=sqlite:// python tutorial/unit-tests/correct/connection_tutorial/tests/connection_tutorial.connection/run.py --kind unit
```

The runner uses an in-memory SQLite database and inline fixtures, not the
configured source or destination databases. It needs SQLAlchemy and a driver,
but does not need CADEO after export. Expect `owner_examples` to pass with zero
failures.

Now export the intentionally wrong payment-status mapping and run its fixture
test:

```bash
cadeo compile tutorial/wrong-status/product.yaml --out tutorial/unit-tests/wrong-status --dialect sqlite --test-pack sql
DATABASE_URL=sqlite:// python tutorial/unit-tests/wrong-status/payment_status/tests/payment_status.payment/run.py --kind unit
```

This test is expected to fail with exit code 1: the wrong mapping marks the
refunded payment as paid, so its output differs from the expected rows. The
other exported checks are classified as `data` and require live tables; `--kind
unit` runs only inline-fixture expected-row tests. Unit-test results do not
replace a build or its acceptance evidence.

## 5. Try wrong mappings

Build the full product first. Each exercise changes a separate contract, not
the approved contract, and publishes to its own targets. A failed build must
leave both the published queue and any existing exercise targets unchanged.

| Exercise | Source and output | Wrong mapping and business effect | Check that must fail |
| --- | --- | --- | --- |
| wrong-key | `input_shipments` -> `tutorial_shipment` | Using customer ID as shipment ID gives two shipments the same key. | `shipment/canonical_key_unique` |
| wrong-status | `input_payments` -> `tutorial_payment` | Treating refunded payments as paid reverses the agreed status rule. | `payment/owner_examples` |
| inner-join | `input_support_agents`, `input_support_tickets` -> `tutorial_support_agent`, `tutorial_support_ticket` | An INNER agent join drops ticket T2 when agent A9 is absent. | `support_ticket/reconciliation` |

CLI: Run `cadeo tutorial exercise wrong-status`. Repeat with `wrong-key` and
`inner-join`. The command returns success only when the expected failure occurs
and the published queue remains unchanged. Inspect the printed run manifest.
UI: Choose an exercise. Select **Run wrong mapping**. Inspect the named failed
checks and the unchanged support queue. A blocked build is the expected result.
For the normal build command, a blocked build returns exit code 1.

### Understand the shipment key failure

`canonical_key_unique` is a built-in check generated from the model's
`unique_key_attributes: [shipment_id]`. Its logic counts groups with duplicate
shipment IDs; zero duplicate groups is a pass. The reference
`shipment/canonical_key_unique` under `AC_001` links this check to the acceptance
criterion. It does not define the check's logic.

Open the shipment exercise. **Map** and **Prove** show **Generated checks** for
the selected model, including the full check ID and expandable SQL. The SQL is
generated from the current draft; it is not evidence of a successful run.

**Edit rule settings** opens the model, mapping, or assurance setting that owns
a generated check. Built-in checks are not arbitrary editable SQL: changing the
declared key, nullability, joins, or retention policy changes the generated checks.

**Manage custom checks** opens the selected model's check editor in **Prove**.
Enter a check ID and select **Add custom check**, then choose its type and fill
in the rule. Accepted values, relationships, expressions, and expected rows use
the same schema as YAML. Edit the ID to rename a check, or **Remove** it to delete
it. Renaming updates acceptance links; deletion removes those links after
confirmation. Save persists these changes to the contract.

Each check offers acceptance-criterion linking and unlinking, as well as
**Review results** for run evidence. Changing a draft does not execute a check;
run a build to obtain new evidence.

The source rows are `SH1, C1, North` and `SH2, C1, South`. The intentionally
wrong mapping `shipment_id: {column: s.customer_id}` produces shipment IDs
`C1, C1`, so the duplicate-key check fails. The repair is
`shipment_id: {column: s.shipment_id}`; keep the `customer_id` mapping unchanged.
Run a build from the editor to verify a repair. **Run wrong mapping** expects a
failure, so a repaired exercise will no longer meet that button's expected result.

Each exercise is a separate business example, not a one-line variation of the
device product. A fresh initialization recreates these deliberately wrong
contracts. Initialization keeps any existing YAML edits; it does not reset them.

To repair a real error, agree the rule with the owner, fix the mapping, rerun
the checks, and keep the new run evidence. Do not permit row loss by default.

## Other database connections

SQLite keeps this example local. Run `cadeo engines --installed` to inspect drivers.
For PostgreSQL, run `python -m pip install 'cadeo[postgresql]'`. For DuckDB,
run `python -m pip install 'cadeo[duckdb]'`. Other extras are listed by `cadeo engines`.
UI: Open **Workspace**, then **Database driver**. Inspect the driver capability
before you install it. Driver installation alone does not certify publication.

Create a separate contract and database for migration. Define named connections
under `sources.connections` and select each physical dataset's connection and
the destination execution connection explicitly. Set their URL environment
variables in the shell before you start the CLI or UI. Do not put credentials in
YAML. Run `cadeo engines --url-env YOUR_URL_VARIABLE --connect`, then discovery
and a test build. Verify publication support for the selected driver.
For separate local SQLite files, qualify each input table with its connection
ID, as in `source.input_devices`; the executor attaches that file read-only.
Other backends still require physical inputs on the execution connection:
ingest remote inputs there first and select that connection for those datasets.
Driver installation alone does not add cross-database execution support.

## Stop and clean up

Stop the editor with Ctrl+C in its terminal. The tutorial folder contains all
sample data, contracts, snapshots, SQL, and build evidence. Remove that folder
only when you no longer need the evidence. A rerun uses the same sample data;
it does not reset or replace your edits.