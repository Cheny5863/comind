import json

from fastapi.testclient import TestClient

import backend


def test_new_map_creates_missing_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "missing-maps"
    monkeypatch.setattr(backend, "WORKSPACE", str(workspace))

    client = TestClient(backend.app)
    r = client.post("/api/new", params={"name": "first"})

    assert r.status_code == 200
    assert r.json()["name"] == "first.smm.json"
    created = workspace / "first.smm.json"
    assert created.is_file()
    doc = json.loads(created.read_text(encoding="utf-8"))
    assert doc["mindMapData"]["root"]["data"]["text"] == "first"


def test_new_map_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "WORKSPACE", str(tmp_path))

    client = TestClient(backend.app)
    r = client.post("/api/new", params={"name": "../bad"})

    assert r.status_code == 400


def test_new_map_rejects_windows_invalid_filename_chars(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "WORKSPACE", str(tmp_path))

    client = TestClient(backend.app)
    r = client.post("/api/new", params={"name": "bad:name"})

    assert r.status_code == 400
    assert "invalid" in r.text.lower() or "不能包含" in r.text


def test_save_map_creates_missing_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "missing-maps"
    monkeypatch.setattr(backend, "WORKSPACE", str(workspace))

    client = TestClient(backend.app)
    doc = {
        "mindMapData": {
            "root": {"data": {"text": "Saved"}, "children": []},
            "theme": {"template": "avocado", "config": {}},
            "layout": "logicalStructure",
            "config": {},
            "view": None,
        },
        "mindMapConfig": {},
        "lang": "zh",
        "localConfig": None,
    }
    r = client.post("/api/save", params={"name": "saved"}, json=doc)

    assert r.status_code == 200
    assert (workspace / "saved.smm.json").is_file()


def test_delete_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "WORKSPACE", str(tmp_path))

    client = TestClient(backend.app)
    r = client.post("/api/delete", params={"name": "../bad.smm.json"})

    assert r.status_code == 400


def test_rename_rejects_windows_invalid_filename_chars(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "WORKSPACE", str(tmp_path))
    (tmp_path / "old.smm.json").write_text("{}", encoding="utf-8")

    client = TestClient(backend.app)
    r = client.post(
        "/api/rename",
        params={"old_name": "old.smm.json", "new_name": "bad:name"},
    )

    assert r.status_code == 400
    assert (tmp_path / "old.smm.json").is_file()
