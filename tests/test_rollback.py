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
        ]))
        assert _user_message_count(f) == 2
