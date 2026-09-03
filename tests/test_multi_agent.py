"""多 agent 分支隔离测试：写路径分支校验、整树替换限制、diff 快照隔离。"""
import json
import os
from pathlib import Path

import chat_service
from conftest import MAP_KEY, all_uids, disk_doc, find_by_uid, sample_doc
from chat_service import safe_key_slug


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


def test_branch_agent_add_parent_ref_chain_ok(env):
    """分支 agent 链式 add + ref：往同批次新建节点下加子节点，必须成功。

    回归 2026-08-02 movexbot 发现的 bug：白名单集合是操作开始时从磁盘
    构建的，不含本批次新建的 uid，导致通过 ref 引用新节点时被误判
    「不在你的分支内」拒绝（applied=1, errors=[...]）。
    修复方案（Ian）：改用 parent 链祖先判断，新节点挂分支内父节点下天然满足。
    """
    client = env
    r = client.post("/api/mindmap/apply_ops", json={
        "key": MAP_KEY, "branch_uid": "a-1",
        "ops": [
            {"action": "add", "parent_uid": "a-1-1", "text": "链式父", "ref": "chain_a"},
            {"action": "add", "parent_ref": "chain_a", "text": "链式子", "ref": "chain_b"},
        ],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["applied"] == 2, body
    assert not body["errors"], body
    uid_a = body["created"]["chain_a"]
    root = disk_doc()["mindMapData"]["root"]
    node_a = find_by_uid(root, uid_a)
    assert node_a is not None
    assert node_a["children"][0]["data"]["text"] == "<p>链式子</p>"


def test_branch_agent_move_within_branch_ok(env):
    """分支 agent 在自己分支内移动节点：成功，move 后同批次仍能继续操作该节点（parent 链同步）。"""
    client = env
    r = client.post("/api/mindmap/apply_ops", json={
        "key": MAP_KEY, "branch_uid": "a-1",
        "ops": [
            {"action": "move", "uid": "a-1-1", "new_parent_uid": "a-1-2"},
            {"action": "update_text", "uid": "a-1-1", "text": "A1移动后改名"},
        ],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["applied"] == 2, body
    assert not body["errors"], body
    root = disk_doc()["mindMapData"]["root"]
    a2 = find_by_uid(root, "a-1-2")
    assert a2["children"][0]["data"]["uid"] == "a-1-1"
    assert find_by_uid(root, "a-1-1")["data"]["text"] == "<p>A1移动后改名</p>"


def test_merge_save_tree_keeps_ai_nodes():
    """前端保存 merge：磁盘独有节点（AI 新增未同步）必须保留，前端独有节点加入。"""
    disk_root = {
        "data": {"uid": "root", "text": "root"},
        "children": [
            {"data": {"uid": "ai-1", "text": "AI新增节点"}, "children": []},
            {"data": {"uid": "same", "text": "AI改过的文本"}, "children": []},
        ],
    }
    front_root = {
        "data": {"uid": "root", "text": "root"},
        "children": [
            {"data": {"uid": "same", "text": "用户看到的旧文本"}, "children": []},
            {"data": {"uid": "user-1", "text": "用户新加节点"}, "children": []},
        ],
    }
    merged = chat_service._merge_save_tree(disk_root, front_root)
    merged_by_uid = {c["data"]["uid"]: c for c in merged["children"]}
    # AI 新增节点保留（不在前端树里）
    assert "ai-1" in merged_by_uid, "AI 新增节点被前端保存覆盖！"
    # 用户新加节点加入
    assert "user-1" in merged_by_uid
    # 同 uid 取前端（用户保存意图）
    assert merged_by_uid["same"]["data"]["text"] == "用户看到的旧文本"
    # 顺序以用户为准：same 在前，AI 新增追加末尾
    assert [c["data"]["uid"] for c in merged["children"]] == ["same", "user-1", "ai-1"]


def test_front_covers_disk():
    """前端画布包含磁盘全部节点 → 完整视图，直接覆盖（用户删除/移动是真实意图）。"""
    disk_root = {"data": {"uid": "root"}, "children": [
        {"data": {"uid": "a"}, "children": []},
        {"data": {"uid": "b"}, "children": []},
    ]}
    # 前端包含全部 + 用户删了 b（disk 有 front 没有 → 不覆盖，走 merge 保留 b）
    front_missing = {"data": {"uid": "root"}, "children": [{"data": {"uid": "a"}, "children": []}]}
    assert not chat_service._front_covers_disk(front_missing, disk_root)
    # 前端包含全部节点（即使顺序不同）→ 覆盖
    front_all = {"data": {"uid": "root"}, "children": [
        {"data": {"uid": "b"}, "children": []},
        {"data": {"uid": "a"}, "children": []},
    ]}
    assert chat_service._front_covers_disk(front_all, disk_root)


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


def test_diff_does_not_see_other_branch_changes(env):
    """分支收紧（2026-09-03）：分支 B 的 diff 感知不到 A 分支的改动。

    diff 快照与对比都限定在分支子树内——分支 agent 只感知自己分支的变化。
    root agent 不受限，仍能看到 A 分支的改动（管全图）。
    """
    client = env
    client.get(f"/api/mindmap/diff?key={MAP_KEY}&branch=a-1")   # A 快照
    client.get(f"/api/mindmap/diff?key={MAP_KEY}&branch=b-1")   # B 快照
    client.get(f"/api/mindmap/diff?key={MAP_KEY}&branch=")      # root 快照
    # A 分支改 a-1-1
    r = client.post("/api/mindmap/apply_ops", json={
        "key": MAP_KEY, "branch_uid": "a-1",
        "ops": [{"action": "update_text", "uid": "a-1-1", "text": "A1被A改了"}],
    })
    assert r.status_code == 200
    # B 分支 diff 不应看到 a-1-1 变化（分支隔离）
    r = client.get(f"/api/mindmap/diff?key={MAP_KEY}&branch=b-1")
    assert r.status_code == 200
    body = r.json()
    changed = body.get("changed", [])
    assert not any(c["uid"] == "a-1-1" for c in changed), f"B 分支 diff 泄漏了 A 分支改动: {changed}"
    # B 分支 diff 对自己分支内无变化 → 空增量
    assert body.get("added") == [] and body.get("removed") == []
    # A 分支自己的 diff 看不到自己刚提交的改动（写者快照已前移）
    r = client.get(f"/api/mindmap/diff?key={MAP_KEY}&branch=a-1")
    assert r.status_code == 200
    assert not any(c["uid"] == "a-1-1" for c in r.json().get("changed", []))
    # root agent 的 diff 能看到 a-1-1 变化（root 管全图，不受分支隔离）
    r = client.get(f"/api/mindmap/diff?key={MAP_KEY}&branch=")
    assert r.status_code == 200
    assert any(c["uid"] == "a-1-1" for c in r.json().get("changed", []))


def test_outline_branch_only_returns_own_subtree(env):
    """分支收紧：outline 带 branch 只返回分支子树，看不到其他分支。"""
    client = env
    r = client.get(f"/api/mindmap/outline?key={MAP_KEY}&branch=a-1")
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "branch"
    tree = body["tree"]
    assert "节点A" in tree and "节点A1" in tree and "节点A2" in tree   # 分支内容
    assert "节点B" not in tree and "根节点" not in tree                # 其他分支/根不可见
    # root（不带 branch）仍能看全图
    r = client.get(f"/api/mindmap/outline?key={MAP_KEY}")
    assert r.status_code == 200
    assert "节点B" in r.json()["tree"]


def test_subtree_branch_rejects_outside_uid(env):
    """分支收紧：subtree 带 branch 时，分支外的 uid 拒绝读取。"""
    client = env
    # 分支 A 内读自己节点 → OK
    r = client.get(f"/api/mindmap/subtree?key={MAP_KEY}&uid=a-1-1&branch=a-1")
    assert r.status_code == 200
    assert "节点A1" in r.json()["tree"]
    # 分支 A 读分支 B 的节点 → 400 拒绝
    r = client.get(f"/api/mindmap/subtree?key={MAP_KEY}&uid=b-1&branch=a-1")
    assert r.status_code == 400
    assert "不在你的分支内" in r.json()["detail"]
    # root（不带 branch）可读任意节点
    r = client.get(f"/api/mindmap/subtree?key={MAP_KEY}&uid=b-1")
    assert r.status_code == 200
    assert "节点B" in r.json()["tree"]


def test_diff_first_full_branch_returns_own_subtree(env):
    """分支收紧：diff 首轮 full 只返回分支子树骨架（不是整图）。"""
    client = env
    r = client.get(f"/api/mindmap/diff?key={MAP_KEY}&branch=a-1")
    assert r.status_code == 200
    body = r.json()
    assert body["full"] is True
    assert body["scope"] == "branch"
    tree = body["tree"]
    assert "节点A" in tree and "节点A1" in tree
    assert "节点B" not in tree and "根节点" not in tree


def test_diff_branch_deleted_branch_errors(env):
    """分支收紧：分支根被删后，outline/subtree/diff 都报错而不是返回全图。"""
    client = env
    # root 删除 a-1 分支整棵
    r = client.post("/api/mindmap/apply_ops", json={
        "key": MAP_KEY, "branch_uid": "",
        "ops": [{"action": "delete", "uid": "a-1"}],
    })
    assert r.status_code == 200
    r = client.get(f"/api/mindmap/outline?key={MAP_KEY}&branch=a-1")
    assert r.status_code == 400
    assert "分支不存在" in r.json()["detail"]
    r = client.get(f"/api/mindmap/diff?key={MAP_KEY}&branch=a-1")
    assert r.status_code == 400


def test_branch_agent_cannot_delete_own_branch_root(env):
    """写路径补漏：分支 agent 不能删除自己的分支根节点。"""
    client = env
    r = client.post("/api/mindmap/apply_ops", json={
        "key": MAP_KEY, "branch_uid": "a-1",
        "ops": [{"action": "delete", "uid": "a-1"}],
    })
    assert r.status_code == 400
    assert "分支根" in r.text
    # root agent 可以删（不受限）
    r = client.post("/api/mindmap/apply_ops", json={
        "key": MAP_KEY, "branch_uid": "",
        "ops": [{"action": "delete", "uid": "a-1"}],
    })
    assert r.status_code == 200
    assert r.json()["applied"] == 1


def test_branch_agent_cannot_move_own_branch_root(env):
    """写路径补漏：分支 agent 不能移动自己的分支根节点（改变归属）。"""
    client = env
    r = client.post("/api/mindmap/apply_ops", json={
        "key": MAP_KEY, "branch_uid": "a-1",
        "ops": [{"action": "move", "uid": "a-1", "new_parent_uid": "b-1"}],
    })
    assert r.status_code == 400
    assert "分支根" in r.text


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
    assert any(a["display_label"] == "节点A" for a in agents)  # 短标签不截断


def test_display_label_truncate(env):
    """display_label：超过 20 字截断加省略号，label 保留完整。"""
    import chat_service as cs
    assert cs._display_label("短") == "短"
    assert cs._display_label("恰好五个字") == "恰好五个字"
    assert cs._display_label("x" * 20) == "x" * 20  # 20 字内完整
    assert cs._display_label("x" * 25) == "x" * 20 + "…"  # 超过 20 截断
    assert cs._display_label("") == ""


def test_reset_new_branch_appears_in_agents(env):
    """bug 回归：reset 新分支（mapping 无条目）后，agents 列表能列出它，
    且 label 是节点可读文本（前5字），不是 uid。"""
    import backend as be
    # 新分支：Charles 抓包（a-1-1 的完整 uid 对应 sample_doc 里的节点）
    # sample_doc: root-1 → [a-1 节点A → [a-1-1 节点A1, a-1-2 节点A2], b-1 节点B]
    client = env
    # 先 reset 一个 mapping 里没有的新分支（a-1-1 节点 A1）
    r = client.post(f"/api/chat/{MAP_KEY}/reset", params={"branch": "a-1-1"})
    assert r.status_code == 200
    agents = client.get(f"/api/chat/{MAP_KEY}/agents").json()
    branch_agent = [a for a in agents if a["branch_uid"] == "a-1-1"]
    assert len(branch_agent) == 1, f"新分支未出现在 agents 列表: {agents}"
    # label 应为节点可读文本（节点A1），display_label 完整；绝不能是 uid
    assert branch_agent[0]["label"] == "节点A1", f"label 错误: {branch_agent[0]['label']!r}"
    assert branch_agent[0]["display_label"] == "节点A1"
    assert not branch_agent[0]["label"].startswith("a-1-1"), "label 是 uid，bug 未修复"


def test_has_history_semantics(env):
    """has_history：「+」语义决策依据。占位/空 session = False（幂等切换），
    有实际用户消息 = True（新开一轮）。"""
    client = env
    # 1) 新分支占位条目（reset 后无会话文件）→ has_history=False
    r = client.post(f"/api/chat/{MAP_KEY}/reset", params={"branch": "a-1-1"})
    assert r.status_code == 200
    ag = [a for a in client.get(f"/api/chat/{MAP_KEY}/agents").json() if a["branch_uid"] == "a-1-1"]
    assert ag and ag[0]["has_history"] is False
    # 2) 往该分支写入实际对话（模拟用户交流：session 文件含 user 消息）
    import backend as be
    # 用 mapping 指向一个真实带 user 消息的 session 文件
    fake_sf = str(Path(be.chat_service.SESSION_DIR) / "fake_hist.smm.jsonl")
    Path(fake_sf).write_text('{"type":"message","message":{"role":"user","content":"hi"}}\n', encoding="utf-8")
    be.chat_manager._mapping[MAP_KEY + "::a-1-1"] = fake_sf
    ag = [a for a in client.get(f"/api/chat/{MAP_KEY}/agents").json() if a["branch_uid"] == "a-1-1"]
    assert ag and ag[0]["has_history"] is True
    # 3) 无 mapping 条目的分支（从未创建）→ has_history=False
    ag = [a for a in client.get(f"/api/chat/{MAP_KEY}/agents").json() if a["branch_uid"] == "b-1"]
    assert not ag  # 从未创建的 branch 不在列表里


def test_all_sessions_flat_sorted_with_branch_tags(env):
    """all_sessions：root + 分支混排、按最后对话时间倒序、分支带标签。"""
    import backend as be
    client = env
    # 造两个 session 文件：一个 root、一个分支，消息 timestamp 不同
    sd = be.chat_service.SESSION_DIR
    root_sf = str(sd / (safe_key_slug(MAP_KEY) + "__old.jsonl"))
    branch_sf = str(sd / (safe_key_slug(MAP_KEY) + "__a-1-1__new.jsonl"))
    # 消息时间：root 旧（2026-07-01），分支新（2026-07-02）
    Path(root_sf).write_text(
        '{"type":"message","message":{"role":"user","content":"r"},"timestamp":"2026-07-01T00:00:00.000Z"}\n',
        encoding="utf-8",
    )
    Path(branch_sf).write_text(
        '{"type":"message","message":{"role":"user","content":"b"},"timestamp":"2026-07-02T00:00:00.000Z"}\n',
        encoding="utf-8",
    )
    # 文件 mtime 故意设成相反（root 新、分支旧）——验证排序用的是消息时间而非 mtime
    old_t = 2000.0
    new_t = 1000.0
    os.utime(root_sf, (old_t, old_t))
    os.utime(branch_sf, (new_t, new_t))
    # 模拟分支 mapping：branch_sf 属于 a-1-1 分支
    be.chat_manager._mapping[MAP_KEY + "::a-1-1"] = branch_sf
    items = client.get(f"/api/chat/{MAP_KEY}/all_sessions").json()
    assert len(items) >= 2
    # 按消息时间倒序：分支（07-02）应排在 root（07-01）前，即使 root mtime 更新
    idx_root = next(i for i, it in enumerate(items) if it["file"] == root_sf)
    idx_branch = next(i for i, it in enumerate(items) if it["file"] == branch_sf)
    assert idx_branch < idx_root, f"排序错误: branch@{idx_branch} root@{idx_root}"
    # 分支带标签，root 无 branch_uid
    branch_item = next(i for i in items if i["branch_uid"] == "a-1-1")
    assert branch_item["display_label"] == "节点A1"
    root_item = next(i for i in items if i["branch_uid"] == "")
    # root 不返回硬编码中文 label（前端用 i18n t("rootAgent") 显示）
    assert root_item["display_label"] == ""
    # modified 是消息时间戳（不是文件 mtime）
    assert abs(branch_item["modified"] - 1782950400.0) < 2  # 2026-07-02 UTC


def test_session_modified_is_last_message_ts(env):
    """bug 回归：历史 session 的 modified 来自最后一条消息时间戳，
    不依赖文件 mtime——选中/读取 session 不应改变排序时间。"""
    import backend as be
    client = env
    sd = be.chat_service.SESSION_DIR
    prefix = safe_key_slug(MAP_KEY) + "__"
    sf = str(sd / (prefix + "ts.jsonl"))
    Path(sf).write_text(
        '{"type":"session","timestamp":"2026-07-01T00:00:00.000Z"}\n'
        '{"type":"message","message":{"role":"user","content":"a"},"timestamp":"2026-07-03T12:00:00.000Z"}\n'
        '{"type":"model_change","timestamp":"2026-07-04T00:00:00.000Z"}\n',
        encoding="utf-8",
    )
    # 文件 mtime 设为 2026-07-05（比最后消息晚）——验证不采用 mtime
    later = 1783209600.0  # 2026-07-05
    os.utime(sf, (later, later))
    items = client.get(f"/api/chat/{MAP_KEY}/all_sessions").json()
    it = next(i for i in items if i["file"] == sf)
    # 应为最后一条 message 的时间（07-03T12:00 = 1783080000），
    # 不是 model_change（07-04）也不是 mtime（07-05）
    assert abs(it["modified"] - 1783080000.0) < 2, f"modified={it['modified']}"


def test_active_removed_from_all_sessions(env):
    """bug 回归：all_sessions 不再返回 active 字段（高亮纯前端逻辑）。"""
    client = env
    items = client.get(f"/api/chat/{MAP_KEY}/all_sessions").json()
    assert all("active" not in i for i in items)


def test_old_session_branch_slug_fallback(env):
    """bug 回归：同一分支的旧 session（不在 mapping 中）仍应显示正确分支标签。

    mapping 只记录当前活跃会话；旧 session 文件只能靠文件名 slug 反查分支。
    """
    import backend as be
    client = env
    sd = be.chat_service.SESSION_DIR
    prefix = safe_key_slug(MAP_KEY) + "__"
    # 旧分支 session：文件名 slug 来自 safe_key_slug(uid)[:8]，a-1-1 保留 - 仍是 "a-1-1"
    slug = "a-1-1"
    old_sf = str(sd / (prefix + slug + "__old.jsonl"))
    Path(old_sf).write_text('{"type":"message","message":{"role":"user","content":"old"}}\n', encoding="utf-8")
    # mapping 里只有 root 和另一个活跃分支
    be.chat_manager._mapping[MAP_KEY] = str(sd / (prefix + "root.jsonl"))
    Path(be.chat_manager._mapping[MAP_KEY]).write_text(
        '{"type":"message","message":{"role":"user","content":"r"}}\n',
        encoding="utf-8",
    )
    items = client.get(f"/api/chat/{MAP_KEY}/all_sessions").json()
    old_it = next(i for i in items if i["file"] == old_sf)
    # slug 前缀匹配 a-1-1 节点 → 应标为分支 a-1-1，不是 root
    assert old_it["branch_uid"] == "a-1-1", f"旧 session 分支归属错误: {old_it['branch_uid']!r}"
    assert old_it["display_label"] == "节点A1"


def test_reset_busy_when_in_turn(env):
    """Ctrl+Alt+N 触发 reset(root) 时，若 root 正在跑（_in_turn=True），
    reset 应 detach（清 mapping）但不 kill——旧 pi 进程留在 _sessions 后台跑完，
    reaper 会在它空闲后清理。返回 ok，前端 connectSSE 触发 get_or_spawn 创建新 session。
    """
    import backend as be
    from chat_service import ChatSession, session_key
    mgr = be.chat_manager
    client = env
    skey = session_key(MAP_KEY, "")
    fake_sf = str(be.chat_service.SESSION_DIR / "fake_busy.smm.jsonl")
    sess = ChatSession(MAP_KEY, "", session_file=None, lang="zh", map_state=mgr._map_state)
    sess._alive = True
    sess._in_turn = True  # 模拟正在跑
    mgr._mapping[skey] = fake_sf
    mgr._sessions[fake_sf] = sess
    try:
        r = client.post(f"/api/chat/{MAP_KEY}/reset")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok", body
        # mapping 已清，但 session 仍活着（detach，不 kill）
        assert mgr._sessions.get(fake_sf) is sess, "正在跑的 session 被 kill 了"
        assert mgr._mapping.get(skey) is None, "mapping 未被 reset 清理"
        assert sess._alive, "detach 后 session 不应被 kill"
    finally:
        mgr._sessions.pop(fake_sf, None)
        mgr._mapping.pop(skey, None)


def test_reset_kills_idle_session(env):
    """空闲（未在跑）session reset：正常 kill + 清 mapping（原有语义不回退）。"""
    import backend as be
    from chat_service import ChatSession, session_key
    mgr = be.chat_manager
    client = env
    skey = session_key(MAP_KEY, "")
    fake_sf = str(be.chat_service.SESSION_DIR / "fake_idle.smm.jsonl")
    sess = ChatSession(MAP_KEY, "", session_file=None, lang="zh", map_state=mgr._map_state)
    sess._alive = True
    sess._in_turn = False
    mgr._mapping[skey] = fake_sf
    mgr._sessions[fake_sf] = sess
    try:
        r = client.post(f"/api/chat/{MAP_KEY}/reset")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok", body
        assert mgr._sessions.get(fake_sf) is None, "空闲 session 未被 reset 清理"
        assert mgr._mapping.get(skey) is None, "mapping 未被 reset 清理"
    finally:
        mgr._sessions.pop(fake_sf, None)
        mgr._mapping.pop(skey, None)


def test_all_sessions_pagination(env):
    """all_sessions 分页：limit 显式传入返回 {items,total}，切片正确且无重叠。

    前端历史列表一页 20 条（防卡顿），快捷键/恢复仍走 limit=0 全量。
    """
    import backend as be
    client = env
    sd = be.chat_service.SESSION_DIR
    prefix = safe_key_slug(MAP_KEY) + "__"
    # 造 25 个 session，消息时间从旧到新递增
    for i in range(25):
        sf = str(sd / (prefix + f"p{i:02d}.jsonl"))
        ts = f"2026-07-{(i % 27) + 1:02d}T00:00:00.000Z"
        Path(sf).write_text(
            '{"type":"message","message":{"role":"user","content":"x"},"timestamp":"' + ts + '"}\n',
            encoding="utf-8",
        )
    # 不带 limit：旧行为，纯 list（旧前端/现有测试兼容）
    all_items = client.get(f"/api/chat/{MAP_KEY}/all_sessions").json()
    assert isinstance(all_items, list)
    # 第一页：20 条（最新在前）
    r1 = client.get(f"/api/chat/{MAP_KEY}/all_sessions?limit=20&offset=0").json()
    assert r1["total"] == len(all_items)
    assert len(r1["items"]) == 20
    # 第二页：剩余
    r2 = client.get(f"/api/chat/{MAP_KEY}/all_sessions?limit=20&offset=20").json()
    assert r2["total"] == len(all_items)
    assert len(r2["items"]) == len(all_items) - 20
    # 两页无重叠、合计等于全量；第一页的 modified 都不早于第二页（倒序）
    files1 = [it["file"] for it in r1["items"]]
    files2 = [it["file"] for it in r2["items"]]
    assert not set(files1) & set(files2)
    assert len(set(files1) | set(files2)) == len(all_items)
    assert min(it["modified"] for it in r1["items"]) >= max(it["modified"] for it in r2["items"])
    # limit=0：全量
    r0 = client.get(f"/api/chat/{MAP_KEY}/all_sessions?limit=0").json()
    assert r0["total"] == len(all_items) and len(r0["items"]) == len(all_items)


def test_all_sessions_meta_cache(env, monkeypatch):
    """元数据缓存：session 文件 (mtime,size) 未变时第二次调用不重读/重算。

    session 多时打开历史列表卡顿的主因是每个文件全量读 jsonl+turns——
    缓存命中后整块复用，只有文件真实变化才重算。
    """
    import backend as be
    client = env
    sd = be.chat_service.SESSION_DIR
    prefix = safe_key_slug(MAP_KEY) + "__"
    sf = str(sd / (prefix + "cache.jsonl"))
    Path(sf).write_text(
        '{"type":"message","message":{"role":"user","content":"c"},"timestamp":"2026-07-01T00:00:00.000Z"}\n',
        encoding="utf-8",
    )
    mgr = be.chat_manager
    calls = {"n": 0}
    orig = mgr._compute_session_meta

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(mgr, "_compute_session_meta", counting)
    client.get(f"/api/chat/{MAP_KEY}/all_sessions?limit=0")
    first = calls["n"]
    assert first >= 1
    # 文件未变 → 缓存命中，不再重算
    client.get(f"/api/chat/{MAP_KEY}/all_sessions?limit=0")
    assert calls["n"] == first, "文件未变却重算了元数据"
    # 文件变化（追加消息）→ 缓存失效，重算一次
    with open(sf, "a", encoding="utf-8") as fh:
        fh.write('{"type":"message","message":{"role":"user","content":"c2"},"timestamp":"2026-07-02T00:00:00.000Z"}\n')
    client.get(f"/api/chat/{MAP_KEY}/all_sessions?limit=0")
    assert calls["n"] == first + 1, "文件变化后缓存未失效"
