"""节点图片：agent 写图（set_image / add image）、看图（get_node_image）、回滚。

覆盖的约定：
- base64/本地文件 → 去重进根节点 data.imgMap，节点 data.image 只留 smm_img_key_*
- 图片解析失败必须整条 op 失败（不能只加文字、悄悄丢图）
- 分支 agent 只能看自己分支内的节点图片
"""
import base64
import json
import struct
import zlib
from pathlib import Path

import backend
import chat_service
from conftest import MAP_KEY, find_by_uid, read_disk_root


def make_png(path, w=64, h=32, rgb=(200, 30, 60)) -> bytes:
    """生成纯色 PNG（不依赖 PIL，CI 也能跑）。"""
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    Path(path).write_bytes(png)
    return png


def ops(client, op_list):
    return client.post("/api/mindmap/apply_ops", json={"key": MAP_KEY, "ops": op_list})


def test_set_image_from_file(env, tmp_path):
    png = make_png(tmp_path / "chart.png")
    r = ops(env, [{"action": "set_image", "uid": "a-1", "source": str(tmp_path / "chart.png"),
                   "imageTitle": "CPU 30s"}])
    assert r.status_code == 200 and r.json()["applied"] == 1, r.text

    root = read_disk_root(tmp_path, env)
    node = find_by_uid(root, "a-1")
    key = node["data"]["image"]
    assert key.startswith("smm_img_key_")
    assert node["data"]["imageSize"] == {"width": 64, "height": 32, "custom": False}
    assert node["data"]["imageTitle"] == "CPU 30s"
    data_url = root["data"]["imgMap"][key]
    assert data_url.startswith("data:image/png;base64,")
    assert base64.b64decode(data_url.split(",", 1)[1]) == png


def test_set_image_dedupes_same_bytes(env, tmp_path):
    make_png(tmp_path / "same.png")
    r = ops(env, [
        {"action": "set_image", "uid": "a-1", "source": str(tmp_path / "same.png")},
        {"action": "set_image", "uid": "b-1", "source": str(tmp_path / "same.png")},
    ])
    assert r.json()["applied"] == 2
    root = read_disk_root(tmp_path, env)
    k1 = find_by_uid(root, "a-1")["data"]["image"]
    k2 = find_by_uid(root, "b-1")["data"]["image"]
    assert k1 == k2
    assert len(root["data"]["imgMap"]) == 1


def test_set_image_missing_file_fails_loudly(env, tmp_path):
    r = ops(env, [{"action": "set_image", "uid": "a-1", "source": "/tmp/definitely-not-here.png"}])
    body = r.json()
    detail = body.get("detail", "")
    assert r.status_code == 400 or body.get("applied") == 0
    assert "图片文件不存在" in (detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False))
    assert "image" not in find_by_uid(read_disk_root(tmp_path, env), "a-1")["data"]


