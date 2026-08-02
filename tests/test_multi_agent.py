"""多 agent 分支隔离测试：写路径分支校验、整树替换限制、diff 快照隔离。"""
import json

import chat_service
from conftest import MAP_KEY, all_uids, disk_doc, find_by_uid, sample_doc


def test_branch_agent_update_within_branch_ok(env):
    """分支 agent 改自己分支内的节点：成功。"""
    client = env
    r = client.post("/api/mindmap/apply_ops", json={
        "key": MAP_KEY, "branch_uid": "a-1",
        "ops": [{"action": "update_text", "uid": "a-1-1", "text": "A1改"}],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["applied"] == 1
    root = disk_doc()["mindMapData"]["root"]
    assert find_by_uid(root, "a-1-1")["data"]["text"] == "<p>A1改</p>"


def test_branch_agent_add_under_own_branch_ok(env):
    """分支 agent 在自己的分支下加节点：成功。"""
    client = env
    r = client.post("/api/mindmap/apply_ops", json={
        "key": MAP_KEY, "branch_uid": "a-1",
        "ops": [{"action": "add", "parent_uid": "a-1-1", "text": "A1新子节点"}],
    })
    assert r.status_code == 200
    assert r.json()["applied"] == 1


def test_branch_agent_update_outside_branch_rejected(env):
    """分支 agent 改分支外的节点：拒绝。"""
    client = env
    r = client.post("/api/mindmap/apply_ops", json={
        "key": MAP_KEY, "branch_uid": "a-1",
        "ops": [{"action": "update_text", "uid": "b-1", "text": "想改B"}],
    })
    assert r.status_code == 400
    assert "不在你的分支内" in r.text
    # 磁盘不变
    root = disk_doc()["mindMapData"]["root"]
    assert find_by_uid(root, "b-1")["data"]["text"] == "<p>节点B</p>"


def test_branch_agent_add_outside_branch_rejected(env):
    """分支 agent 在分支外的父节点下加节点：拒绝。"""
    client = env
    r = client.post("/api/mindmap/apply_ops", json={
        "key": MAP_KEY, "branch_uid": "a-1",
        "ops": [{"action": "add", "parent_uid": "b-1", "text": "加在B下面"}],
    })
    assert r.status_code == 400
    assert "不在你的分支内" in r.text


def test_branch_agent_delete_outside_branch_rejected(env):
    """分支 agent 删除分支外的节点：拒绝。"""
    client = env
    r = client.post("/api/mindmap/apply_ops", json={
        "key": MAP_KEY, "branch_uid": "a-1",
        "ops": [{"action": "delete", "uid": "b-1"}],
    })
    assert r.status_code == 400
    assert "不在你的分支内" in r.text


def test_branch_agent_move_to_outside_branch_rejected(env):
    """分支 agent 把分支内节点移到分支外：拒绝。"""
    client = env
    r = client.post("/api/mindmap/apply_ops", json={
        "key": MAP_KEY, "branch_uid": "a-1",
        "ops": [{"action": "move", "uid": "a-1-1", "new_parent_uid": "b-1"}],
    })
    assert r.status_code == 400
    assert "不在你的分支内" in r.text


def test_branch_agent_replace_mindmap_rejected(env):
    """分支 agent 整树替换：拒绝（会覆盖其他分支）。"""
    client = env
    new_root = {"data": {"text": "<p>新根</p>", "uid": "root-1", "richText": True},
                "children": []}
    r = client.post("/api/mindmap/apply", json={
        "key": MAP_KEY, "branch_uid": "a-1", "tree": new_root,
    })
    assert r.status_code == 400
    assert "不允许 replace_mindmap" in r.text


def test_root_agent_replace_mindmap_ok(env):
    """root agent（branch_uid 空）整树替换：允许。"""
    client = env
    new_root = {"data": {"text": "<p>新根</p>", "uid": "root-1", "richText": True},
                "children": []}
    r = client.post("/api/mindmap/apply", json={
        "key": MAP_KEY, "branch_uid": "", "tree": new_root,
    })
    assert r.status_code == 200
    assert disk_doc()["mindMapData"]["root"]["data"]["text"] == "<p>新根</p>"


def test_diff_snapshot_isolated_per_branch(env):
    """diff 快照按 (map, branch) 隔离：A 分支 diff 不移动 B 分支快照。"""
    client = env
    # 分支 A 首次 diff：建立快照
    r = client.get(f"/api/mindmap/diff?key={MAP_KEY}&branch=a-1")
    assert r.status_code == 200
    assert r.json()["full"] is True
    # 分支 B 首次 diff：独立快照，也 full
    r = client.get(f"/api/mindmap/diff?key={MAP_KEY}&branch=b-1")
    assert r.status_code == 200
    assert r.json()["full"] is True
    # root 也是独立快照
    r = client.get(f"/api/mindmap/diff?key={MAP_KEY}&branch=")
    assert r.status_code == 200
    assert r.json()["full"] is True
    # 无变化的第二次 diff（各自分支）应为空增量而非 full
    r = client.get(f"/api/mindmap/diff?key={MAP_KEY}&branch=a-1")
    assert r.status_code == 200
    assert r.json()["full"] is False
    assert r.json()["added"] == [] and r.json()["removed"] == [] and r.json()["changed"] == []


def test_diff_sees_other_branch_changes(env):
    """分支 A 改完图后，分支 B 的 diff 能看到 A 的改动（快照未被动）。"""
    client = env
    client.get(f"/api/mindmap/diff?key={MAP_KEY}&branch=a-1")   # A 快照
    client.get(f"/api/mindmap/diff?key={MAP_KEY}&branch=b-1")   # B 快照
    # A 分支改 a-1-1
    r = client.post("/api/mindmap/apply_ops", json={
        "key": MAP_KEY, "branch_uid": "a-1",
        "ops": [{"action": "update_text", "uid": "a-1-1", "text": "A1被A改了"}],
    })
    assert r.status_code == 200
    # B 分支 diff 应看到 a-1-1 变化
    r = client.get(f"/api/mindmap/diff?key={MAP_KEY}&branch=b-1")
    assert r.status_code == 200
    changed = r.json().get("changed", [])
    assert any(c["uid"] == "a-1-1" for c in changed)


def test_agents_list(env):
    """agents 列表接口：root + 已有分支 agent。"""
    client = env
    # 模拟分支 agent 的 mapping 条目（get_or_spawn 时会写入；这里直接塞）
    backend = __import__("backend")
    backend.chat_manager._mapping[MAP_KEY + "::a-1"] = "/tmp/fake-a-1.jsonl"
    r = client.get(f"/api/chat/{MAP_KEY}/agents")
    assert r.status_code == 200
    agents = r.json()
    assert any(a["branch_uid"] == "" for a in agents)      # root
    assert any(a["branch_uid"] == "a-1" for a in agents)   # 分支 A
    assert any(a["label"] == "节点A" for a in agents)      # 分支标签从脑图取
    assert any(a["display_label"] == "节点A" for a in agents)  # 5 字内不截断


def test_display_label_truncate(env):
    """display_label：超过 5 字截断加省略号，label 保留完整。"""
    import chat_service as cs
    assert cs._display_label("短") == "短"
    assert cs._display_label("恰好五个字") == "恰好五个字"
    assert cs._display_label("超过五个字的完整分支名") == "超过五个字…"
    assert cs._display_label("") == ""
