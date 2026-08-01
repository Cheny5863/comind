"""sync / full / diff 接口测试。"""
from conftest import MAP_KEY, find_by_uid


class TestSyncFull:
    def test_full_without_sync_400(self, env):
        r = env.get("/api/mindmap/full", params={"key": "nosync.smm.json"})
        assert r.status_code == 400

    def test_full_returns_tree_with_uids(self, env):
        r = env.get("/api/mindmap/full", params={"key": MAP_KEY})
        assert r.status_code == 200
        tree = r.json()["tree"]
        assert "#root-1" in tree and "#a-1-2" in tree and "节点B" in tree

    def test_illegal_key_rejected(self, env):
        r = env.get("/api/mindmap/full", params={"key": "../etc/passwd"})
        assert r.status_code == 400


class TestDiff:
    def test_first_diff_returns_full(self, env):
        r = env.get("/api/mindmap/diff", params={"key": MAP_KEY})
        assert r.status_code == 200
        body = r.json()
        assert body["full"] is True and "#root-1" in body["tree"]

    def test_diff_snapshot_advances(self, env):
        env.get("/api/mindmap/diff", params={"key": MAP_KEY})
        r = env.get("/api/mindmap/diff", params={"key": MAP_KEY})
        body = r.json()
        assert body["full"] is False
        assert body["added"] == [] and body["removed"] == [] and body["changed"] == []

    def test_diff_detects_add_remove_change_move(self, env):
        env.get("/api/mindmap/diff", params={"key": MAP_KEY})
        # 模拟用户在前端编辑：删 a-1-1、改 b-1 文本、把 a-1-2 移到 b-1 下、新增 c-1
        import backend
        md = backend.chat_manager._map_state[MAP_KEY]
        root = md["root"]
        a1 = find_by_uid(root, "a-1")
        b1 = find_by_uid(root, "b-1")
        a12 = find_by_uid(root, "a-1-2")
        assert a1 and b1 and a12
        a1["children"] = [
            c for c in a1["children"]
            if c["data"]["uid"] not in ("a-1-1", "a-1-2")
        ]
        b1["children"].append(a12)
        b1["data"]["text"] = "<p>节点B改</p>"
        b1["children"].append({
            "data": {"text": "<p>新节点C</p>", "uid": "c-1", "richText": True,
                     "expand": True, "isActive": False},
            "children": [],
        })
        env.post(f"/api/chat/{MAP_KEY}/sync", json=md)
        r = env.get("/api/mindmap/diff", params={"key": MAP_KEY})
        body = r.json()
        added_uids = {n["uid"] for n in body["added"]}
        removed_uids = {n["uid"] for n in body["removed"]}
        changed = {c["uid"]: c for c in body["changed"]}
        assert "c-1" in added_uids
        assert "a-1-1" in removed_uids
        assert changed["b-1"]["new_text"] == "节点B改"
        assert changed["a-1-2"].get("moved") is True
