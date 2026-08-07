"""轮次回滚单测：净 diff 计算、反向 ops、冲突跳过、turns 文件读写、jsonl 截断。"""
import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from chat_service import (
    ChatSessionManager,
    _compute_net_diff,
    _build_reverse_ops,
    _user_message_count,
    _user_messages_from_jsonl,
    _parse_node_assist,
    _load_turns,
    _save_turns,
    _turns_path,
)


def _mk(uid, text, children=None, note=""):
    n = {"data": {"uid": uid, "text": text, "richText": True}, "children": children or []}
    if note:
        n["data"]["note"] = note
    return n


class TestNetDiff:
    def test_update_text(self):
        before = _mk("r", "root", [_mk("a", "旧文本")])
        after = _mk("r", "root", [_mk("a", "新文本")])
        diff = _compute_net_diff(before, after)
        assert len(diff) == 1
        assert diff[0]["action"] == "update_text"
        assert diff[0]["before"]["text"] == "旧文本"
        assert diff[0]["after"]["text"] == "新文本"

    def test_add_and_delete(self):
        before = _mk("r", "root", [_mk("a", "A")])
        after = _mk("r", "root", [_mk("a", "A"), _mk("b", "B", [_mk("c", "C")])])
        diff = _compute_net_diff(before, after)
        by_uid = {d["uid"]: d for d in diff}
        assert "b" in by_uid and by_uid["b"]["action"] == "add"
        # 子节点 c 由 b 的 add 带出，不单独记录
        assert "c" not in by_uid
        # delete
        diff2 = _compute_net_diff(after, before)
        by_uid2 = {d["uid"]: d for d in diff2}
        assert "b" in by_uid2 and by_uid2["b"]["action"] == "delete"
        assert by_uid2["b"]["before"]["parent_uid"] == "r"
        assert "c" not in by_uid2

    def test_add_then_delete_net_zero(self):
        before = _mk("r", "root", [_mk("a", "A")])
        mid = _mk("r", "root", [_mk("a", "A"), _mk("b", "B")])
        after = _mk("r", "root", [_mk("a", "A")])
        # 回合内先加后删 b：轮前 vs 轮后无变化 → diff 为空
        diff = _compute_net_diff(before, after)
        assert diff == []

    def test_move(self):
        before = _mk("r", "root", [_mk("a", "A", [_mk("x", "X")]), _mk("b", "B")])
        after = _mk("r", "root", [_mk("a", "A"), _mk("b", "B", [_mk("x", "X")])])
        diff = _compute_net_diff(before, after)
        moves = [d for d in diff if d["action"] == "move"]
        assert len(moves) == 1
        assert moves[0]["uid"] == "x"
        assert moves[0]["before"]["parent_uid"] == "a"
        assert moves[0]["after"]["parent_uid"] == "b"

    def test_rich_text_note_change(self):
        before = _mk("r", "root", [_mk("a", "<p>相同</p>", note="旧备注")])
        after = _mk("r", "root", [_mk("a", "<p>相同</p>", note="新备注")])
        diff = _compute_net_diff(before, after)
        assert len(diff) == 1 and diff[0]["action"] == "update_text"


class TestReverseOps:
    def _idx(self, root):
        out = {}
        def walk(n):
            out[n["data"]["uid"]] = n
            for c in n.get("children", []):
                walk(c)
        walk(root)
        return out

    def _parents(self, root, parent=""):
        out = {}
        def walk(n, p):
            out[n["data"]["uid"]] = p
            for c in n.get("children", []):
                walk(c, n["data"]["uid"])
        walk(root, parent)
        return out

    def test_reverse_update_text_conflict_skip(self):
        current = _mk("r", "root", [_mk("a", "AI改后的文本")])
        diff = [{"uid": "a", "action": "update_text",
                 "before": {"text": "旧文本"}, "after": {"text": "AI改后的文本"}}]
        ops, skipped = _build_reverse_ops(diff, self._idx(current), self._parents(current))
        assert len(ops) == 1 and ops[0]["action"] == "update_text" and ops[0]["text"] == "旧文本"
        # 用户改过 → 冲突跳过
        current2 = _mk("r", "root", [_mk("a", "用户手动改的")])
        ops2, skipped2 = _build_reverse_ops(diff, self._idx(current2), self._parents(current2))
        assert ops2 == [] and len(skipped2) == 1

    def test_reverse_delete_restore_and_add_delete(self):
        # 回滚时状态：AI 新增的 b（含子 c）还在；被 AI 删的 d 已不存在
        current = _mk("r", "root", [_mk("a", "A"), _mk("b", "B", [_mk("c", "C")])])
        later = [
            {"uid": "b", "action": "add", "after": {"node": _mk("b", "B", [_mk("c", "C")]), "parent_uid": "r"}},
            {"uid": "d", "action": "delete", "before": {"node": _mk("d", "D"), "parent_uid": "r"}},
        ]
        ops, skipped = _build_reverse_ops(later, self._idx(current), self._parents(current))
        acts = [o["action"] for o in ops]
        assert acts == ["restore", "delete"]  # restore 先（父先于子）
        assert ops[0]["uid"] == "d" and ops[1]["uid"] == "b"
        assert skipped == []

    def test_reverse_delete_user_rebuilt_skip(self):
        current = _mk("r", "root", [_mk("d", "D")])  # 用户重建了 d
        later = [{"uid": "d", "action": "delete", "before": {"node": _mk("d", "D"), "parent_uid": "r"}}]
        ops, skipped = _build_reverse_ops(later, self._idx(current), self._parents(current))
        assert ops == [] and len(skipped) == 1


