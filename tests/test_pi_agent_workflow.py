"""Pi Agent 工作流回归测试。

模拟 pi agent 通过 API 对脑图做增删改查的真实场景，
重点覆盖服务重启、前端 sync 数据结构不一致、信封保留等边界。
"""
import json

import backend
import chat_service
from conftest import MAP_KEY, disk_doc, find_by_uid, node, read_disk_root, sample_doc


def ops(client, op_list, key=MAP_KEY):
    return client.post("/api/mindmap/apply_ops", json={"key": key, "ops": op_list})


def get_full(client, key=MAP_KEY):
    return client.get("/api/mindmap/full", params={"key": key})


def sync(client, data, key=MAP_KEY):
    return client.post(f"/api/chat/{key}/sync", json=data)


# ────────────────────────────────────────────
# 场景 1: 前端 syncMap 发裸节点树（无 root key）
# 前端 mindMap.getData() 无参数时返回纯节点树 {data, children}，
# 没有 {root, theme, layout, ...} 包装。
# ────────────────────────────────────────────

class TestBareSyncTree:
    """前端 syncMap 发送裸节点树后，pi agent 的 CRUD 全部正常。"""

    def test_get_mindmap_after_bare_sync(self, env, tmp_path):
        """sync 裸节点树后，get_mindmap 能正常返回。"""
        bare = {"data": {"text": "根", "uid": "root-1"}, "children": [
            {"data": {"text": "A", "uid": "a-1"}, "children": []}
        ]}
        sync(env, bare)
        r = get_full(env)
        assert r.status_code == 200
        assert "#root-1" in r.json()["tree"]

    def test_apply_ops_after_bare_sync(self, env, tmp_path):
        """sync 裸节点树后，apply_ops 能正常操作节点。"""
        bare = {"data": {"text": "根", "uid": "root-1"}, "children": [
            {"data": {"text": "A", "uid": "a-1"}, "children": []}
        ]}
        sync(env, bare)
        r = ops(env, [{"action": "update_text", "uid": "a-1", "text": "A改"}])
        assert r.status_code == 200
        assert r.json()["applied"] == 1

    def test_add_after_bare_sync(self, env, tmp_path):
        """sync 裸节点树后，add 操作正常。"""
        bare = {"data": {"text": "根", "uid": "root-1"}, "children": []}
        sync(env, bare)
        r = ops(env, [{"action": "add", "parent_uid": "root-1", "text": "新节点"}])
        assert r.status_code == 200
        assert r.json()["applied"] == 1

    def test_envelope_preserved_after_bare_sync_ops(self, env, tmp_path):
        """sync 裸节点树 → apply_ops → 磁盘文件的信封（theme/layout/view）不丢失。"""
        bare = {"data": {"text": "根", "uid": "root-1"}, "children": [
            {"data": {"text": "A", "uid": "a-1"}, "children": []}
        ]}
        sync(env, bare)
        ops(env, [{"action": "add", "parent_uid": "root-1", "text": "新"}])
        doc = disk_doc()
        md = doc["mindMapData"]
        assert md.get("theme") == {"template": "avocado", "config": {}}
        assert md.get("layout") == "mindMap"
        assert "root" in md


# ────────────────────────────────────────────
# 场景 2: 服务重启（_map_state 清空）
# Pi agent 的 session 横跨重启，重启后用户继续聊天，
# 前端 syncMap 发裸树 → pi agent 调 API。
# ────────────────────────────────────────────

