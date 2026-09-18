"""思考等级锁定回归测试。

锁 = 全局默认思考等级（session_dir/thinking_lock.json）。锁定后新 spawn 的 pi
会话用锁定等级作 --thinking（覆盖新建脑图、新分支会话、重启后重建会话）；
未锁定 / 文件缺失 → 默认 max。

注意：不真起 pi 进程（CI 无 pi 二进制），用假 Popen 拦下 spawn 命令。
"""
import io
import json
from pathlib import Path

import backend
import chat_service as cs
from conftest import MAP_KEY

LOCK_FILE = "thinking_lock.json"


def _capture_spawn(monkeypatch):
    """拦 chat_service 里的 subprocess.Popen，返回收集命令行的列表。"""
    cmds = []

    class _FakeProc:
        def __init__(self, cmd, **kwargs):
            cmds.append(cmd)
            self.stdout = io.StringIO("")   # 读线程首行即 EOF，立刻退出
            self.stderr = io.StringIO("")

        def poll(self):
            return None

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(cs.subprocess, "Popen", _FakeProc)
    return cmds


def _lock_path():
    return Path(cs.THINKING_LOCK_PATH)


# ─── 纯逻辑 ───

def test_default_lock_off(env):
    """未设置时：未锁定 + 默认等级 max。"""
    mgr = backend.chat_manager
    assert mgr.get_thinking_lock() == {"locked": False, "level": "max"}
    assert mgr.effective_thinking_level() == "max"


def test_set_lock_persists_and_overrides_default(env):
    """锁定后 effective_thinking_level 用锁定等级；解锁回默认。"""
    mgr = backend.chat_manager
    got = mgr.set_thinking_lock(True, "low")
    assert got == {"locked": True, "level": "low"}
    assert mgr.effective_thinking_level() == "low"
    # 落盘，重启可恢复
    assert json.loads(_lock_path().read_text(encoding="utf-8")) == {"locked": True, "level": "low"}
    assert cs.ChatSessionManager().get_thinking_lock() == {"locked": True, "level": "low"}

    mgr.set_thinking_lock(False)
    assert mgr.get_thinking_lock() == {"locked": False, "level": "low"}  # 等级保留
    assert mgr.effective_thinking_level() == "max"


def test_set_lock_empty_level_keeps_previous(env):
    """level 传空串 = 沿用已存等级（前端点锁定不带等级时不会丢值）。"""
    mgr = backend.chat_manager
    mgr.set_thinking_lock(True, "high")
    assert mgr.set_thinking_lock(True, "")["level"] == "high"


def test_lock_file_missing_or_broken(env):
    """文件缺失 / 内容异常 → 未锁定 + 默认等级，不抛异常。"""
    p = _lock_path()
    p.write_text("not json", encoding="utf-8")
    assert cs.ChatSessionManager().get_thinking_lock() == {"locked": False, "level": "max"}
    p.write_text(json.dumps({"locked": True}), encoding="utf-8")
    assert cs.ChatSessionManager().get_thinking_lock() == {"locked": True, "level": "max"}


# ─── spawn 命令（新建脑图 / 新会话的真实路径）───

def test_spawn_uses_locked_level(env, monkeypatch):
    mgr = cs.ChatSessionManager()
    mgr.set_thinking_lock(True, "low")
    cmds = _capture_spawn(monkeypatch)
    mgr.get_or_spawn(MAP_KEY)
    assert len(cmds) == 1
    cmd = cmds[0]
    assert cmd[cmd.index("--thinking") + 1] == "low"


def test_spawn_defaults_to_max_when_unlocked(env, monkeypatch):
    mgr = cs.ChatSessionManager()
    mgr.set_thinking_lock(False, "low")
    cmds = _capture_spawn(monkeypatch)
    mgr.get_or_spawn(MAP_KEY)
    cmd = cmds[0]
    assert cmd[cmd.index("--thinking") + 1] == "max"


# ─── HTTP 接口（前端调用路径）───

def test_api_thinking_lock_roundtrip(env):
    r = env.get(f"/api/chat/{MAP_KEY}/thinking_lock")
    assert r.status_code == 200 and r.json() == {"locked": False, "level": "max"}

    r = env.post(f"/api/chat/{MAP_KEY}/thinking_lock", json={"locked": True, "level": "medium"})
    assert r.status_code == 200 and r.json() == {"locked": True, "level": "medium"}
    assert backend.chat_manager.effective_thinking_level() == "medium"

    # 前端 api() 会带 ?branch=xxx（全局设置，忽略分支参数）
    r = env.get(f"/api/chat/{MAP_KEY}/thinking_lock?branch=abc")
    assert r.status_code == 200 and r.json() == {"locked": True, "level": "medium"}

    r = env.post(f"/api/chat/{MAP_KEY}/thinking_lock", json={"locked": False})
    assert r.json() == {"locked": False, "level": "medium"}
    assert backend.chat_manager.effective_thinking_level() == "max"

    r = env.post(f"/api/chat/{MAP_KEY}/thinking_lock", json={"locked": True, "level": "low"})
    assert r.json() == {"locked": True, "level": "low"}
