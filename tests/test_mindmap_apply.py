"""apply_ops move/混合批次 + apply 整树替换接口测试。"""
import backend
from conftest import MAP_KEY, disk_doc, find_by_uid, read_disk_root


def ops(env, op_list):
    return env.post("/api/mindmap/apply_ops", json={"key": MAP_KEY, "ops": op_list})


class TestMove:
    def test_move_ok(self, env, tmp_path):
        r = ops(env, [{"action": "move", "uid": "a-1-2", "new_parent_uid": "b-1"}])
        assert r.status_code == 200 and r.json()["applied"] == 1
        root = read_disk_root(tmp_path, env)
        a1, b1 = find_by_uid(root, "a-1"), find_by_uid(root, "b-1")
        assert [c["data"]["uid"] for c in a1["children"]] == ["a-1-1"]
        assert b1["children"][0]["data"]["uid"] == "a-1-2"
        assert b1["children"][0]["data"]["text"] == "<p>节点A2</p>"  # 字段保留

    def test_move_with_index(self, env, tmp_path):
        ops(env, [{"action": "move", "uid": "b-1", "new_parent_uid": "root-1", "index": 0}])
        root = read_disk_root(tmp_path, env)
        assert root["children"][0]["data"]["uid"] == "b-1"

    def test_move_to_own_descendant_rejected(self, env, tmp_path):
        r = ops(env, [{"action": "move", "uid": "a-1", "new_parent_uid": "a-1-1"}])
        assert r.status_code == 400
        assert "自己" in r.json()["detail"]
        root = read_disk_root(tmp_path, env)  # 树未被破坏
        assert find_by_uid(root, "a-1")["children"]

    def test_move_to_itself_rejected(self, env):
        r = ops(env, [{"action": "move", "uid": "a-1", "new_parent_uid": "a-1"}])
        assert r.status_code == 400

    def test_move_root_rejected(self, env):
        r = ops(env, [{"action": "move", "uid": "root-1", "new_parent_uid": "a-1"}])
        assert r.status_code == 400

    def test_move_missing_uid_400(self, env):
        r = ops(env, [{"action": "move", "uid": "ghost", "new_parent_uid": "b-1"}])
        assert r.status_code == 400


class TestBatch:
    def test_mixed_batch_partial_success(self, env, tmp_path):
        """部分 op 失败时：成功的照常生效（200），错误逐条返回。"""
        r = ops(env, [
            {"action": "update_text", "uid": "b-1", "text": "B改"},
            {"action": "delete", "uid": "ghost"},
            {"action": "add", "parent_uid": "a-1", "text": "A的新子节点"},
        ])
        assert r.status_code == 200
        body = r.json()
        assert body["applied"] == 2 and len(body["errors"]) == 1
        root = read_disk_root(tmp_path, env)
        assert find_by_uid(root, "b-1")["data"]["text"] == "<p>B改</p>"
        assert len(find_by_uid(root, "a-1")["children"]) == 3

    def test_empty_ops_400(self, env):
        r = ops(env, [])
        assert r.status_code == 400

    def test_unknown_action_400(self, env):
        r = ops(env, [{"action": "explode", "uid": "a-1"}])
        assert r.status_code == 400
        assert "explode" in r.json()["detail"]

    def test_snapshot_advances_after_ops(self, env):
        """ops 提交后 snapshot 前移：后续 diff 不会把 AI 删的节点误报为用户删除。"""
        env.get("/api/mindmap/diff", params={"key": MAP_KEY})  # 建立 snapshot
        ops(env, [{"action": "delete", "uid": "a-1-1"}])
        r = env.get("/api/mindmap/diff", params={"key": MAP_KEY})
        body = r.json()
        assert body["removed"] == []
        assert body["changed"] == []

    def test_disk_memory_consistent_after_ops(self, env, tmp_path):
        """提交后磁盘树与内存状态一致（前端 setData 收到的就是这棵树）。"""
        ops(env, [{"action": "delete", "uid": "b-1"}])
        disk_root = read_disk_root(tmp_path, env)
        mem_root = backend.chat_manager._map_state[MAP_KEY]["root"]
        assert disk_root == mem_root


class TestApplyFullReplace:
    def test_replace_ok_and_defaults(self, env, tmp_path):
        new_tree = {
            "data": {"text": "<p>根节点</p>", "uid": "root-1"},
            "children": [
                {"data": {"text": "全新节点"}, "children": []},
            ],
        }
        r = env.post("/api/mindmap/apply", json={"key": MAP_KEY, "tree": new_tree})
        assert r.status_code == 200
        root = read_disk_root(tmp_path, env)
        assert find_by_uid(root, "a-1") is None  # 整树替换
        child = root["children"][0]
        assert child["data"]["uid"]  # 新节点自动补 uid
        assert child["data"]["text"] == "<p>全新节点</p>"

    def test_existing_node_only_text_changes(self, env, tmp_path):
        """已有 uid 节点：AI 只能改 text，note 等字段以磁盘原值为准。"""
        new_tree = {
            "data": {"text": "<p>根节点</p>", "uid": "root-1"},
            "children": [
                {"data": {"text": "<p>B文本被AI改</p>", "uid": "b-1",
                          "note": "<p>AI想覆盖备注</p>", "richText": True},
                 "children": []},
            ],
        }
        env.post("/api/mindmap/apply", json={"key": MAP_KEY, "tree": new_tree})
        b1 = find_by_uid(read_disk_root(tmp_path, env), "b-1")
        assert b1["data"]["text"] == "<p>B文本被AI改</p>"
        assert b1["data"]["note"] == "<p>备注B</p>"  # 原备注保留

    def test_envelope_preserved(self, env, tmp_path):
        """信封（theme/layout）永远以磁盘为准，apply 不动它。"""
        new_tree = {"data": {"text": "<p>根节点</p>", "uid": "root-1"}, "children": []}
        env.post("/api/mindmap/apply", json={"key": MAP_KEY, "tree": new_tree})
        doc = disk_doc()
        assert doc["mindMapData"]["theme"]["template"] == "avocado"
        assert doc["mindMapData"]["layout"] == "mindMap"

    def test_invalid_tree_400(self, env):
        r = env.post("/api/mindmap/apply",
                     json={"key": MAP_KEY, "tree": {"children": []}})
        assert r.status_code == 400

    def test_invalid_nested_tree_400(self, env):
        r = env.post("/api/mindmap/apply", json={"key": MAP_KEY, "tree": {
            "data": {"text": "x"}, "children": [{"no_data": True}]}})
        assert r.status_code == 400
