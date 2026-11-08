# Tutorial: Find payment connections that need support

Allow 15 minutes. Use local sample data only. You need Python 3.10 or later.
SQLite needs no extra driver. Do not use a production database for this exercise.

## 1. Start with one source and one output

Install CADE from the repository: `python -m pip install -e .`.
For an installed release, use `python -m pip install cade`.
Run `cade tutorial init`. This creates a new `tutorial` folder. It does not
replace an existing folder. Run the printed `export` command in your terminal.
It sets the SQLite connection for the normal CLI commands below.

Run `cade build tutorial/starter/product.yaml`.
One source, `input_devices`, produces one output, `tutorial_device`.
The output has three device IDs: D1, D2, and D3. Its key is `device_id`.
Two devices belong to M1. A merchant ID is not a device ID.

UI: Run `cade ui .`. Open the printed local URL. Select **Tutorial**.
Select **Create local example** if you have not used the CLI setup.
Select **Open starter**. Use **Build & publish** in the Deliver stage.
Keep the validation scope set to **Current specification**.

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
`model_ref` dependencies. CADE builds them first. LEFT joins retain D3 when
service S9 is absent. Unique join-key checks prevent row multiplication.
The full model replaces the starter design; the starter table is not an input.
Use a separate source-system key if you later combine systems with overlapping IDs.

CLI: Run `cade plan tutorial/product/product.yaml`, then
`cade discover tutorial/product/product.yaml --profile`, then
`cade build tutorial/product/product.yaml`.
Discovery can skip canonical inputs before their first build. Run it again
after the build. Inspect the physical sources in both reports.

UI: Select **Open full product**. Review Define, Model, Connect, Map,
Prove, and Deliver. Select **Build & publish** in Deliver.
Open **Acceptance evidence**. Inspect `connection/owner_examples`, the key
checks, reconciliation, and the run manifest. Results must come from a real run.

The schedule is contract metadata. CADE does not start a scheduler. These
sample rows do not prove a real source meets the 24-hour freshness promise.

## 4. Use the product

CLI: Run `cade tutorial report`. UI: Select **Show support queue**.
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

## 5. Try wrong mappings

Build the full product first. Each exercise changes a separate contract, not
the approved contract. A failed build must leave the published queue unchanged.

| Exercise | Wrong mapping | Effect | Check that must fail |
| --- | --- | --- | --- |
| wrong-key | Use merchant ID as connection ID. | D1 and D2 become the same key. Device-level work cannot be assigned correctly. | connection/canonical_key_unique and connection/owner_examples |
| wrong-status | Treat OFF as active instead of LIVE. | D2 appears active. D3 leaves the support queue. | connection/owner_examples. Accepted-value checks alone cannot detect this error. |
| inner-join | Use an INNER join for services. | D3 disappears because S9 is absent. Support cannot see the missing record. | connection/reconciliation and connection/owner_examples |

CLI: Run `cade tutorial exercise wrong-status`. Repeat with `wrong-key` and
`inner-join`. The command returns success only when the expected failure occurs
and the published queue remains unchanged. Inspect the printed run manifest.
UI: Choose an exercise. Select **Run wrong mapping**. Inspect the named failed
checks and the unchanged support queue. A blocked build is the expected result.
For the normal build command, a blocked build returns exit code 1.

To repair a real error, agree the rule with the owner, fix the mapping, rerun
the checks, and keep the new run evidence. Do not permit row loss by default.

## Other database connections

SQLite keeps this example local. Run `cade engines --installed` to inspect drivers.
For PostgreSQL, run `python -m pip install 'cade[postgresql]'`. For DuckDB,
run `python -m pip install 'cade[duckdb]'`. Other extras are listed by `cade engines`.
UI: Open **Workspace**, then **Database driver**. Inspect the driver capability
before you install it. Driver installation alone does not certify publication.

Create a separate contract and database for migration. Change
`sources.connections.local.url_env` to your database URL environment variable.
Set that variable in the shell before you start the CLI or UI. Do not put
credentials in YAML. Change `delivery.dialect` and load the source tables in
that database. Run `cade engines --url-env YOUR_URL_VARIABLE --connect`, then
discovery and a test build. Verify publication support for the selected driver.
This tutorial joins tables in one SQLite database. It does not copy data
between databases; ingest remote sources into the execution database first.

## Stop and clean up

Stop the editor with Ctrl+C in its terminal. The tutorial folder contains all
sample data, contracts, snapshots, SQL, and build evidence. Remove that folder
only when you no longer need the evidence. A rerun uses the same sample data;
it does not reset or replace your edits.