def test_set_image_rejects_non_image(env, tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("not an image", encoding="utf-8")
    r = ops(env, [{"action": "set_image", "uid": "a-1", "source": str(p)}])
    text = json.dumps(r.json(), ensure_ascii=False)
    assert r.status_code == 400 or r.json().get("applied") == 0
    assert "不是受支持的图片格式" in text


def test_add_node_with_image(env, tmp_path):
    make_png(tmp_path / "add.png", w=10, h=20)
    r = ops(env, [{"action": "add", "parent_uid": "a-1", "text": "图节点",
                   "image": str(tmp_path / "add.png"), "ref": "n1"}])
    body = r.json()
    assert body["applied"] == 1, body
    uid = body["created"]["n1"]
    node = find_by_uid(read_disk_root(tmp_path, env), uid)
    assert node["data"]["image"].startswith("smm_img_key_")
    assert node["data"]["imageSize"]["width"] == 10


def test_add_with_bad_image_does_not_add_node(env, tmp_path):
    r = ops(env, [{"action": "add", "parent_uid": "a-1", "text": "图节点",
                   "image": str(tmp_path / "nope.png"), "ref": "n1"}])
    text = json.dumps(r.json(), ensure_ascii=False)
    assert r.status_code == 400 or r.json().get("applied") == 0
    assert "图片文件不存在" in text
    parent = find_by_uid(read_disk_root(tmp_path, env), "a-1")
    assert all(c["data"].get("text") != "<p>图节点</p>" for c in parent.get("children") or [])


def test_remove_image(env, tmp_path):
    make_png(tmp_path / "rm.png")
    ops(env, [{"action": "set_image", "uid": "a-1", "source": str(tmp_path / "rm.png")}])
    r = ops(env, [{"action": "set_image", "uid": "a-1", "source": ""}])
    assert r.json()["applied"] == 1
    data = find_by_uid(read_disk_root(tmp_path, env), "a-1")["data"]
    for k in ("image", "imageSize", "imageTitle"):
        assert k not in data


def test_node_image_endpoint(env, tmp_path):
    png = make_png(tmp_path / "read.png", w=48, h=24)
    ops(env, [{"action": "set_image", "uid": "a-1", "source": str(tmp_path / "read.png")}])
    r = env.get("/api/mindmap/node_image", params={"key": MAP_KEY, "uid": "a-1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mime"] == "image/png"
    assert (body["width"], body["height"]) == (48, 24)
    assert base64.b64decode(body["data"]) == png


def test_node_image_without_image(env):
    r = env.get("/api/mindmap/node_image", params={"key": MAP_KEY, "uid": "b-1"})
    assert r.status_code == 400
    assert "没有图片" in r.json()["detail"]


def test_node_image_branch_isolation(env, tmp_path):
    make_png(tmp_path / "x.png")
    ops(env, [{"action": "set_image", "uid": "a-1", "source": str(tmp_path / "x.png")}])
    # b-1 作为分支根，读 a-1 的图 → 拒绝
    r = env.get("/api/mindmap/node_image", params={"key": MAP_KEY, "uid": "a-1", "branch": "b-1"})
    assert r.status_code == 400
    assert "不在你的分支内" in r.json()["detail"]
    # 分支内节点可读
    r2 = env.get("/api/mindmap/node_image", params={"key": MAP_KEY, "uid": "a-1", "branch": "a-1"})
    assert r2.status_code == 200


def test_outline_marks_image_with_size(env, tmp_path):
    make_png(tmp_path / "mark.png", w=64, h=32)
    ops(env, [{"action": "set_image", "uid": "a-1", "source": str(tmp_path / "mark.png")}])
    r = env.get("/api/mindmap/outline", params={"key": MAP_KEY})
    assert "含图片 64x32" in r.json()["tree"]


def test_set_image_records_reversible_turn_diff(env, tmp_path):
    png = make_png(tmp_path / "turn.png")
    skey = chat_service.session_key(MAP_KEY, "")
    backend.chat_manager._turn_ops[skey] = []
    try:
        ops(env, [{"action": "set_image", "uid": "a-1", "source": str(tmp_path / "turn.png")}])
        diff = list(backend.chat_manager._turn_ops[skey])
    finally:
        backend.chat_manager._turn_ops.pop(skey, None)
    assert len(diff) == 1
    d = diff[0]
    assert d["action"] == "set_image" and d["before"]["image"] == ""
    key = d["after"]["image"]
    assert key.startswith("smm_img_key_")
    # diff 里只存短 key + 单条 imgMap（不能把几百 KB 的 base64 塞进 turns.json）
    assert list(d["after"]["imgMap"]) == [key]
    assert base64.b64decode(d["after"]["imgMap"][key].split(",", 1)[1]) == png

    # 反向应用 → 图片被撤销
    skipped = chat_service._apply_reverse(backend.chat_manager, MAP_KEY, diff, "")
    assert skipped == []
    assert "image" not in find_by_uid(read_disk_root(tmp_path, env), "a-1")["data"]


def test_rollback_restores_previous_image(env, tmp_path):
    make_png(tmp_path / "old.png", rgb=(10, 200, 10))
    make_png(tmp_path / "new.png", rgb=(200, 10, 10))
    ops(env, [{"action": "set_image", "uid": "a-1", "source": str(tmp_path / "old.png")}])
    old_key = find_by_uid(read_disk_root(tmp_path, env), "a-1")["data"]["image"]

    skey = chat_service.session_key(MAP_KEY, "")
    backend.chat_manager._turn_ops[skey] = []
    try:
        ops(env, [{"action": "set_image", "uid": "a-1", "source": str(tmp_path / "new.png")}])
        diff = list(backend.chat_manager._turn_ops[skey])
    finally:
        backend.chat_manager._turn_ops.pop(skey, None)

    chat_service._apply_reverse(backend.chat_manager, MAP_KEY, diff, "")
    root = read_disk_root(tmp_path, env)
    assert find_by_uid(root, "a-1")["data"]["image"] == old_key
    assert root["data"]["imgMap"][old_key].startswith("data:image/png;base64,")


def test_restore_image_skips_when_payload_lost(env, tmp_path):
    """imgMap 里数据丢了 → 不恢复一个渲染空白的 key。"""
    root = read_disk_root(tmp_path, env)
    node = find_by_uid(root, "a-1")
    chat_service._restore_node_image(root, node, {"image": "smm_img_key_gone", "imgMap": {}})
    assert "image" not in node["data"]


def _replace_tree(extra_children=None):
    children = [
        {"data": {"text": "<p>节点A</p>", "uid": "a-1"}, "children": []},
        {"data": {"text": "<p>节点B</p>", "uid": "b-1"}, "children": []},
    ]
    children.extend(extra_children or [])
    return {"data": {"text": "<p>根节点</p>", "uid": "root-1"}, "children": children}


def test_replace_new_node_with_image_path(env, tmp_path):
    make_png(tmp_path / "rep.png", w=30, h=10)
    tree = _replace_tree([{"data": {"text": "<p>新图节点</p>", "image": str(tmp_path / "rep.png")}, "children": []}])
    r = env.post("/api/mindmap/apply", json={"key": MAP_KEY, "tree": tree})
    assert r.status_code == 200, r.text
    root = read_disk_root(tmp_path, env)
    new = [c for c in root["children"] if c["data"]["text"] == "<p>新图节点</p>"][0]
    assert new["data"]["image"].startswith("smm_img_key_")
    assert root["data"]["imgMap"][new["data"]["image"]].startswith("data:image/png;base64,")


def test_replace_bad_image_rejected(env, tmp_path):
    tree = _replace_tree([{"data": {"text": "<p>坏图节点</p>", "image": str(tmp_path / "nope.png")}, "children": []}])
    r = env.post("/api/mindmap/apply", json={"key": MAP_KEY, "tree": tree})
    assert r.status_code == 400
    assert "图片文件不存在" in json.dumps(r.json(), ensure_ascii=False)
    root = read_disk_root(tmp_path, env)
    assert all(c["data"]["text"] != "<p>坏图节点</p>" for c in root["children"])


def test_replace_keeps_existing_node_image(env, tmp_path):
    make_png(tmp_path / "keep.png")
    ops(env, [{"action": "set_image", "uid": "a-1", "source": str(tmp_path / "keep.png")}])
    key = find_by_uid(read_disk_root(tmp_path, env), "a-1")["data"]["image"]
    # AI 整树提交时把已有节点的 image 写坏（写了路径）→ 已有节点仍以磁盘为准
    tree = _replace_tree()
    tree["children"][0]["data"]["image"] = "/tmp/garbage.png"
    r = env.post("/api/mindmap/apply", json={"key": MAP_KEY, "tree": tree})
    assert r.status_code == 200, r.text
    assert find_by_uid(read_disk_root(tmp_path, env), "a-1")["data"]["image"] == key
