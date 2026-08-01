"""apply_ops 增量改图接口测试 — update_text / add / delete / move。"""
from conftest import MAP_KEY, all_uids, disk_doc, find_by_uid, read_disk_root


def ops(env, op_list):
    return env.post("/api/mindmap/apply_ops", json={"key": MAP_KEY, "ops": op_list})


class TestUpdateText:
    def test_update_text_ok(self, env, tmp_path):
        r = ops(env, [{"action": "update_text", "uid": "a-1-1", "text": "A1新文本"}])
        assert r.status_code == 200 and r.json()["applied"] == 1
        root = read_disk_root(tmp_path, env)
        n = find_by_uid(root, "a-1-1")
        assert n["data"]["text"] == "<p>A1新文本</p>"  # richText 节点自动包裹
        assert n["data"]["uid"] == "a-1-1"  # 其余字段保留

    def test_update_text_html_not_double_wrapped(self, env, tmp_path):
        ops(env, [{"action": "update_text", "uid": "a-1-1", "text": "<p>已带标签</p>"}])
        n = find_by_uid(read_disk_root(tmp_path, env), "a-1-1")
        assert n["data"]["text"] == "<p>已带标签</p>"

    def test_update_text_missing_uid_400(self, env):
        r = ops(env, [{"action": "update_text", "uid": "ghost", "text": "x"}])
        assert r.status_code == 400
        assert "ghost" in r.json()["detail"]


class TestAdd:
    def test_add_child_ok(self, env, tmp_path):
        r = ops(env, [{"action": "add", "parent_uid": "b-1", "text": "B的子节点"}])
        assert r.status_code == 200 and r.json()["applied"] == 1
        b1 = find_by_uid(read_disk_root(tmp_path, env), "b-1")
        child = b1["children"][0]
        assert child["data"]["text"] == "<p>B的子节点</p>"
        assert child["data"]["uid"]  # 自动补 uid
        assert child["data"]["richText"] is True
        assert child["data"]["expand"] is True
        assert child["data"]["isActive"] is False

    def test_add_subtree_recursive(self, env, tmp_path):
        r = ops(env, [{"action": "add", "parent_uid": "b-1", "text": "父",
                       "children": [{"text": "子1"}, {"text": "子2",
                                    "children": [{"text": "孙"}]}]}])
        assert r.json()["applied"] == 1
        b1 = find_by_uid(read_disk_root(tmp_path, env), "b-1")
        sub = b1["children"][0]
        assert len(sub["children"]) == 2
        assert sub["children"][1]["children"][0]["data"]["text"] == "<p>孙</p>"

    def test_add_with_parent_ref_chain(self, env, tmp_path):
        r = ops(env, [
            {"action": "add", "parent_uid": "root-1", "text": "新分支", "ref": "nb"},
            {"action": "add", "parent_ref": "nb", "text": "分支下的子节点"},
        ])
        body = r.json()
        assert body["applied"] == 2
        new_uid = body["created"]["nb"]
        root = read_disk_root(tmp_path, env)
        nb = find_by_uid(root, new_uid)
        assert nb and nb["children"][0]["data"]["text"] == "<p>分支下的子节点</p>"

    def test_add_with_index(self, env, tmp_path):
        ops(env, [{"action": "add", "parent_uid": "root-1", "text": "插到最前", "index": 0}])
        root = read_disk_root(tmp_path, env)
        assert root["children"][0]["data"]["text"] == "<p>插到最前</p>"

    def test_add_missing_parent_400(self, env):
        r = ops(env, [{"action": "add", "parent_uid": "ghost", "text": "x"}])
        assert r.status_code == 400


class TestDelete:
    def test_delete_leaf(self, env, tmp_path):
        r = ops(env, [{"action": "delete", "uid": "a-1-1"}])
        assert r.status_code == 200 and r.json()["applied"] == 1
        root = read_disk_root(tmp_path, env)
        assert find_by_uid(root, "a-1-1") is None
        assert find_by_uid(root, "a-1-2") is not None  # 兄弟不受影响

    def test_delete_subtree_removes_descendants(self, env, tmp_path):
        r = ops(env, [{"action": "delete", "uid": "a-1"}])
        assert r.json()["applied"] == 1
        uids = all_uids(read_disk_root(tmp_path, env))
        assert "a-1" not in uids and "a-1-1" not in uids and "a-1-2" not in uids

    def test_delete_root_rejected(self, env):
        r = ops(env, [{"action": "delete", "uid": "root-1"}])
        assert r.status_code == 400
        assert "根节点" in r.json()["detail"]

    def test_delete_missing_uid_400(self, env):
        r = ops(env, [{"action": "delete", "uid": "ghost"}])
        assert r.status_code == 400

    def test_delete_then_edit_descendant_same_batch(self, env, tmp_path):
        """同批次先删父节点、再改其子孙：子孙已不在树上，必须报错而不是假成功。"""
        r = ops(env, [
            {"action": "delete", "uid": "a-1"},
            {"action": "update_text", "uid": "a-1-1", "text": "改了也白改"},
        ])
        body = r.json()
        # 期望：删除成功 + 第二个 op 报错（该 uid 已随子树删除）
        assert body["applied"] == 1, f"期望只有 delete 生效: {body}"
        assert body["errors"], f"期望对已删子孙的修改报错: {body}"
        assert find_by_uid(read_disk_root(tmp_path, env), "a-1-1") is None