class TestServiceRestart:
    """模拟服务重启：清空 _map_state/_map_snapshot，只有磁盘文件可用。"""

    def _restart(self):
        """模拟服务重启：清空所有内存状态。"""
        backend.chat_manager._map_state.clear()
        backend.chat_manager._map_snapshot.clear()

    def test_apply_ops_after_restart_no_sync(self, env, tmp_path):
        """重启后没有 sync，apply_ops 应仍能从磁盘 fallback 成功。"""
        self._restart()
        r = ops(env, [{"action": "update_text", "uid": "a-1", "text": "重启后改"}])
        assert r.status_code == 200
        assert r.json()["applied"] == 1
        n = find_by_uid(read_disk_root(tmp_path, env), "a-1")
        assert "重启后改" in n["data"]["text"]

    def test_envelope_preserved_after_restart_ops(self, env, tmp_path):
        """重启后 apply_ops 写回磁盘时信封完好。"""
        self._restart()
        ops(env, [{"action": "add", "parent_uid": "root-1", "text": "重启后加"}])
        doc = disk_doc()
        md = doc["mindMapData"]
        assert md["theme"]["template"] == "avocado"
        assert md["layout"] == "mindMap"

    def test_restart_then_bare_sync_then_ops(self, env, tmp_path):
        """重启 → 前端裸 sync → apply_ops：完整链路正常。"""
        self._restart()
        bare = {"data": {"text": "根", "uid": "root-1"}, "children": [
            {"data": {"text": "A", "uid": "a-1"}, "children": []}
        ]}
        sync(env, bare)
        r = ops(env, [{"action": "update_text", "uid": "a-1", "text": "裸sync后改"}])
        assert r.status_code == 200
        assert r.json()["applied"] == 1
        doc = disk_doc()
        assert doc["mindMapData"]["theme"]["template"] == "avocado"

    def test_get_mindmap_after_restart_requires_sync(self, env, tmp_path):
        """重启后 get_mindmap 在没 sync 时应返回 400（_map_state 空）。"""
        self._restart()
        r = get_full(env)
        assert r.status_code == 400


# ────────────────────────────────────────────
# 场景 3: 用户在 UI 新增节点但未保存到磁盘
# 前端 syncMap 包含磁盘上没有的新节点，
# pi agent 调 apply_ops 应能操作这些节点。
# ────────────────────────────────────────────

class TestUnsavedNodes:
    """用户在 UI 新增但未落盘的节点，pi agent 仍能操作。"""

    def test_ops_on_unsaved_node(self, env, tmp_path):
        """磁盘没有 uid=new-1，但 sync 中有 → apply_ops 能操作它。"""
        md = json.loads((tmp_path / MAP_KEY).read_text())["mindMapData"]
        root = md["root"]
        root["children"].append(node("new-1", "前端新增"))
        sync(env, md)  # 这里 sync 的是 mindMapData 结构
        r = ops(env, [{"action": "update_text", "uid": "new-1", "text": "改了"}])
        assert r.status_code == 200
        assert r.json()["applied"] == 1

    def test_add_child_to_unsaved_node(self, env, tmp_path):
        """在前端新增的未落盘节点下 add 子节点。"""
        md = json.loads((tmp_path / MAP_KEY).read_text())["mindMapData"]
        root = md["root"]
        root["children"].append(node("new-1", "前端新增"))
        sync(env, md)
        r = ops(env, [{"action": "add", "parent_uid": "new-1", "text": "子节点"}])
        assert r.status_code == 200
        assert r.json()["applied"] == 1

    def test_delete_unsaved_node(self, env, tmp_path):
        """删除前端新增的未落盘节点。"""
        md = json.loads((tmp_path / MAP_KEY).read_text())["mindMapData"]
        root = md["root"]
        root["children"].append(node("new-1", "前端新增"))
        sync(env, md)
        # 注意：delete 操作基于磁盘树遍历，磁盘上没有 new-1，
        # 所以这里预期失败——这是当前的设计限制。
        # delete 在磁盘树上找不到 new-1，会报错
        r = ops(env, [{"action": "delete", "uid": "new-1"}])
        # 全部 ops 失败时返回 400
        assert r.status_code == 400

    def test_unsaved_bare_sync(self, env, tmp_path):
        """前端用裸节点树 sync 一棵含新节点的树 → apply_ops 能操作新节点。"""
        bare = {"data": {"text": "根", "uid": "root-1"}, "children": [
            {"data": {"text": "A", "uid": "a-1"}, "children": []},
            {"data": {"text": "前端新增", "uid": "new-1"}, "children": []},
        ]}
        sync(env, bare)
        r = ops(env, [{"action": "update_text", "uid": "new-1", "text": "改了"}])
        assert r.status_code == 200
        assert r.json()["applied"] == 1


# ────────────────────────────────────────────
# 场景 4: 信封保留的完整性验证
# 无论经历什么操作路径，磁盘文件的 theme/layout/config/view
# 都不能丢失或被覆盖。
# ────────────────────────────────────────────

