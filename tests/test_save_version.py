"""回归测试：/api/save 版本号只在 mindMapData 真修改时递增（2026-08-05）。"""
import json

import backend
import chat_service
from conftest import MAP_KEY


def _doc():
    fpath = f"{chat_service.PROJECT_CWD}/{MAP_KEY}"
    return json.loads(open(fpath).read())


def test_save_view_only_does_not_bump_version(env):
    doc = _doc()
    body_a = {**doc, "view": {"x": 100}}
    r = env.post(f"/api/save?name={MAP_KEY}&version=0", json=body_a)
    assert r.json()["status"] == "ok"
    assert r.json()["version"] == 0, f"view 变化不应递增版本: {r.json()}"
    body_b = {**doc, "view": {"x": 200}}
    r2 = env.post(f"/api/save?name={MAP_KEY}&version=0", json=body_b)
    assert r2.json()["status"] == "ok"
    assert r2.json()["version"] == 0, f"另一设备 view 保存也不该触发冲突/递增: {r2.json()}"


def test_save_real_edit_bumps_version_and_conflicts(env):
    doc = _doc()
    edited = json.loads(json.dumps(doc))
    edited["mindMapData"]["root"]["children"].append(
        {"data": {"text": "<p>新节点</p>", "uid": "new-1", "richText": True},
         "children": []}
    )
    r = env.post(f"/api/save?name={MAP_KEY}&version=0", json=edited)
    assert r.json()["status"] == "ok"
    assert r.json()["version"] == 1, f"真修改应递增版本: {r.json()}"
    old_edit = json.loads(json.dumps(doc))
    old_edit["mindMapData"]["root"]["children"].append(
        {"data": {"text": "<p>B的修改</p>", "uid": "old-edit", "richText": True},
         "children": []}
    )
    r2 = env.post(f"/api/save?name={MAP_KEY}&version=0", json=old_edit)
    assert r2.json()["status"] == "conflict", f"陈旧画布真修改应被拒绝: {r2.json()}"
    assert r2.json()["version"] == 1


def test_save_echo_content_does_not_bump(env):
    doc = _doc()
    r = env.post(f"/api/save?name={MAP_KEY}&version=0", json=doc)
    assert r.json()["status"] == "ok"
    assert r.json()["version"] == 0, "内容与磁盘相同（回声）不应递增版本"


def test_save_config_only_does_not_bump(env):
    doc = _doc()
    cfg = {**doc, "mindMapConfig": {"theme": {"template": "dark"}}}
    r = env.post(f"/api/save?name={MAP_KEY}&version=0", json=cfg)
    assert r.json()["status"] == "ok"
    assert r.json()["version"] == 0, "config 变化不应递增版本（不影响内容版本）"
