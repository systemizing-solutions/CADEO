import csv
import contextlib
import json
import sqlite3
from io import StringIO

import pytest
import yaml
from jsonschema import Draft202012Validator

from cadeo.brokers.file_broker import FileBroker
from cadeo.models.specification import SpecificationValidationError
from cadeo.services.bootstrap_service import BootstrapService, SourceField, SourceMetadata
from cadeo.exposers.cli import main
from cadeo.services.product_service import ProductService
from cadeo.services.project_service import Project, ProjectService
from cadeo.services.specification_service import SpecificationService


def test_csv_bootstrap_is_explicitly_provisional_and_refresh_preserves_authored_intent(tmp_path):
    source_path = tmp_path / "devices.csv"
    with source_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("device_id", "active"))
        writer.writerow(("d-1", "true"))
        writer.writerow(("d-2", "false"))

    source = BootstrapService.from_csv(source_path)
    draft = BootstrapService.draft(source)
    assert draft["bootstrap"]["status"] == "review_required"
    assert {field["type"] for field in draft["model"]["entities"]["devices"]["attributes"].values()} == {
        "string", "boolean"}
    assert "record_grain" in draft["bootstrap"]["review_required"]

    draft["model"]["entities"]["devices"]["record_grain"] = "One row per approved device"
    draft["mapping"]["entities"]["devices"]["fields"]["active"] = {"literal": True}
    refreshed_source = SourceMetadata("devices", (
        SourceField("device_id", "string", False),
        SourceField("active", "boolean", True),
        SourceField("retired", "boolean", True),
    ), source_kind="csv")
    proposal, diff = BootstrapService.refresh(draft, refreshed_source)

    assert proposal["model"]["entities"]["devices"]["record_grain"] == "One row per approved device"
    assert proposal["mapping"]["entities"]["devices"]["fields"]["active"] == {"literal": True}
    assert "retired" in proposal["model"]["entities"]["devices"]["attributes"]
    assert "retired" in proposal["sources"]["datasets"]["devices"]["columns"]
    assert "refreshed proposal" in diff
    assert "retired" in diff
    assert "retired" not in yaml.safe_dump(draft)


def test_ddl_bootstrap_reads_columns_and_primary_key(tmp_path):
    ddl = tmp_path / "device.sql"
    ddl.write_text("CREATE TABLE device (device_id INTEGER PRIMARY KEY, label VARCHAR(40) NOT NULL)",
                   encoding="utf-8")

    source = BootstrapService.from_ddl(ddl)

    assert source.name == "device"
    assert source.key_columns == ("device_id",)
    assert [(field.name, field.type, field.nullable) for field in source.fields] == [
        ("device_id", "integer", False), ("label", "string", False)]


def test_live_table_bootstrap_creates_valid_blocked_draft(tmp_path):
    database = tmp_path / "source.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE device (device_id INTEGER PRIMARY KEY, label TEXT NOT NULL)")
    source = BootstrapService().from_table(
        f"sqlite:///{database}", "device", "source", "CADEO_SOURCE_URL")
    draft = BootstrapService.draft(source)

    schema_path = tmp_path / "product.schema-v0.8.8.json"
    schema_path.write_text(FileBroker().read_bundled_text("schemas/product.schema.json"), encoding="utf-8")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(draft)
    spec_path = tmp_path / "product.yaml"
    spec_path.write_text(yaml.safe_dump(draft, sort_keys=False), encoding="utf-8")

    products = ProductService(FileBroker(), SpecificationService(FileBroker()))
    bundle = products.validate(spec_path)
    with pytest.raises(SpecificationValidationError, match="review-required"):
        products.compile(spec_path)
    project = Project(tmp_path, tuple((model.product.name, spec_path, model)
                                      for model in bundle.models))
    projects = ProjectService(FileBroker(), SpecificationService(FileBroker()), products)
    with pytest.raises(SpecificationValidationError, match="review-required"):
        projects.build(project)


def test_cli_bootstrap_and_refresh_write_separate_review_artifacts(tmp_path, monkeypatch):
    database = tmp_path / "source.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE device (device_id INTEGER PRIMARY KEY, label TEXT)")
    monkeypatch.setenv("CADEO_SOURCE_URL", f"sqlite:///{database}")
    draft_path = tmp_path / "device.yaml"
    with contextlib.redirect_stdout(StringIO()):
        assert main(["bootstrap", "--table", "device", "--url-env", "CADEO_SOURCE_URL",
                     "--out", str(draft_path)]) == 0
    original = draft_path.read_text(encoding="utf-8")
    assert (tmp_path / "device.schema-v0.8.8.json").is_file()

    csv_path = tmp_path / "device.csv"
    csv_path.write_text("device_id,label\n1,one\n", encoding="utf-8")
    with contextlib.redirect_stdout(StringIO()):
        assert main(["bootstrap", str(csv_path), "--refresh", str(draft_path)]) == 0
    assert draft_path.read_text(encoding="utf-8") == original
    assert (tmp_path / "device.refreshed.yaml").is_file()
    assert (tmp_path / "device.refreshed.diff").is_file()


def test_web_ui_bootstrap_action_creates_and_refreshes_review_drafts(tmp_path):
    from cadeo.exposers.web import create_app

    source_path = tmp_path / "devices.csv"
    source_path.write_text("device_id,label\n1,one\n", encoding="utf-8")
    client = create_app(tmp_path).test_client()
    token = client.get("/api/bootstrap").get_json()["token"]
    headers = {"X-Editor-Token": token}
    page = client.get("/").get_data(as_text=True)
    assert 'id="bootstrapOptions"' in page
    assert "Bootstrap source options" in page
    assert "Bootstrap draft" in client.get("/assets/editor.js").get_data(as_text=True)

    response = client.post("/api/action", json={
        "action": "bootstrap", "source": "devices.csv", "out": "devices.yaml",
    }, headers=headers)
    assert response.status_code == 200, response.get_json()
    created = response.get_json()
    assert created["path"] == "devices.yaml"
    assert created["status"] == "review_required"
    original = (tmp_path / "devices.yaml").read_text(encoding="utf-8")

    source_path.write_text("device_id,label,active\n1,one,true\n", encoding="utf-8")
    refreshed = client.post("/api/action", json={
        "action": "bootstrap", "source": "devices.csv", "refresh": "devices.yaml",
    }, headers=headers)
    assert refreshed.status_code == 200, refreshed.get_json()
    proposal = refreshed.get_json()
    assert proposal["path"] == "devices.refreshed.yaml"
    assert proposal["diff"] == "devices.refreshed.diff"
    assert (tmp_path / "devices.yaml").read_text(encoding="utf-8") == original
    assert "active" in (tmp_path / proposal["path"]).read_text(encoding="utf-8")
    diff = client.get("/api/artifact", query_string={"path": proposal["diff"]})
    assert diff.status_code == 200
    assert "refreshed proposal" in diff.get_json()["content"]
