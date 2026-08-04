"""分支标签 _branch_label 数据源优先级测试。

背景：分支绑定/引用依赖节点 uid（稳定唯一，uuidv4）。_branch_label 取分支
根节点文本，曾优先读 _map_state 内存快照（只在 sync/apply 时更新，用户手动
编辑后过期）→ 分支标题显示旧文本。修复为磁盘优先（权威）、state 兜底。
"""
import json
from pathlib import Path

import chat_service
import backend
from conftest import MAP_KEY, node, find_by_uid


def _write_disk_text(uid, text):
    fpath = Path(chat_service.PROJECT_CWD) / MAP_KEY
    doc = json.loads(fpath.read_text())
    n = find_by_uid(doc["mindMapData"]["root"], uid)
    n["data"]["text"] = f"<p>{text}</p>"
    fpath.write_text(json.dumps(doc, ensure_ascii=False))


def test_branch_label_disk_priority(env):
    """用户手动编辑节点落盘后，即使 _map_state 快照过期，label 也要用磁盘新文本。"""
    # env sync 时 a-1 文本是「节点A」，改磁盘为「节点A-已改」模拟用户编辑落盘
    _write_disk_text("a-1", "节点A-已改")
    stale_state = backend.chat_manager._map_state.get(MAP_KEY)
    # 快照里仍是旧文本（未 sync）
    root = chat_service._state_root(stale_state)
    assert find_by_uid(root, "a-1")["data"]["text"] == "<p>节点A</p>"
    label = chat_service._branch_label(MAP_KEY, "a-1", stale_state)
    assert "节点A-已改" in label
    assert "节点A</p>" not in label


def test_branch_label_state_fallback(env):
    """磁盘上还不存在的新节点（用户在 UI 新建未保存）→ state 兜底。"""
    state = json.loads(json.dumps(backend.chat_manager._map_state.get(MAP_KEY)))
    root = chat_service._state_root(state)
    root["children"].append(node("new-uid-1", "新节点未保存"))
    label = chat_service._branch_label(MAP_KEY, "new-uid-1", state)
    assert "新节点未保存" in label


def test_branch_label_uid_fallback(env):
    """uid 不存在 → 返回 uid 本身。"""
    label = chat_service._branch_label(MAP_KEY, "no-such-uid", None)
    assert label == "no-such-uid"


def test_list_agents_label_from_disk(env):
    """mapping 已有分支会话（分支 agent 已创建），用户改节点落盘后 agents 接口 label 用磁盘新文本。"""
    client = env
    backend.chat_manager._mapping[f"{MAP_KEY}::a-1"] = "/tmp/fake-session-a1.jsonl"
    _write_disk_text("a-1", "节点A-接口改")
    r = client.get(f"/api/chat/{MAP_KEY}/agents")
    assert r.status_code == 200
    agents = r.json()
    a1 = [a for a in agents if a["branch_uid"] == "a-1"][0]
    assert "节点A-接口改" in a1["label"]


def test_rollback_returns_restored_tree(env, tmp_path, monkeypatch):
    """回滚响应携带回滚后完整树，且 AI 新增节点已被反向删除（前端 setData 刷新画布的数据源）。"""
    client = env
    monkeypatch.setattr(chat_service, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(chat_service, "MAPPING_PATH", tmp_path / "sessions" / "mapping.json")
    fpath = Path(chat_service.PROJECT_CWD) / MAP_KEY
    doc = json.loads(fpath.read_text())
    ai_node = node("ai-node-1", "AI加的节点")
    doc["mindMapData"]["root"]["children"].append(ai_node)
    fpath.write_text(json.dumps(doc, ensure_ascii=False))
    # session jsonl：2 条 user 消息；turns：第 2 轮加了 ai-node-1
    sdir = tmp_path / "sessions"
    sdir.mkdir(exist_ok=True)
    sf = str(sdir / "test-rollback.jsonl")
    lines = [
        {"type": "meta", "sessionId": "s1"},
        {"type": "message", "message": {"role": "user", "content": "你好"}},
        {"type": "message", "message": {"role": "assistant", "content": "回复"}},
        {"type": "message", "message": {"role": "user", "content": "帮我加个节点"}},
    ]
    Path(sf).write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines))
    turns = [{
        "turn_id": "t1", "user_msg": "帮我加个节点", "user_msg_idx": 2,
        "diff": [{"uid": "ai-node-1", "action": "add", "before": None,
                  "after": {"node": ai_node, "parent_uid": "root-1"}}],
    }]
    chat_service._save_turns(sf, turns)
    backend.chat_manager._mapping[MAP_KEY] = sf
    # 回滚到第 1 条 user 消息之前 → 撤销第 2 轮的 AI 改动
    r = client.post(f"/api/chat/{MAP_KEY}/rollback", json={"user_msg_idx": 1, "branch_uid": ""})
    assert r.status_code == 200
    res = r.json()
    assert res["ok"] is True
    assert res["map_restored"] is True
    assert res["tree"] is not None
    assert find_by_uid(res["tree"], "ai-node-1") is None
    # 磁盘同步删除
    doc2 = json.loads(fpath.read_text())
    assert find_by_uid(doc2["mindMapData"]["root"], "ai-node-1") is None


def test_branch_label_after_apply_updates_disk(env):
    """AI apply 改图落盘后 label 同步更新（磁盘权威路径）。"""
    client = env
    tree = {
        "data": {"text": "<p>root</p>", "uid": "root-1", "richText": True},
        "children": [
            {"data": {"text": "<p>节点A</p>", "uid": "a-1", "richText": True}, "children": []},
            {"data": {"text": "<p>AI改后的B</p>", "uid": "b-1", "richText": True}, "children": []},
        ],
    }
    r = client.post(f"/api/mindmap/apply", json={"key": MAP_KEY, "tree": tree})
    assert r.status_code == 200
    label = chat_service._branch_label(MAP_KEY, "b-1", backend.chat_manager._map_state.get(MAP_KEY))
    assert "AI改后的B" in label