class TestEnvelopeIntegrity:
    """信封（theme/layout/config/view）在各种操作路径下都不丢失。"""

    def _check_envelope(self):
        doc = disk_doc()
        md = doc["mindMapData"]
        assert md.get("theme") == {"template": "avocado", "config": {}}, \
            f"theme 丢失或被改: {md.get('theme')}"
        assert md.get("layout") == "mindMap", \
            f"layout 丢失或被改: {md.get('layout')}"
        assert "root" in md, "root 丢失"
        return md

    def test_envelope_after_update_text(self, env, tmp_path):
        ops(env, [{"action": "update_text", "uid": "a-1", "text": "改"}])
        self._check_envelope()

    def test_envelope_after_add(self, env, tmp_path):
        ops(env, [{"action": "add", "parent_uid": "root-1", "text": "新"}])
        self._check_envelope()

    def test_envelope_after_delete(self, env, tmp_path):
        ops(env, [{"action": "delete", "uid": "a-1-1"}])
        self._check_envelope()

    def test_envelope_after_move(self, env, tmp_path):
        ops(env, [{"action": "move", "uid": "a-1-2", "new_parent_uid": "b-1"}])
        self._check_envelope()

    def test_envelope_after_apply_full_replace(self, env, tmp_path):
        new_tree = {
            "data": {"text": "<p>根节点</p>", "uid": "root-1"},
            "children": [{"data": {"text": "新"}, "children": []}],
        }
        env.post("/api/mindmap/apply", json={"key": MAP_KEY, "tree": new_tree})
        self._check_envelope()

    def test_envelope_after_multiple_ops(self, env, tmp_path):
        """连续多次 apply_ops，每次信封都完好。"""
        ops(env, [{"action": "add", "parent_uid": "root-1", "text": "一", "ref": "r1"}])
        self._check_envelope()
        ops(env, [{"action": "add", "parent_uid": "root-1", "text": "二"}])
        self._check_envelope()
        ops(env, [{"action": "delete", "uid": "a-1-1"}])
        self._check_envelope()

    def test_envelope_after_bare_sync_then_ops(self, env, tmp_path):
        """裸 sync → ops → 信封完好。"""
        bare = {"data": {"text": "根", "uid": "root-1"}, "children": []}
        sync(env, bare)
        ops(env, [{"action": "add", "parent_uid": "root-1", "text": "新"}])
        self._check_envelope()


# ────────────────────────────────────────────
# 场景 5: Pi agent 的典型工作流 — get_mindmap → apply_ops
# 模拟完整交互：读取脑图 → 根据内容决策 → 修改脑图。
# ────────────────────────────────────────────

