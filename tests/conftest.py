"""共享 fixture：临时脑图目录 + 标准测试树 + TestClient。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat_service  # noqa: E402
import backend  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

MAP_KEY = "test.smm.json"


def node(uid, text, children=None, **extra):
    data = {
        "text": f"<p>{text}</p>",
        "uid": uid,
        "richText": True,
        "expand": True,
        "isActive": False,
    }
    data.update(extra)
    return {"data": data, "children": children or []}


def sample_doc():
    """root-1
    ├── a-1 节点A
    │   ├── a-1-1 节点A1
    │   └── a-1-2 节点A2
    └── b-1 节点B（带备注，用于字段保留校验）
    """
    root = node("root-1", "根节点", children=[
        node("a-1", "节点A", children=[
            node("a-1-1", "节点A1"),
            node("a-1-2", "节点A2"),
        ]),
        node("b-1", "节点B", note="<p>备注B</p>"),
    ])
    return {
        "mindMapData": {
            "root": root,
            "theme": {"template": "avocado", "config": {}},
            "layout": "mindMap",
            "config": {},
            "view": None,
        },
        "mindMapConfig": {},
        "lang": "zh",
        "localConfig": None,
    }


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """每个测试：独立 tmp 目录作为 PROJECT_CWD，写入样例脑图并同步到后端。"""
    monkeypatch.setattr(chat_service, "PROJECT_CWD", str(tmp_path))
    backend.chat_manager._map_state.clear()
    backend.chat_manager._map_snapshot.clear()
    doc = sample_doc()
    (tmp_path / MAP_KEY).write_text(json.dumps(doc, ensure_ascii=False, indent=2))
    client = TestClient(backend.app)
    r = client.post(f"/api/chat/{MAP_KEY}/sync", json=doc["mindMapData"])
    assert r.status_code == 200
    return client


def read_disk_root(tmp_path, client):
    """从 PROJECT_CWD 读当前落盘的树根。"""
    fpath = Path(chat_service.PROJECT_CWD) / MAP_KEY
    return json.loads(fpath.read_text())["mindMapData"]["root"]


def disk_doc():
    fpath = Path(chat_service.PROJECT_CWD) / MAP_KEY
    return json.loads(fpath.read_text())


def find_by_uid(node_, uid):
    if node_.get("data", {}).get("uid") == uid:
        return node_
    for c in node_.get("children", []) or []:
        r = find_by_uid(c, uid)
        if r:
            return r
    return None


def all_uids(node_, out=None):
    if out is None:
        out = set()
    uid = node_.get("data", {}).get("uid")
    if uid:
        out.add(uid)
    for c in node_.get("children", []) or []:
        all_uids(c, out)
    return out
