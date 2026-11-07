"""The export is written a record and a BLOB at a time (consolidation step 4.2, REM-18).

Its members and manifest are those it always had: each table's records laid out as json.dumps lays
out a list of them, every member in the manifest with its true size and hash, and each BLOB once.
The HTTP export is sent from a file, which is removed afterwards, after a failure too."""

import hashlib
import io
import json
import tempfile
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient
from worklog import app as app_module
from worklog import recovery
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
SAME = b"\x00\x01the same bytes twice"


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """Where the export's temporary files go, so a test can see that none is left."""
    folder = tmp_path / "temporary"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "data", testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        item = c.post("/api/v1/work-items", json={"title": "A line separator, \"quotes\" and\na break"},
                      headers={"Idempotency-Key": str(uuid.uuid4())}).json()
        for name in ("first.bin", "second.bin"):
            response = c.post(f"/api/v1/attachments?ownerType=item&ownerId={item['id']}&name={name}", content=SAME,
                              headers={"Idempotency-Key": str(uuid.uuid4()), "Content-Type": "application/octet-stream"})
            assert response.status_code == 200, response.text
        yield c


def members(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names)), "a member is written twice"
        return {name: archive.read(name) for name in names}


def test_the_records_are_laid_out_as_ever_and_each_member_matches_the_manifest(client, scratch):
    files = members(recovery.export_database(client.app.state.db.path))
    manifest = json.loads(files.pop("manifest.json"))
    assert {entry["path"]: (entry["size"], entry["sha256"]) for entry in manifest["files"]} == {
        name: (len(data), hashlib.sha256(data).hexdigest()) for name, data in files.items()}
    for name, data in files.items():
        if name.startswith("records/"):
            text = data.decode("utf-8")
            assert text == json.dumps(json.loads(text), ensure_ascii=False, indent=2), name
    # The two attachments hold the same bytes, which the export holds once, for both.
    same = f"blobs/{hashlib.sha256(SAME).hexdigest()}.bin"
    assert [record["content"]["file"] for record in json.loads(files["records/attachments.json"])] == [same, same]
    assert files[same] == SAME
    titles = [record["title"] for record in json.loads(files["records/work_items.json"])]
    assert titles == ["A line separator, \"quotes\" and\na break"]
    assert list(scratch.iterdir()) == []


def test_the_http_export_is_sent_from_a_file_that_is_then_removed(client, scratch):
    response = client.get("/api/v1/export")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith('attachment; filename="worklog-export.zip"')
    assert "manifest.json" in members(response.content)
    assert list(scratch.iterdir()) == []


def test_a_failed_export_leaves_no_file_behind(client, scratch, monkeypatch):
    def half_written(db_path, target):
        with open(target, "wb") as stream:
            stream.write(b"PK")
        raise OSError("No space left on device")

    monkeypatch.setattr(app_module, "write_export", half_written)
    with TestClient(client.app, base_url=ORIGIN, raise_server_exceptions=False) as person:
        person.cookies = client.cookies
        assert person.get("/api/v1/export").status_code == 503
    assert list(scratch.iterdir()) == []