class TestTurnsStore:
    def test_turns_path(self, tmp_path):
        p = _turns_path(str(tmp_path / "a.smm.json__t.jsonl"))
        assert p.name == "a.smm.json__t.turns.json"

    def test_roundtrip(self, tmp_path):
        sf = str(tmp_path / "s.jsonl")
        turns = [{"turn_id": "t1", "user_msg": "你好", "ts": 1.0, "user_msg_idx": 1, "diff": []}]
        _save_turns(sf, turns)
        assert _load_turns(sf) == turns
        assert _load_turns(str(tmp_path / "nope.jsonl")) == []

    def test_user_message_count(self, tmp_path):
        f = tmp_path / "s.jsonl"
        f.write_text("\n".join([
            json.dumps({"type": "session", "id": "s", "version": 3}),
            json.dumps({"type": "message", "id": "m1", "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}}),
            json.dumps({"type": "message", "id": "m2", "message": {"role": "assistant", "content": []}}),
            json.dumps({"type": "message", "id": "m3", "message": {"role": "user", "content": [{"type": "text", "text": "hi2"}]}}),
        ]), encoding="utf-8")
        assert _user_message_count(f) == 2


class TestParseNodeAssist:
    def test_pure_quote_with_note(self):
        ql, body = _parse_node_assist("[NODE_ASSIST uid=abc123] 用户在节点「多模态融合方案」上求助\n[该节点的备注内容：参考了 2024 论文]\n（引用节点求助）")
        assert ql == [{"uid": "abc123", "text": "多模态融合方案", "note": "参考了 2024 论文"}]
        assert body == "（引用节点求助）"

    def test_pure_quote_no_note(self):
        ql, body = _parse_node_assist("[NODE_ASSIST uid=xyz789] 用户在节点「损失函数设计」上求助\n（引用节点求助）")
        assert ql == [{"uid": "xyz789", "text": "损失函数设计", "note": ""}]
        assert body == "（引用节点求助）"

    def test_quote_with_user_text(self):
        ql, body = _parse_node_assist("[NODE_ASSIST uid=abc123] 用户在节点「多模态融合方案」上求助\n帮我展开这个节点")
        assert ql == [{"uid": "abc123", "text": "多模态融合方案", "note": ""}]
        assert body == "帮我展开这个节点"

    def test_old_format_no_uid(self):
        ql, body = _parse_node_assist("[NODE_ASSIST] 用户在节点「旧格式节点」上求助\n（引用节点求助）")
        assert ql == [{"uid": "", "text": "旧格式节点", "note": ""}]
        assert body == "（引用节点求助）"

    def test_plain_text_not_quote(self):
        assert _parse_node_assist("今天天气怎么样") == ([], "今天天气怎么样")
        assert _parse_node_assist("") == ([], "")

    def test_nested_quotes_in_node_text(self):
        ql, body = _parse_node_assist("[NODE_ASSIST uid=q1] 用户在节点「他说「你好」世界」上求助\n（引用节点求助）")
        assert ql == [{"uid": "q1", "text": "他说「你好」世界", "note": ""}]
        assert body == "（引用节点求助）"

    def test_multi_quote_keeps_order(self):
        # 多引用新格式：前缀（第一个引用）+ 引用列表段 + 用户消息原文（含 [引用N] 占位符）
        text = (
            "[NODE_ASSIST uid=aaa] 用户在节点「第一个节点」上求助\n"
            "引用节点（消息中的 [引用N] 占位符指代这里的节点）：\n"
            "[引用1] uid=aaa 「第一个节点」\n"
            "[引用2] uid=bbb 「第二个节点」\n"
            "这个 [引用1] 是什么意思呢？然后 [引用2] 呢？"
        )
        ql, body = _parse_node_assist(text)
        assert ql == [
            {"uid": "aaa", "text": "第一个节点", "note": ""},
            {"uid": "bbb", "text": "第二个节点", "note": ""},
        ]
        assert body == "这个 [引用1] 是什么意思呢？然后 [引用2] 呢？"

    def test_multi_quote_with_note(self):
        text = (
            "[NODE_ASSIST uid=aaa] 用户在节点「第一个节点」上求助\n"
            "[该节点的备注内容：主备注]\n"
            "引用节点（消息中的 [引用N] 占位符指代这里的节点）：\n"
            "[引用1] uid=aaa 「第一个节点」（备注：主备注）\n"
            "[引用2] uid=bbb 「第二个节点」（备注：次备注）\n"
            "帮我对比一下 [引用1] 和 [引用2]"
        )
        ql, body = _parse_node_assist(text)
        assert ql == [
            {"uid": "aaa", "text": "第一个节点", "note": "主备注"},
            {"uid": "bbb", "text": "第二个节点", "note": "次备注"},
        ]
        assert body == "帮我对比一下 [引用1] 和 [引用2]"


class TestUserMessagesQuoted:
    def _session(self, tmp_path, lines):
        f = tmp_path / "s.jsonl"
        f.write_text("\n".join(lines), encoding="utf-8")
        return f

    def test_pure_quote_strips_prefix_and_note_line(self, tmp_path):
        f = self._session(tmp_path, [
            json.dumps({"type": "message", "message": {"role": "user", "content": "第一轮"}}),
            json.dumps({"type": "message", "message": {"role": "user",
                        "content": "[NODE_ASSIST uid=abc] 用户在节点「多模态融合」上求助\n[该节点的备注内容：备注内容]\n（引用节点求助）"}}),
            json.dumps({"type": "message", "message": {"role": "user",
                        "content": "[NODE_ASSIST uid=xyz] 用户在节点「损失函数」上求助\n帮我展开"}}),
        ])
        ums = _user_messages_from_jsonl(f)
        assert ums[0]["quoted"] is None
        assert ums[0]["quoted_list"] == []
        assert ums[0]["user_msg"] == "第一轮"
        assert ums[1]["quoted_list"] == [{"uid": "abc", "text": "多模态融合", "note": "备注内容"}]
        assert ums[1]["user_msg"] == "（引用节点求助）"  # 占位符剥离后保留，note 行不残留
        assert ums[2]["quoted_list"] == [{"uid": "xyz", "text": "损失函数", "note": ""}]
        assert ums[2]["user_msg"] == "帮我展开"

    def test_multi_quote_roundtrip(self, tmp_path):
        """多引用轮次：quoted_list 按顺序，user_msg 保留 [引用N] 占位符（回填放回 chip 的依据）。"""
        f = self._session(tmp_path, [
            json.dumps({"type": "message", "message": {"role": "user", "content": "第一轮"}}),
            json.dumps({"type": "message", "message": {"role": "user",
                        "content": "[NODE_ASSIST uid=aaa] 用户在节点「第一个」上求助\n引用节点（消息中的 [引用N] 占位符指代这里的节点）：\n[引用1] uid=aaa 「第一个」\n[引用2] uid=bbb 「第二个」\n这个 [引用1] 是什么意思呢？然后 [引用2] 呢？"}}),
        ])
        ums = _user_messages_from_jsonl(f)
        assert ums[1]["quoted_list"] == [
            {"uid": "aaa", "text": "第一个", "note": ""},
            {"uid": "bbb", "text": "第二个", "note": ""},
        ]
        assert ums[1]["user_msg"] == "这个 [引用1] 是什么意思呢？然后 [引用2] 呢？"


class TestListTurnsAndRollbackQuoted:
    """list_turns / rollback 的 quoted 传递（回退列表区分度 + 回填 chip 依据）。"""

    def test_list_turns_carries_quoted(self, env, tmp_path):
        import backend as be
        from chat_service import SESSION_DIR, safe_key_slug
        client = env
        sf = str(SESSION_DIR / (safe_key_slug("test.smm.json") + "__quoted-test.jsonl"))
        Path(sf).write_text("\n".join([
            json.dumps({"type": "message", "message": {"role": "user", "content": "第一轮"}}),
            json.dumps({"type": "message", "message": {"role": "user",
                        "content": "[NODE_ASSIST uid=abc] 用户在节点「多模态融合」上求助\n（引用节点求助）"}}),
            json.dumps({"type": "message", "message": {"role": "user",
                        "content": "[NODE_ASSIST uid=xyz] 用户在节点「损失函数」上求助\n帮我展开"}}),
        ]), encoding="utf-8")
        be.chat_manager._mapping["test.smm.json"] = sf
        turns = be.chat_manager.list_turns("test.smm.json")
        assert len(turns) == 3
        assert turns[0]["quoted"] is None
        assert turns[0]["quoted_list"] == []
        assert turns[0]["user_msg"] == "第一轮"
        assert turns[1]["quoted_list"] == [{"uid": "abc", "text": "多模态融合", "note": ""}]
        assert turns[1]["user_msg"] == "（引用节点求助）"
        assert turns[2]["quoted_list"] == [{"uid": "xyz", "text": "损失函数", "note": ""}]
        assert turns[2]["user_msg"] == "帮我展开"

    def test_rollback_returns_quoted_and_exists(self, env, tmp_path, monkeypatch):
        import backend as be
        from chat_service import SESSION_DIR, safe_key_slug
        client = env
        sf = str(SESSION_DIR / (safe_key_slug("test.smm.json") + "__rollback-quoted.jsonl"))
        Path(sf).write_text("\n".join([
            json.dumps({"type": "message", "message": {"role": "user", "content": "第一轮"}}),
            json.dumps({"type": "message", "message": {"role": "user",
                        "content": "[NODE_ASSIST uid=abc] 用户在节点「节点A」上求助\n（引用节点求助）"}}),
        ]), encoding="utf-8")
        be.chat_manager._mapping["test.smm.json"] = sf
        # 回滚到第 2 轮（引用轮次）之前 → 截断后只剩第 1 轮
        res = be.chat_manager.rollback("test.smm.json", "", 2)
        assert res["ok"] is True
        assert res["user_msg"] == "（引用节点求助）"
        assert res["quoted_list"] == [{"uid": "abc", "text": "节点A", "note": ""}]
        # 引用节点 abc 在测试脑图里存在（a-1 的 uid 是 a-1，这里 abc 不存在）→ False
        assert res["quoted_list_exists"] == [False]

    def test_rollback_quoted_exists_true(self, env, tmp_path):
        import backend as be
        from chat_service import SESSION_DIR, safe_key_slug
        client = env
        sf = str(SESSION_DIR / (safe_key_slug("test.smm.json") + "__rollback-quoted2.jsonl"))
        Path(sf).write_text("\n".join([
            json.dumps({"type": "message", "message": {"role": "user", "content": "第一轮"}}),
            json.dumps({"type": "message", "message": {"role": "user",
                        "content": "[NODE_ASSIST uid=a-1] 用户在节点「节点A」上求助\n（引用节点求助）"}}),
        ]), encoding="utf-8")
        be.chat_manager._mapping["test.smm.json"] = sf
        res = be.chat_manager.rollback("test.smm.json", "", 2)
        assert res["ok"] is True
        assert res["quoted_list"][0]["uid"] == "a-1"
        # a-1 是测试树里的真实节点 → exists True
        assert res["quoted_list_exists"] == [True]

    def test_rollback_multi_quote_exists_mixed(self, env, tmp_path):
        """多引用回滚：存在性按引用顺序逐个返回（存在/不存在混合）。"""
        import backend as be
        from chat_service import SESSION_DIR, safe_key_slug
        client = env
        sf = str(SESSION_DIR / (safe_key_slug("test.smm.json") + "__rollback-multi.jsonl"))
        Path(sf).write_text("\n".join([
            json.dumps({"type": "message", "message": {"role": "user", "content": "第一轮"}}),
            json.dumps({"type": "message", "message": {"role": "user",
                        "content": "[NODE_ASSIST uid=a-1] 用户在节点「节点A」上求助\n引用节点（消息中的 [引用N] 占位符指代这里的节点）：\n[引用1] uid=a-1 「节点A」\n[引用2] uid=ghost 「不存在的节点」\n这个 [引用1] 和 [引用2] 对比一下"}}),
        ]), encoding="utf-8")
        be.chat_manager._mapping["test.smm.json"] = sf
        res = be.chat_manager.rollback("test.smm.json", "", 2)
        assert res["ok"] is True
        assert len(res["quoted_list"]) == 2
        # a-1 真实存在，ghost 不存在
        assert res["quoted_list_exists"] == [True, False]
        # user_msg 保留占位符（前端据此把存在的 [引用1] 还原成 chip，[引用2] 丢弃）
        assert "这个 [引用1] 和 [引用2] 对比一下" in res["user_msg"]