class TestPiAgentTypicalFlow:
    """Pi agent 的典型交互模式。"""

    def test_read_then_update(self, env, tmp_path):
        """get_mindmap → 找到目标 uid → update_text。"""
        r = get_full(env)
        tree_text = r.json()["tree"]
        assert "#a-1-1" in tree_text  # pi agent 看到 uid
        r = ops(env, [{"action": "update_text", "uid": "a-1-1", "text": "按uid改"}])
        assert r.json()["applied"] == 1
        assert "按uid改" in find_by_uid(
            read_disk_root(tmp_path, env), "a-1-1")["data"]["text"]

    def test_read_then_add_subtree(self, env, tmp_path):
        """get_mindmap → 在某节点下展开一棵子树。"""
        get_full(env)
        r = ops(env, [
            {"action": "add", "parent_uid": "b-1", "text": "步骤1", "ref": "s1",
             "children": [{"text": "细节A"}, {"text": "细节B"}]},
            {"action": "add", "parent_ref": "s1", "text": "步骤1补充"},
        ])
        assert r.json()["applied"] == 2
        b1 = find_by_uid(read_disk_root(tmp_path, env), "b-1")
        s1 = b1["children"][0]
        assert len(s1["children"]) == 3  # 细节A、细节B、步骤1补充

    def test_diff_then_ops_then_diff(self, env, tmp_path):
        """diff → ops → diff：第二次 diff 不应重复报告 AI 的改动。"""
        env.get("/api/mindmap/diff", params={"key": MAP_KEY})
        ops(env, [{"action": "add", "parent_uid": "root-1", "text": "AI加的"}])
        r = env.get("/api/mindmap/diff", params={"key": MAP_KEY})
        body = r.json()
        assert body["full"] is False
        # AI 加的节点不应出现在 diff 的 added 中（snapshot 已前移）
        assert body["added"] == []

    def test_multiple_rounds(self, env, tmp_path):
        """模拟多轮对话：每轮 pi agent 先 diff 再 ops。"""
        # 第一轮
        env.get("/api/mindmap/diff", params={"key": MAP_KEY})
        ops(env, [{"action": "add", "parent_uid": "a-1", "text": "第一轮加的"}])

        # 第二轮
        r = env.get("/api/mindmap/diff", params={"key": MAP_KEY})
        assert r.json()["added"] == []  # 第一轮的改动不在 diff 中
        ops(env, [{"action": "add", "parent_uid": "b-1", "text": "第二轮加的"}])

        # 第三轮
        r = env.get("/api/mindmap/diff", params={"key": MAP_KEY})
        assert r.json()["added"] == []

        # 验证三轮的改动都落盘了
        root = read_disk_root(tmp_path, env)
        a1 = find_by_uid(root, "a-1")
        b1 = find_by_uid(root, "b-1")
        assert any("第一轮" in c["data"]["text"] for c in a1["children"])
        assert any("第二轮" in c["data"]["text"] for c in b1["children"])

    def test_replace_then_ops(self, env, tmp_path):
        """replace_mindmap 整树替换后，apply_ops 仍可正常工作。"""
        new_tree = {
            "data": {"text": "<p>新根</p>", "uid": "root-1"},
            "children": [
                {"data": {"text": "<p>X</p>", "uid": "x-1"}, "children": []},
            ],
        }
        env.post("/api/mindmap/apply", json={"key": MAP_KEY, "tree": new_tree})
        # 旧节点 a-1 已不存在
        r = ops(env, [{"action": "update_text", "uid": "a-1", "text": "ghost"}])
        assert r.status_code == 400
        # 新节点 x-1 可操作
        r = ops(env, [{"action": "update_text", "uid": "x-1", "text": "X改"}])
        assert r.json()["applied"] == 1


# ────────────────────────────────────────────
# 场景 6: sync_map 规范化
# 确保 sync_map 对裸节点树的规范化不破坏其他调用方。
# ────────────────────────────────────────────

class TestSyncNormalization:
    """sync_map 入口的数据规范化。"""

    def test_bare_tree_normalized_to_root_key(self, env):
        """裸节点树 {data, children} 应被包装为 {root: {data, children}}。"""
        bare = {"data": {"text": "根", "uid": "root-1"}, "children": []}
        sync(env, bare)
        state = backend.chat_manager._map_state[MAP_KEY]
        assert "root" in state
        assert state["root"]["data"]["uid"] == "root-1"

    def test_mindmapdata_not_double_wrapped(self, env):
        """正常的 mindMapData {root, theme, ...} 不应被二次包装。"""
        md = {"root": {"data": {"text": "根", "uid": "root-1"}, "children": []},
              "theme": {"template": "avocado"}}
        sync(env, md)
        state = backend.chat_manager._map_state[MAP_KEY]
        assert state["root"]["data"]["uid"] == "root-1"
        assert state.get("theme") == {"template": "avocado"}
        # 不应有 state["root"]["root"]
        assert "root" not in state["root"].get("data", {}).get("text", "")

    def test_commit_map_data_not_double_wrapped(self, env, tmp_path):
        """_commit_map 写入的 _map_state 应该能被后续 sync 正确处理。"""
        # 先让 apply_ops 写一次（_commit_map 会更新 _map_state）
        ops(env, [{"action": "add", "parent_uid": "root-1", "text": "新"}])
        state = backend.chat_manager._map_state[MAP_KEY]
        # _commit_map 写入的是 mindMapData 结构（有 root key）
        assert "root" in state
        # 再 sync 一次（模拟前端 syncMap），不应出错
        sync(env, state)
        r = get_full(env)
        assert r.status_code == 200
