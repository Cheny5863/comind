"""SSE 重连丢广播回归测试 — last_map_event 重放兜底。

场景：AI 改图广播后前端 SSE 断开（重连窗口期），重连时 subscribe(replay=True)
必须通过 last_map_event 补发最近一次 mindmap_update，否则画布停在旧状态。

注意：不调用 get_or_spawn（会 spawn pi 子进程，CI 环境无 pi 二进制），
手动构造 ChatSession 实例塞进 _sessions，纯测广播/重放逻辑。
"""
import json
import queue
import time

import backend
import chat_service as cs
from chat_service import ChatSession, session_key
from conftest import MAP_KEY


def _fake_session(mgr, branch_uid=""):
    """构造一个不 spawn 的 ChatSession 并塞进 manager._sessions（测试后 pop）。"""
    skey = session_key(MAP_KEY, branch_uid)
    sess = ChatSession(MAP_KEY, branch_uid, session_file=None, lang="zh", map_state=mgr._map_state)
    sess._alive = True
    mgr._sessions[skey] = sess
    return sess, skey


def _cleanup(mgr, sess, q, skey):
    sess.unsubscribe(q)
    mgr._sessions.pop(skey, None)


def test_last_map_event_replay_after_reconnect(env):
    """改图广播 → 旧订阅者退订（模拟断开）→ 新订阅者 replay 应收到 mindmap_update。"""
    mgr = backend.chat_manager
    sess, skey = _fake_session(mgr)
    q1 = sess.subscribe(replay=True)

    # 模拟一次 AI 改图（add 节点）
    root = cs._state_root(mgr._map_state[MAP_KEY])
    r = env.post("/api/mindmap/apply_ops", json={
        "key": MAP_KEY,
        "ops": [{"action": "add", "parent_uid": root["data"]["uid"], "text": "重放节点"}],
    })
    assert r.status_code == 200 and r.json()["applied"] == 1

    # 模拟 SSE 断开：旧订阅者退订
    sess.unsubscribe(q1)

    # 模拟 SSE 重连：新订阅者 replay=True，应通过 last_map_event 收到 mindmap_update
    q2 = sess.subscribe(replay=True)
    found = None
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            line = q2.get(timeout=1)
        except queue.Empty:
            break
        if line is None:
            break
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "mindmap_update":
            found = ev
            break
    _cleanup(mgr, sess, q2, skey)

    assert found is not None, "重连后未通过 last_map_event 收到 mindmap_update"
    assert found["ver"] == mgr._map_ver.get(MAP_KEY, 0)
    assert found["stats"]["added"] == 1
    # 树里应包含新节点
    tree = found.get("tree") or {}
    assert "重放节点" in json.dumps(tree, ensure_ascii=False)


def test_last_map_event_cleared_per_session(env):
    """不同 session（root + 分支）各自缓存自己的 last_map_event，互不串。"""
    mgr = backend.chat_manager
    root_sess, root_key = _fake_session(mgr)
    branch_sess, branch_key = _fake_session(mgr, "branch-1")
    q_root = root_sess.subscribe(replay=True)
    q_branch = branch_sess.subscribe(replay=True)

    # root 改图 → 同脑图所有 session 都收到并缓存 last_map_event
    root = cs._state_root(mgr._map_state[MAP_KEY])
    r = env.post("/api/mindmap/apply_ops", json={
        "key": MAP_KEY,
        "ops": [{"action": "add", "parent_uid": root["data"]["uid"], "text": "根节点新增"}],
    })
    assert r.status_code == 200 and r.json()["applied"] == 1

    assert root_sess.last_map_event is not None
    assert branch_sess.last_map_event is not None  # 同脑图广播给所有 session

    ev = json.loads(branch_sess.last_map_event)
    assert ev["type"] == "mindmap_update"
    assert "根节点新增" in json.dumps(ev.get("tree") or {}, ensure_ascii=False)

    _cleanup(mgr, root_sess, q_root, root_key)
    _cleanup(mgr, branch_sess, q_branch, branch_key)
