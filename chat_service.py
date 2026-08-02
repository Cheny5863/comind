"""Chat session manager for simple-mind-map — bridges FastAPI ↔ pi RPC subprocesses.

Also hosts mind-map state sync / diff / apply logic used by the pi extension
tools (get_mindmap, get_mindmap_diff, update_mindmap).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from typing import Iterator

logger = logging.getLogger(__name__)

# PyInstaller 打包后：资源（pi-ext 等）在 _MEIPASS 只读解压目录，可写数据挪到 ~/.comind
if getattr(sys, "frozen", False):
    _RES_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    _DATA_DIR = Path.home() / ".comind"
else:
    _RES_DIR = Path(__file__).resolve().parent
    _DATA_DIR = _RES_DIR


def _default_pi_bin() -> str:
    """发布包可选内置 smm-pi 独立二进制（免 node）；否则回退环境变量/家目录 pi。"""
    if getattr(sys, "frozen", False):
        cand = Path(sys.executable).parent / "smm-pi"
        if cand.exists():
            return str(cand)
    return os.path.expanduser("~/.npm-global/bin/pi")


PI_BIN = os.environ.get("PI_BIN") or _default_pi_bin()
BASE_DIR = _RES_DIR
EXT_PATH = BASE_DIR / "pi-ext" / "mindmap-tools.ts"
PROMPT_PATH = BASE_DIR / "pi-ext" / "system-prompt.md"
SESSION_DIR = Path(os.environ.get(
    "SMM_CHAT_SESSION_DIR",
    os.path.expanduser("~/.comind/chat-sessions"),
))
MAPPING_PATH = SESSION_DIR / "mapping.json"
# 前端可写的 provider key 存储（本机私有；打包模式放 ~/.comind/private，升级不丢）
KEYS_PATH = _DATA_DIR / "private" / "keys.json"
MODELS_PATH = SESSION_DIR / "models.json"
UISTATE_PATH = SESSION_DIR / "uistate.json"
MAX_SESSIONS = 5
IDLE_TIMEOUT = 1800  # 30 min
PROJECT_CWD = os.path.expanduser("~/comind-maps")


def _background_text(map_key: str) -> str:
    """读取该脑图的背景文件内容（不存在返回空串）。

    路径约定与 backend.py 的 _background_path 一致：
    ~/comind-maps/backgrounds/<去掉.smm.json后缀>.md
    """
    base = map_key[:-9] if map_key.endswith(".smm.json") else map_key.rsplit(".", 1)[0]
    bpath = Path(PROJECT_CWD) / "backgrounds" / (base + ".md")
    try:
        if bpath.is_file():
            return bpath.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def _branch_label(map_key: str, branch_uid: str, state: dict | None = None) -> str:
    """取分支根节点的文本标签，用于注入 system prompt 分支职责说明。

    state 是 _map_state 中该脑图的 mindMapData（可选）；优先用它，找不到
    兜底从磁盘脑图文件找。都找不到返回 uid 本身。
    """
    label = branch_uid
    try:
        if state:
            root = _state_root(state)
            if isinstance(root, dict):
                idx = _index_by_uid(root)
                node = idx.get(branch_uid)
                if node and isinstance(node.get("data"), dict):
                    t = _strip_html(node["data"].get("text", ""))
                    if t:
                        return t[:40]
        # 磁盘兜底
        fpath = Path(PROJECT_CWD) / map_key
        if map_key.endswith(".smm.json") and fpath.is_file():
            doc = json.loads(fpath.read_text())
            md = doc.get("mindMapData") or {}
            root = _state_root(md)
            if isinstance(root, dict):
                idx = _index_by_uid(root)
                node = idx.get(branch_uid)
                if node and isinstance(node.get("data"), dict):
                    t = _strip_html(node["data"].get("text", ""))
                    if t:
                        return t[:40]
    except Exception:
        pass
    return label


# provider id → 环境变量名（也用作 private/keys.json 的键名）
PROVIDER_ENV = {
    "deepseek": "DEEPSEEK_API_KEY",
    "moonshotai-cn": "MOONSHOT_API_KEY",
}


def _load_provider_keys() -> dict:
    """Read provider API keys. Never logged.

    读取优先级（从高到低）——本地私人工具，前端明确设置的 key 最优先：
    1. private/keys.json（前端「模型设置」写入，本机私有、git ignore）
    2. 环境变量（部署/进程级配置）
    3. ~/.hermes（pi 的旧配置，兼容兜底）
    """
    keys = {"DEEPSEEK_API_KEY": "", "MOONSHOT_API_KEY": ""}
    # ① private/keys.json（前端可改，最优先）
    try:
        if KEYS_PATH.is_file():
            stored = json.loads(KEYS_PATH.read_text(encoding="utf-8")) or {}
            if isinstance(stored, dict):
                for k in keys:
                    if not keys[k] and stored.get(k):
                        keys[k] = str(stored[k]).strip()
    except Exception:
        pass
    # ② 环境变量（部署级配置兜底）
    for k in keys:
        if not keys[k]:
            keys[k] = os.environ.get(k, "")
    # ③ ~/.hermes 兼容（旧配置兜底）
    if not keys["DEEPSEEK_API_KEY"]:
        env_file = Path(os.path.expanduser("~/.hermes/.env"))
        if env_file.is_file():
            try:
                for line in env_file.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("DEEPSEEK_API_KEY="):
                        keys["DEEPSEEK_API_KEY"] = line.split("=", 1)[1].strip()
                        break
            except Exception:
                pass
    if not keys["MOONSHOT_API_KEY"]:
        cfg_file = Path(os.path.expanduser("~/.hermes/config.yaml"))
        if cfg_file.is_file():
            try:
                import yaml
                cfg = yaml.safe_load(cfg_file.read_text()) or {}
                keys["MOONSHOT_API_KEY"] = (
                    (cfg.get("providers") or {}).get("kimi") or {}
                ).get("api_key", "")
            except Exception:
                pass
    return {k: v for k, v in keys.items() if v}


def save_key(provider: str, key: str | None) -> None:
    """把某个 provider 的 key 写入 private/keys.json（chmod 600）。

    key 为空/None 表示清除。文件格式：{"DEEPSEEK_API_KEY": "...", ...}
    """
    env_var = PROVIDER_ENV.get(provider)
    if not env_var:
        raise ValueError(f"未知 provider: {provider}")
    stored = {}
    try:
        if KEYS_PATH.is_file():
            stored = json.loads(KEYS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    if not isinstance(stored, dict):
        stored = {}
    if key:
        stored[env_var] = str(key).strip()
    else:
        stored.pop(env_var, None)
    KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEYS_PATH.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(KEYS_PATH, 0o600)
    except OSError:
        pass


def reload_keys() -> dict:
    """重新从磁盘加载 provider keys（前端保存 key 后调用）。

    返回更新后的完整 key 表（不含空值）。
    """
    global PROVIDER_KEYS
    PROVIDER_KEYS = _load_provider_keys()
    return dict(PROVIDER_KEYS)


PROVIDER_KEYS = _load_provider_keys()


def safe_key_slug(map_key: str) -> str:
    """Filesystem-safe slug for a map key (session file naming)."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", map_key)


def _new_session_filename(map_key: str, branch_uid: str = "") -> str:
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H-%M-%S") + f"-{now.microsecond // 1000:03d}Z"
    slug = safe_key_slug(map_key)
    if branch_uid:
        # 分支 session 文件带分支标识，避免与 root 会话混淆；root 保持原名（兼容旧数据）
        branch_slug = safe_key_slug(branch_uid)[:8]
        return f"{slug}__{branch_slug}__{stamp}.jsonl"
    return f"{slug}__{stamp}.jsonl"


def session_key(map_key: str, branch_uid: str = "") -> str:
    """多 agent 的会话键：map_key::branch_uid（root agent 的 branch_uid 为空串）。"""
    return f"{map_key}::{branch_uid}" if branch_uid else map_key


def split_session_key(key: str) -> tuple[str, str]:
    """把会话键拆回 (map_key, branch_uid)。兼容旧格式（无 :: 视为 root）。"""
    if "::" in key:
        m, b = key.rsplit("::", 1)
        return m, b
    return key, ""


class ChatSession:
    """Wraps one pi --mode rpc subprocess for a single (map, branch) pair."""

    def __init__(self, map_key: str, branch_uid: str = "", session_file: str | None = None, lang: str = "zh",
                 map_state: dict | None = None):
        self.map_key = map_key
        self.branch_uid = branch_uid or ""  # "" = root agent (full map)
        self.session_file = session_file
        self.lang = lang
        # manager._map_state 的引用（只读用，查分支名）；None 时 _branch_label 走磁盘兜底
        self._map_state = map_state
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.listeners: list[Queue] = []
        # 进行中回合的事件缓冲：页面刷新/SSE 重连时重放，
        # 让前端恢复流式状态和已输出的内容。回合结束清空。
        self._buffer: deque[str] = deque(maxlen=2000)
        self.last_active = time.time()
        self._reader_thread: threading.Thread | None = None
        self._alive = False
        self._in_turn = False  # True between agent_start and agent_end

    def spawn(self) -> None:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        if not self.session_file:
            # Name the session file ourselves so history listing is a simple
            # glob per map key. pi creates the file at the given path.
            self.session_file = str(SESSION_DIR / _new_session_filename(self.map_key, self.branch_uid))
        env = dict(os.environ)
        env.update(PROVIDER_KEYS)
        env["SMM_API_BASE"] = os.environ.get("SMM_API_BASE", "http://localhost:8789")
        env["MAP_KEY"] = self.map_key
        env["BRANCH_UID"] = self.branch_uid

        system_prompt = ""
        if PROMPT_PATH.is_file():
            system_prompt = PROMPT_PATH.read_text()
        bg = _background_text(self.map_key)
        if bg:
            system_prompt += (
                "\n\n## 背景信息（已自动注入）\n\n"
                "下面是你所在脑图的背景信息（自动注入，无需调 get_background）：\n\n"
                + bg
                + "\n"
            )
        # 分支职责：绑定分支的 agent 只能改自己的分支，root agent 管全图
        if self.branch_uid:
            state = (self._map_state or {}).get(self.map_key)
            branch_label = _branch_label(self.map_key, self.branch_uid, state)
            system_prompt += (
                "\n\n## 你负责的分支（重要）\n\n"
                f"你绑定在脑图的分支「{branch_label}」（根节点 uid={self.branch_uid}）上工作。\n"
                "- **改**：update_mindmap / replace_mindmap **只允许操作你分支内的节点**（分支根节点及其子孙）。涉及分支外节点的改动会被后端拒绝。\n"
                "- **看（你的优势）**：你**可以查看整张脑图**——get_mindmap / get_subtree / get_mindmap_diff 返回**全图结构**，不限分支。\n"
                "  其他分支（即使不是你的管辖范围）的内容、结构、变化你都能看到，这是你全面理解脑图、更好帮助用户的优势。\n"
                "- **遇到分支外的请求时（正确姿势）**：不要只说\"改不了\"。\n"
                "  1. 先用 get_mindmap / get_subtree 真正去读那个分支，理解它现在有什么、什么结构；\n"
                "  2. 在回复中**体现你的全图理解**：简述你看到的目标分支现状，基于它给出有价值的分析和建议（可以具体到节点）；\n"
                "  3. 再说明你无法直接修改该分支（职责边界），建议找对应分支的 agent 或 root agent 落地改动，\n"
                "     如果用户请求合理，可以明确说\"把这条建议转给负责 XX 的 agent\"。\n"
            )
        else:
            system_prompt += (
                "\n\n## 你的职责范围（重要）\n\n"
                "你是 root agent，负责整张脑图。可以查看和修改任意节点。\n"
                "用户可能同时让多个分支 agent 在不同分支上并行工作；你负责全局统筹，\n"
                "改图时注意不要破坏其他分支的结构。\n"
            )
        # 界面语言：跟随前端语言，AI 助理用同一种语言回复
        system_prompt += (
            "\n\n## 界面语言\n\n"
            f"用户当前界面语言：{self.lang}（zh=中文，en=English）。"
            "请始终使用该语言回复用户；工具/API 内部文本可保持英文。\n"
        )

        cmd = [
            PI_BIN, "--mode", "rpc",
            "--provider", "deepseek",
            "--model", "deepseek-v4-flash",
            "-e", str(EXT_PATH),
            "--session-dir", str(SESSION_DIR),
            "--session", self.session_file,
            # 内置读写 + bash（改 JS/CSS 等文件用 write/edit；脑图必须走扩展工具）
            "--tools",
            "read,grep,find,ls,write,edit,bash,get_mindmap,get_mindmap_diff,get_subtree,get_background,update_mindmap,replace_mindmap,web_fetch",
        ]
        if system_prompt:
            cmd += ["--append-system-prompt", system_prompt]

        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
            cwd=PROJECT_CWD,
        )
        self._alive = True
        self._reader_thread = threading.Thread(
            target=self._read_stdout, daemon=True, name=f"pi-reader-{self.map_key}"
        )
        self._reader_thread.start()

    def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while self._alive and self.proc.poll() is None:
                line = self.proc.stdout.readline()
                if not line:
                    break
                line = line.rstrip("\n").rstrip("\r")
                if not line:
                    continue
                # 缓冲进行中回合的事件（agent_end/agent_settled 时清空）
                try:
                    ev = json.loads(line)
                    ev_type = ev.get("type")
                    if ev_type == "agent_start":
                        self._in_turn = True
                        sf = ev.get("sessionFile") or ev.get("session_file")
                        if sf:
                            self.session_file = sf
                    self._buffer.append(line)
                    # Keep session alive while pi is producing output
                    self.last_active = time.time()
                    if ev_type in ("agent_end", "agent_settled"):
                        self._in_turn = False
                        self._buffer.clear()
                except json.JSONDecodeError:
                    pass
                # Broadcast to all listeners
                dead = []
                for i, q in enumerate(self.listeners):
                    try:
                        q.put_nowait(line)
                    except Exception:
                        dead.append(i)
                for i in reversed(dead):
                    self.listeners.pop(i)
        except Exception as exc:
            logger.warning("reader error for %s: %s", self.map_key, exc)
        finally:
            self._alive = False
            # Signal EOF to all listeners
            for q in self.listeners:
                try:
                    q.put_nowait(None)
                except Exception:
                    pass

    def send(self, cmd: dict) -> None:
        if not self.proc or not self._alive:
            raise RuntimeError("session not alive")
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps(cmd) + "\n")
        self.proc.stdin.flush()
        self.last_active = time.time()

    def subscribe(self, replay: bool = False) -> Queue:
        q: Queue = Queue(maxsize=2500)
        if replay:
            # 先把缓冲的进行中回合事件塞进去，再接实时流
            for line in self._buffer:
                try:
                    q.put_nowait(line)
                except Exception:
                    break
        self.listeners.append(q)
        return q

    def unsubscribe(self, q: Queue) -> None:
        try:
            self.listeners.remove(q)
        except ValueError:
            pass

    def kill(self) -> None:
        self._alive = False
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc = None

    @property
    def alive(self) -> bool:
        return self._alive and self.proc is not None and self.proc.poll() is None


class ChatSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}
        self._lock = threading.Lock()
        self._mapping = self._load_mapping()
        # per-map 写锁：同一脑图的 apply_ops/apply_map 串行化，防多 agent 并发写互相覆盖
        self._write_locks: dict[str, threading.Lock] = {}
        self._model_pref: dict[str, dict] = self._load_json(MODELS_PATH)
        self._panel_state: dict[str, bool] = self._load_json(UISTATE_PATH)
        self._lang_pref: dict[str, str] = {}  # map_key → 界面语言（zh/en…）
        # Mind-map state: key → current mindMapData / AI-synced snapshot
        self._map_state: dict[str, dict] = {}
        self._map_snapshot: dict[str, dict] = {}
        # Start reaper
        t = threading.Thread(target=self._reap_loop, daemon=True, name="pi-reaper")
        t.start()

    def _load_mapping(self) -> dict[str, str]:
        if MAPPING_PATH.is_file():
            try:
                return json.loads(MAPPING_PATH.read_text())
            except Exception:
                pass
        return {}

    @staticmethod
    def _load_json(path: Path) -> dict:
        if path.is_file():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return {}

    @staticmethod
    def _save_json(path: Path, data: dict) -> None:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))

    def _save_mapping(self) -> None:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        MAPPING_PATH.write_text(json.dumps(self._mapping, indent=2))

    def get_or_spawn(self, map_key: str, branch_uid: str = "") -> ChatSession:
        skey = session_key(map_key, branch_uid)
        with self._lock:
            sess = self._sessions.get(skey)
            if sess and sess.alive:
                return sess
            # Evict the longest-idle session if at max
            if len(self._sessions) >= MAX_SESSIONS:
                # Never evict a session that is mid-turn (actively working)
                evictable = [s for s in self._sessions.values() if not s._in_turn]
                if evictable:
                    oldest = min(evictable, key=lambda s: s.last_active)
                    oldest.kill()
                    self._sessions.pop(session_key(oldest.map_key, oldest.branch_uid), None)
            session_file = self._mapping.get(skey)
            sess = ChatSession(map_key, branch_uid, session_file, self._lang_pref.get(skey, "zh"), self._map_state)
            sess.spawn()
            self._sessions[skey] = sess
            # Re-apply the user's model choice to the fresh pi process
            pref = self._model_pref.get(map_key)
            if pref:
                threading.Thread(
                    target=self._apply_model_pref, args=(sess, pref), daemon=True,
                ).start()
            # Persist the key → session-file binding eagerly; this is what
            # makes history survive restarts.
            if sess.session_file and self._mapping.get(skey) != sess.session_file:
                self._mapping[skey] = sess.session_file
                self._save_mapping()
            return sess

    def prompt(self, map_key: str, message: str, context: dict | None = None, lang: str | None = None,
               branch_uid: str = "") -> None:
        skey = session_key(map_key, branch_uid)
        if lang:
            self._lang_pref[skey] = lang
        sess = self.get_or_spawn(map_key, branch_uid)
        parts = []
        if context:
            quoted = context.get("quoted_node")
            if quoted:
                uid = quoted.get("uid", "") if isinstance(quoted, dict) else ""
                text = quoted.get("text", "") if isinstance(quoted, dict) else str(quoted)
                prefix = f"[NODE_ASSIST uid={uid}]" if uid else "[NODE_ASSIST]"
                parts.append(f"{prefix} 用户在节点「{text}」上求助")
                note = quoted.get("note", "") if isinstance(quoted, dict) else ""
                if note:
                    parts.append(f"[该节点的备注内容：{note}]")
        parts.append(message)
        full_msg = "\n".join(parts)
        sess.send({"type": "prompt", "message": full_msg})

    def abort(self, map_key: str, branch_uid: str = "") -> bool:
        sess = self._sessions.get(session_key(map_key, branch_uid))
        if sess and sess.alive:
            sess.send({"type": "abort"})
            # If pi doesn't end the turn within 5s, force-kill and respawn
            def _force_kill():
                time.sleep(5)
                if sess._in_turn and sess.alive:
                    logger.warning("abort timeout for %s, force-killing pi", map_key)
                    sess._in_turn = False
                    # Synthesize agent_end so listeners/SSE get notified
                    end_ev = json.dumps({"type": "agent_end", "forced": True})
                    sess._buffer.clear()
                    for q in list(sess.listeners):
                        try:
                            q.put_nowait(end_ev)
                        except Exception:
                            pass
                    sess.kill()
            threading.Thread(target=_force_kill, daemon=True).start()
            return True
        return False

    def reset(self, map_key: str, branch_uid: str = "") -> None:
        """Start a new conversation for a (map, branch) pair. The old session
        file is KEPT on disk so it stays available in the history list."""
        skey = session_key(map_key, branch_uid)
        with self._lock:
            sess = self._sessions.pop(skey, None)
            if sess:
                sess.kill()
            self._mapping.pop(skey, None)
            self._save_mapping()

    def events(self, map_key: str, branch_uid: str = "") -> Iterator[str]:
        sess = self.get_or_spawn(map_key, branch_uid)
        q = sess.subscribe(replay=True)
        try:
            while True:
                try:
                    line = q.get(timeout=30)
                except Empty:
                    yield "event: ping\ndata: {}\n\n"
                    continue
                if line is None:
                    break
                # Forward as SSE
                try:
                    ev = json.loads(line)
                    ev_type = ev.get("type", "unknown")
                    yield f"event: {ev_type}\ndata: {line}\n\n"
                    # Persist mapping on session file discovery / conversation end
                    if ev_type in ("agent_start", "agent_end") and sess.session_file:
                        skey = session_key(map_key, branch_uid)
                        if self._mapping.get(skey) != sess.session_file:
                            self._mapping[skey] = sess.session_file
                            self._save_mapping()
                except json.JSONDecodeError:
                    yield f"event: raw\ndata: {line}\n\n"
        finally:
            sess.unsubscribe(q)

    def get_history(self, map_key: str, branch_uid: str = "") -> list[dict]:
        sf = self._mapping.get(session_key(map_key, branch_uid))
        if not sf or not Path(sf).is_file():
            return []
        return parse_session(Path(sf))

    def list_sessions(self, map_key: str, branch_uid: str = "") -> list[dict]:
        """List all saved sessions for a (map, branch), newest first.

        Session files are named ``<safe_key>__<timestamp>.jsonl`` (root) or
        ``<safe_key>__<branch_slug>__<timestamp>.jsonl`` (branch) by us
        at spawn time, so a glob + branch-slug filter is authoritative.
        """
        prefix = safe_key_slug(map_key) + "__"
        branch_slug = safe_key_slug(branch_uid)[:8] if branch_uid else ""
        sessions = []
        for f in sorted(SESSION_DIR.glob(f"{prefix}*.jsonl"), reverse=True):
            try:
                # 分支 session 文件名含 __<branch_slug>__，root 不含
                stem = f.name[len(prefix):]
                is_branch = "__" in stem and stem.count("__") >= 2
                if branch_uid:
                    if not (is_branch and stem.startswith(branch_slug + "__")):
                        continue
                else:
                    if is_branch:
                        continue
                stat = f.stat()
                text = f.read_text()
                user_count = text.count('"role": "user"') + text.count('"role":"user"')
                sessions.append({
                    "file": str(f),
                    "name": f.stem,
                    "modified": stat.st_mtime,
                    "size": stat.st_size,
                    "user_messages": user_count,
                    "active": str(f) == self._mapping.get(session_key(map_key, branch_uid), ""),
                })
            except Exception:
                continue
        return sessions

    def list_agents(self, map_key: str) -> list[dict]:
        """列出该脑图的所有 agent（root + 各分支），供前端多 agent 列表显示。

        数据来源：_map_state 里的分支节点 + mapping 里已存在的会话键。
        返回每个 agent 的 branch_uid（"" = root）、分支名、活跃会话文件、
        是否正在流式输出。
        """
        agents: dict[str, dict] = {}
        # root agent
        root_sf = self._mapping.get(map_key)
        agents[""] = {
            "branch_uid": "",
            "label": "整张脑图",
            "session_file": root_sf,
            "streaming": self._sessions.get(map_key, None) is not None
            and self._sessions[map_key]._in_turn,
        }
        # 各分支 agent（来自 mapping 键 map_key::branch_uid）
        for skey, sf in self._mapping.items():
            mkey, b = split_session_key(skey)
            if mkey != map_key or not b:
                continue
            sess = self._sessions.get(skey)
            agents[b] = {
                "branch_uid": b,
                "label": _branch_label(map_key, b, self._map_state.get(map_key)),
                "session_file": sf,
                "streaming": sess is not None and sess._in_turn,
            }
        return sorted(agents.values(), key=lambda a: (a["branch_uid"] != "", a["label"]))

    def switch_session(self, map_key: str, session_file: str, branch_uid: str = "") -> bool:
        """Switch to a different session file for a (map, branch)."""
        path = Path(session_file)
        if not path.is_file():
            return False
        # Only allow switching to files that belong to this map key (+branch).
        prefix = safe_key_slug(map_key) + "__"
        if path.parent != SESSION_DIR or not path.name.startswith(prefix):
            return False
        skey = session_key(map_key, branch_uid)
        with self._lock:
            sess = self._sessions.pop(skey, None)
            if sess:
                sess.kill()
            self._mapping[skey] = session_file
            self._save_mapping()
        return True

    # ─── 模型管理 ───

    def _rpc_request(self, sess: ChatSession, cmd: dict, timeout: float = 15) -> dict | None:
        """Send an RPC command and wait for its response event."""
        q = sess.subscribe()
        try:
            sess.send(cmd)
            deadline = time.time() + timeout
            while True:
                remain = deadline - time.time()
                if remain <= 0:
                    return None
                try:
                    line = q.get(timeout=remain)
                except Empty:
                    return None
                if line is None:
                    return None
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "response" and ev.get("command") == cmd.get("type"):
                    return ev
        finally:
            sess.unsubscribe(q)

    def get_models(self, map_key: str) -> dict:
        """Available models + current model for this map's session.

        Only the two enabled providers are exposed: deepseek + moonshotai-cn.
        """
        sess = self.get_or_spawn(map_key)
        ev = self._rpc_request(sess, {"type": "get_available_models"})
        models = []
        if ev and ev.get("success"):
            models = [
                m for m in (ev.get("data") or {}).get("models", [])
                if m.get("provider") in ("deepseek", "moonshotai-cn")
            ]
        st = self._rpc_request(sess, {"type": "get_state"})
        current = None
        if st and st.get("success"):
            current = (st.get("data") or {}).get("model")
        return {"models": models, "current": current}

    def set_model(self, map_key: str, provider: str, model_id: str) -> bool:
        if provider not in ("deepseek", "moonshotai-cn"):
            return False
        sess = self.get_or_spawn(map_key)
        ev = self._rpc_request(sess, {
            "type": "set_model", "provider": provider, "modelId": model_id,
        })
        ok = bool(ev and ev.get("success"))
        if ok:
            self._model_pref[map_key] = {"provider": provider, "modelId": model_id}
            self._save_json(MODELS_PATH, self._model_pref)
        return ok

    def _apply_model_pref(self, sess: ChatSession, pref: dict) -> None:
        try:
            self._rpc_request(sess, {
                "type": "set_model",
                "provider": pref["provider"],
                "modelId": pref["modelId"],
            }, timeout=10)
        except Exception:
            pass

    # ─── 面板开关状态（服务端持久化，不依赖浏览器缓存）───

    def get_status(self, map_key: str, branch_uid: str = "") -> dict:
        """Return session streaming status for frontend state recovery."""
        sess = self._sessions.get(session_key(map_key, branch_uid))
        if sess and sess.alive:
            return {"alive": True, "streaming": sess._in_turn}
        return {"alive": False, "streaming": False}

    def get_panel_open(self, map_key: str) -> bool:
        return bool(self._panel_state.get(map_key, False))

    def set_panel_open(self, map_key: str, open_: bool) -> None:
        self._panel_state[map_key] = bool(open_)
        self._save_json(UISTATE_PATH, self._panel_state)

    def restart_all(self) -> int:
        """Key 变更后重启所有空闲的 pi 子进程，让新 key 立即生效。

        进行中（_in_turn）的会话不打断，本回合结束后下次访问自然重建。
        返回重启的会话数。
        """
        with self._lock:
            to_kill = [k for k, s in self._sessions.items() if not s._in_turn]
            for k in to_kill:
                sess = self._sessions.pop(k)
                sess.kill()
            return len(to_kill)

    def _reap_loop(self) -> None:
        while True:
            time.sleep(60)
            now = time.time()
            with self._lock:
                to_kill = [
                    k for k, s in self._sessions.items()
                    if not s._in_turn and (now - s.last_active > IDLE_TIMEOUT or not s.alive)
                ]
                for k in to_kill:
                    sess = self._sessions.pop(k)
                    sess.kill()
                    logger.info("reaped idle session %s", k)


def parse_session(path: Path) -> list[dict]:
    """Parse pi session JSONL (v3 tree), return user/assistant messages."""
    entries = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        entries.append(entry)

    # Build tree: find active branch (follow leaf to root)
    by_id: dict[str, dict] = {}
    children: dict[str, list[str]] = {}
    for e in entries:
        eid = e.get("id")
        if not eid:
            continue
        by_id[eid] = e
        pid = e.get("parentId")
        if pid:
            children.setdefault(pid, []).append(eid)

    # Find the leaf (last entry with no children)
    leaf_id = None
    for e in reversed(entries):
        eid = e.get("id")
        if eid and eid not in children:
            leaf_id = eid
            break

    if not leaf_id:
        return []

    # Walk from leaf to root
    branch_ids = set()
    cur = leaf_id
    while cur:
        branch_ids.add(cur)
        cur = by_id.get(cur, {}).get("parentId")

    # Collect messages in order. pi session entries have type "message";
    # the actual role lives in entry["message"]["role"]
    # (user / assistant / toolResult). Assistant content parts are typed
    # "text" / "thinking" / "toolCall" (camelCase, with name + arguments).
    messages = []
    for e in entries:
        eid = e.get("id")
        if eid not in branch_ids:
            continue
        if e.get("type") != "message":
            continue
        msg = e.get("message", {})
        role = msg.get("role")
        if role == "user":
            text = ""
            content = msg.get("content", "")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = " ".join(
                    c.get("text", "") for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                )
            messages.append({"role": "user", "text": text})
        elif role == "assistant":
            content = msg.get("content", [])
            text_parts = []
            tool_calls = []
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict):
                        if c.get("type") == "text":
                            text_parts.append(c.get("text", ""))
                        elif c.get("type") == "toolCall":
                            tool_calls.append({
                                "name": c.get("name", ""),
                                "args": c.get("arguments", {}),
                            })
            messages.append({
                "role": "assistant",
                "text": "\n".join(text_parts),
                "tool_calls": tool_calls if tool_calls else None,
            })

    return messages


# ─── Mind-map state sync / diff / apply ───

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _HTML_TAG_RE.sub("", text or "").strip()


def slim_tree(
    node: dict,
    depth: int = 0,
    lines: list[str] | None = None,
    text_limit: int = 0,
    note_limit: int = 0,
    show_child_count: bool = False,
) -> str:
    """Render a mind-map node tree as indented plain text with uids.

    Every node's data.uid (if present) is shown as ``#uid`` so the AI can
    address nodes precisely. HTML tags are stripped from node text.
    节点的备注/超链接/标签/图片也一并展示——它们是内容，不只是元数据。

    渐进式披露参数：
    - text_limit > 0：节点文本截断到该长度（加 … 省略号）
    - note_limit > 0：备注截断；-1：备注只显示〔含备注〕标记，不显示内容
    - show_child_count：行尾追加子节点数 [N]，骨架导航用
    """
    if lines is None:
        lines = []
    data = node.get("data", {}) if isinstance(node, dict) else {}
    text = _strip_html(data.get("text", ""))
    if text_limit and len(text) > text_limit:
        text = text[:text_limit] + "…"
    uid = data.get("uid", "")
    prefix = "  " * depth
    suffix = f"  #{uid}" if uid else ""
    extras = []
    note = _strip_html(data.get("note", ""))
    if note:
        if note_limit == -1:
            extras.append("含备注")
        elif note_limit and len(note) > note_limit:
            extras.append(f"备注: {note[:note_limit]}…")
        else:
            extras.append(f"备注: {note[:500]}")
    if data.get("hyperlink"):
        extras.append(f"链接: {data['hyperlink']}")
    if data.get("tag"):
        tag = data["tag"]
        extras.append(f"标签: {tag if isinstance(tag, str) else json.dumps(tag, ensure_ascii=False)}")
    if data.get("image"):
        extras.append("含图片")
    extra_str = (f"  〔{'；'.join(extras)}〕") if extras else ""
    count_str = ""
    if show_child_count:
        n = len(node.get("children", []) or [])
        if n:
            count_str = f"  [{n}子]"
    lines.append(f"{prefix}- {text}{suffix}{count_str}{extra_str}")
    for child in node.get("children", []) or []:
        slim_tree(child, depth + 1, lines, text_limit, note_limit, show_child_count)
    return "\n".join(lines)


def _index_by_uid(node: dict, out: dict[str, dict] | None = None) -> dict[str, dict]:
    """Flatten tree → {uid: node} for nodes that carry data.uid."""
    if out is None:
        out = {}
    if isinstance(node, dict):
        data = node.get("data", {})
        uid = data.get("uid")
        if uid:
            out[uid] = node
        for child in node.get("children", []) or []:
            _index_by_uid(child, out)
    return out


def _index_with_parent(node: dict, parent_uid: str = "", out: dict | None = None) -> dict:
    """Flatten tree → {uid: parent_uid}，用于检测节点移动。"""
    if out is None:
        out = {}
    if isinstance(node, dict):
        uid = node.get("data", {}).get("uid")
        if uid:
            out[uid] = parent_uid
        for child in node.get("children", []) or []:
            _index_with_parent(child, uid, out)
    return out


def _node_summary(node: dict) -> dict:
    data = node.get("data", {}) if isinstance(node, dict) else {}
    return {"uid": data.get("uid", ""), "text": _strip_html(data.get("text", ""))}


def _ensure_map_state(manager: ChatSessionManager, key: str) -> None:
    """_map_state 为空时从磁盘文件加载 mindMapData（服务重启兜底）。

    正常情况下前端 sync 会推最新 UI 状态；重启后没有 sync 时，
    读取路径（outline/diff/subtree）用磁盘上最近落盘的数据兜底，
    避免 AI 拿到 "no map state synced yet" 报错。
    """
    if manager._map_state.get(key):
        return
    fpath = Path(PROJECT_CWD) / key
    if key.endswith(".smm.json") and fpath.is_file():
        try:
            doc = json.loads(fpath.read_text())
            md = doc.get("mindMapData") or {}
            if md:
                manager._map_state[key] = md
                manager._map_snapshot.pop(key, None)
        except Exception:
            pass


def outline_tree(manager: ChatSessionManager, key: str) -> dict:
    """骨架模式：完整结构 + 截断文本 + 子节点数，保证大图也不会爆 token。

    渐进式披露的入口——AI 用骨架导航定位分支，细节再调 subtree_slim。
    """
    _ensure_map_state(manager, key)
    current = manager._map_state.get(key)
    if current is None:
        return {"error": "no map state synced yet; frontend must POST /api/chat/{key}/sync first"}
    root = _state_root(current)
    if not isinstance(root, dict):
        return {"error": "脑图为空"}
    return {
        "mode": "outline",
        "node_count": len(_index_by_uid(root)),
        "tree": slim_tree(root, text_limit=60, note_limit=-1, show_child_count=True),
        "hint": "骨架：文本截断 60 字符、备注只显示标记。定位目标分支后调 get_subtree(uid) 取完整内容。",
    }


def _find_path(root: dict, uid: str, path: list[str] | None = None) -> list[str] | None:
    """返回从根到 uid 节点的文本路径（不含目标自身）。找不到返回 None。"""
    if path is None:
        path = []
    if not isinstance(root, dict):
        return None
    data = root.get("data", {}) if isinstance(root, dict) else {}
    if data.get("uid") == uid:
        return path
    text = _strip_html(data.get("text", "")) or "(空)"
    for child in root.get("children", []) or []:
        res = _find_path(child, uid, path + [text])
        if res is not None:
            return res
    return None


def subtree_slim(manager: ChatSessionManager, key: str, uid: str) -> dict:
    """取某个节点下的完整子树（含备注/链接，文本不截断），带面包屑路径。

    渐进式披露的第二层：AI 用骨架定位 uid 后，这里拿该分支的完整内容。
    """
    _ensure_map_state(manager, key)
    current = manager._map_state.get(key)
    if current is None:
        return {"error": "no map state synced yet; frontend must POST /api/chat/{key}/sync first"}
    root = _state_root(current)
    if not isinstance(root, dict):
        return {"error": "脑图为空"}
    idx = _index_by_uid(root)
    node = idx.get(uid)
    if node is None:
        return {"error": f"uid {uid} 不存在（脑图已变化？重新调 get_mindmap 拿最新骨架）"}
    path = _find_path(root, uid) or []
    self_text = _strip_html(node.get("data", {}).get("text", "")) or "(空)"
    return {
        "uid": uid,
        "path": " > ".join([p for p in path] + [self_text]),
        "node_count": len(_index_by_uid(node)),
        "tree": slim_tree(node, text_limit=0, note_limit=500),
    }


def _state_root(current):
    """从 _map_state 值中安全取根节点树。

    _map_state 期望标准 mindMapData 结构 {root, theme?, ...}，但历史数据/前端
    异常可能存成纯节点树 {data, children, root?}（root 是 copyRenderTree 复制
    出来的冗余字段，值是旧快照）。这里统一防御：
    - root 存在且是节点树（有 data key）→ 取 root
    - 否则认为 current 本身就是节点树
    """
    if isinstance(current, dict):
        r = current.get("root")
        if isinstance(r, dict) and isinstance(r.get("data"), dict):
            return r
    return current


def sync_map(manager: ChatSessionManager, key: str, data: dict) -> None:
    """Frontend pushes the latest mindMapData for a map key.

    前端 syncMap() 调 mindMap.getData()（无参），返回的是纯节点树
    {data, children}，没有 root key。但 _commit_map 等下游代码期望
    _map_state 里是标准 mindMapData 结构 {root, theme?, ...}。
    这里统一规范化：如果传入的 data 有 'data' key（节点树），就包装成
    {root: data}。

    注意：不能只用 "root" not in data 判断——copyRenderTree 会把渲染根节点
    的非 data/children 字段（包括可能存在的 root 冗余字段）复制进输出，
    导致节点树自带 root key，规范化被绕过（AI 读到旧快照，看不到新节点）。
    判断依据改为"data['data'] 是 dict（节点树特征）"，并顺带清掉冗余字段。
    """
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        node = dict(data)
        node.pop("root", None)
        node.pop("smmVersion", None)
        data = {"root": node}
    manager._map_state[key] = data


def diff_map(manager: ChatSessionManager, key: str, branch_uid: str = "") -> dict:
    """Diff current map state vs the AI-synced snapshot (by uid).

    快照按 (map, branch) 隔离：每个 agent 的 diff 快照独立前移，
    避免 A 调过 get_mindmap_diff 后 B 的增量感知被清空（多 agent 并行关键）。

    First call (no snapshot): returns the full slim tree and creates the
    snapshot. Subsequent calls return added/removed/changed lists and then
    update the snapshot to the current state.
    """
    _ensure_map_state(manager, key)
    current = manager._map_state.get(key)
    if current is None:
        return {"error": "no map state synced yet; frontend must POST /api/chat/{key}/sync first"}
    root = _state_root(current)
    snap_key = session_key(key, branch_uid)
    snapshot = manager._map_snapshot.get(snap_key)
    if snapshot is None:
        manager._map_snapshot[snap_key] = json.loads(json.dumps(current))
        return {
            "full": True,
            "tree": slim_tree(root, text_limit=60, note_limit=-1, show_child_count=True),
            "node_count": len(_index_by_uid(root)),
            "hint": "首轮返回骨架（文本截断 60 字符、备注只显示标记）。需要某分支完整内容时调 get_subtree(uid)。",
        }
    cur_idx = _index_by_uid(root)
    old_idx = _index_by_uid(_state_root(snapshot))
    cur_par = _index_with_parent(root)
    old_par = _index_with_parent(_state_root(snapshot))
    added, removed, changed = [], [], []
    for uid, node in cur_idx.items():
        if uid not in old_idx:
            added.append(_node_summary(node))
        else:
            old_data = old_idx[uid].get("data", {})
            new_data = node.get("data", {})
            old_text = _strip_html(old_data.get("text", ""))
            new_text = _strip_html(new_data.get("text", ""))
            old_note = _strip_html(old_data.get("note", ""))
            new_note = _strip_html(new_data.get("note", ""))
            moved = old_par.get(uid, "") != cur_par.get(uid, "")
            if old_text != new_text or old_note != new_note or moved:
                entry: dict = {"uid": uid, "old_text": old_text, "new_text": new_text}
                if old_note != new_note:
                    entry["old_note"] = old_note
                    entry["new_note"] = new_note
                if moved:
                    entry["moved"] = True
                    entry["old_parent"] = old_par.get(uid, "")
                    entry["new_parent"] = cur_par.get(uid, "")
                changed.append(entry)
    for uid, node in old_idx.items():
        if uid not in cur_idx:
            removed.append(_node_summary(node))
    # Snapshot moves forward with every diff — per (map, branch)
    manager._map_snapshot[snap_key] = json.loads(json.dumps(current))
    return {"full": False, "added": added, "removed": removed, "changed": changed}


def _validate_tree(node, path: str = "root") -> str | None:
    if not isinstance(node, dict):
        return f"{path}: node must be an object"
    if "data" not in node or not isinstance(node["data"], dict):
        return f"{path}: missing/invalid 'data' object"
    children = node.get("children", [])
    if not isinstance(children, list):
        return f"{path}: 'children' must be a list"
    for i, child in enumerate(children):
        err = _validate_tree(child, f"{path}.children[{i}]")
        if err:
            return err
    return None


def _load_doc_for_write(manager: ChatSessionManager, key: str):
    """加载脑图用于写入。返回 (doc, mindMapData, old_by_uid, fpath)。

    信封（theme/layout/view 等）和节点树都以磁盘文件为准。
    如果 _map_state 中包含磁盘上不存在的新节点（用户在 UI 中新增
    但尚未保存），把它们合并到 uid 索引中，确保 apply_ops 能操作。
    """
    doc, file_md = {}, {}
    fpath = Path(PROJECT_CWD) / key
    if key.endswith(".smm.json") and fpath.is_file():
        try:
            doc = json.loads(fpath.read_text())
            file_md = doc.get("mindMapData") or {}
        except Exception as exc:
            logger.warning("read %s failed: %s", key, exc)
    if not file_md:
        # 磁盘文件不存在/为空时退化为内存状态
        current = manager._map_state.get(key) or {}
        file_md = dict(current) if isinstance(current, dict) else {}

    # 用磁盘树构建 uid 索引
    disk_root = file_md.get("root", file_md)
    old_by_uid = _index_by_uid(disk_root if isinstance(disk_root, dict) else {})

    # 把 _map_state 中磁盘上没有的节点补进索引（用户 UI 新增未落盘的节点）
    mem_state = manager._map_state.get(key)
    if mem_state and isinstance(mem_state, dict):
        mem_root = mem_state.get("root", mem_state)
        if isinstance(mem_root, dict):
            mem_index = _index_by_uid(mem_root)
            for uid, node in mem_index.items():
                if uid not in old_by_uid:
                    old_by_uid[uid] = node

    return doc, file_md, old_by_uid, fpath


def _commit_map(manager: ChatSessionManager, key: str, doc: dict, new_data: dict, branch_uid: str = "") -> None:
    """提交新 mindMapData：更新内存、备份、落盘、SSE 广播。

    branch_uid 是写者（哪个 agent 提交的）。写者自己的 diff 快照前移
    （自己改的不会重复出现在自己的 diff 里）；其他 agent 的快照不动，
    这样它们下一次 get_mindmap_diff 能看到本次变化（多 agent 协作关键）。
    """
    manager._map_state[key] = new_data
    writer_key = session_key(key, branch_uid)
    manager._map_snapshot[writer_key] = json.loads(json.dumps(new_data))
    # 广播 mindmap_update 给同脑图的所有 agent 会话（root + 各分支），
    # 让并行工作的其他 agent 前端实时看到结构变化。
    fpath = Path(PROJECT_CWD) / key
    if key.endswith(".smm.json"):
        try:
            if fpath.is_file():
                backup = fpath.with_suffix(fpath.suffix + ".aibak")
                backup.write_text(fpath.read_text())
            doc["mindMapData"] = new_data
            fpath.write_text(json.dumps(doc, ensure_ascii=False, indent=2))
        except Exception as exc:
            logger.warning("persist %s failed: %s", key, exc)
    event = json.dumps(
        {"type": "mindmap_update", "tree": new_data.get("root")}, ensure_ascii=False
    )
    for skey, sess in list(manager._sessions.items()):
        if skey == key or skey.startswith(key + "::"):
            for q in list(sess.listeners):
                try:
                    q.put_nowait(event)
                except Exception:
                    pass


def apply_map(manager: ChatSessionManager, key: str, tree: dict, branch_uid: str = "") -> str | None:
    """AI pushes a full new root tree. Returns validation error or None.

    硬约束：
    - 信封（theme/layout/view 等）永远以磁盘文件为准，不信内存、不信 AI
    - 已有节点（按 uid）只接受 AI 对 text 的修改，其余字段一律保留原值
    - 新节点（无 uid）自动补 uid/richText/expand/isActive 默认值
    - 写盘前自动备份 .aibak
    - 多 agent：只有 root agent（branch_uid 空）允许整树替换；分支 agent
      整树提交会覆盖其他分支的结构，直接拒绝
    - 同一脑图的写操作串行化（per-map 锁），防并发写互相覆盖
    """
    if branch_uid:
        return "分支 agent 不允许 replace_mindmap（整树替换会覆盖其他分支）；请用 update_mindmap 增量修改自己分支内的节点"
    err = _validate_tree(tree)
    if err:
        return err
    lock = manager._write_locks.setdefault(key, threading.Lock())
    with lock:
        doc, file_md, old_by_uid, _ = _load_doc_for_write(manager, key)
        merged_root = _merge_ai_tree(tree, old_by_uid)
        new_data = dict(file_md)
        new_data["root"] = merged_root
        _commit_map(manager, key, doc, new_data)
    return None


def _merge_ai_tree(node: dict, old_by_uid: dict) -> dict:
    """合并 AI 提交的新树：已有节点只接受 text 变更，新节点补默认字段。"""
    data = node.get("data", {}) if isinstance(node.get("data"), dict) else {}
    uid = data.get("uid")
    if uid and uid in old_by_uid:
        # 已有节点：以文件原值为准，AI 只能改 text
        merged = dict(old_by_uid[uid].get("data", {}))
        if "text" in data:
            merged["text"] = data["text"]
        data = merged
    else:
        data = dict(data)
        data.setdefault("uid", str(uuid.uuid4()))
        data.setdefault("richText", True)
        data.setdefault("expand", True)
        data.setdefault("isActive", False)
        t = data.get("text", "")
        if data.get("richText") and isinstance(t, str) and t and "<" not in t:
            data["text"] = f"<p>{t}</p>"
    return {
        "data": data,
        "children": [_merge_ai_tree(c, old_by_uid) for c in node.get("children", [])],
    }


def _new_node(text: str) -> dict:
    return {
        "data": {
            "text": f"<p>{text}</p>",
            "uid": str(uuid.uuid4()),
            "richText": True,
            "expand": True,
            "isActive": False,
        },
        "children": [],
    }


def _node_from_spec(spec: dict) -> dict:
    """从 op 描述递归构建子树（text + children）。"""
    node = _new_node(spec.get("text", ""))
    node["children"] = [_node_from_spec(c) for c in spec.get("children", []) or []]
    return node


def _contains_uid(node: dict, uid: str) -> bool:
    if node.get("data", {}).get("uid") == uid:
        return True
    return any(_contains_uid(c, uid) for c in node.get("children", []) or [])


def _register_subtree(node: dict, index: dict) -> None:
    uid = node.get("data", {}).get("uid")
    if uid:
        index[uid] = node
    for c in node.get("children", []) or []:
        _register_subtree(c, index)


def _unregister_subtree(node: dict, index: dict) -> None:
    """把整棵子树从 uid 索引中移除（删除节点时用，防止同批次后续 op
    命中已脱离树的"幽灵节点"——修改假成功、实际不生效）。"""
    uid = node.get("data", {}).get("uid")
    if uid:
        index.pop(uid, None)
    for c in node.get("children", []) or []:
        _unregister_subtree(c, index)


def _collect_branch_uids(root: dict, branch_uid: str) -> set[str] | None:
    """返回分支根节点及其子孙的所有 uid 集合；branch_uid 空（root agent）返回 None（不限制）。"""
    if not branch_uid:
        return None
    idx = _index_by_uid(root)
    branch_node = idx.get(branch_uid)
    if branch_node is None:
        return set()  # 分支根不存在——拒绝一切写
    return set(_index_by_uid(branch_node).keys())


def apply_ops(manager: ChatSessionManager, key: str, ops: list, branch_uid: str = "") -> dict:
    """增量改图：小改动的首选路径，AI 不用生成整棵树。

    支持的 op：
    - {"action":"update_text","uid","text"}        改节点文本（其余字段保留）
    - {"action":"add","parent_uid"|"parent_ref","text","children"?,"index"?,"ref"?}
        新增子节点/子树；ref 给新节点起临时名，同批次后续 op 可引用
    - {"action":"delete","uid"}                    删除节点（根节点除外）
    - {"action":"move","uid","new_parent_uid"|"new_parent_ref","index"?}
        移动节点（保留子树和全部字段；禁止移到自己/自己的子树下）
    返回 {"applied": n, "errors": [...], "created": {ref: uid}}。

    多 agent 约束：
    - 分支 agent（branch_uid 非空）：所有 op 的目标 uid 必须在分支内，
      越界 op 报错拒绝；写操作串行化（per-map 锁）防并发覆盖
    - root agent（branch_uid 空）：不限分支，但仍受写锁保护
    """
    if not isinstance(ops, list) or not ops:
        return {"applied": 0, "errors": ["ops 必须是非空数组"]}
    lock = manager._write_locks.setdefault(key, threading.Lock())
    with lock:
        return _apply_ops_locked(manager, key, ops, branch_uid)


def _apply_ops_locked(manager: ChatSessionManager, key: str, ops: list, branch_uid: str) -> dict:
    doc, file_md, old_by_uid, _ = _load_doc_for_write(manager, key)
    root = file_md.get("root", file_md)
    if not root or not isinstance(root, dict) or "data" not in root:
        return {"applied": 0, "errors": ["脑图为空或不存在"]}
    allowed = _collect_branch_uids(root, branch_uid)
    applied, errors, created, refs = 0, [], {}, {}
    root_uid = root.get("data", {}).get("uid")

    def insert_child(parent: dict, node: dict, idx) -> None:
        children = parent.setdefault("children", [])
        if isinstance(idx, int) and 0 <= idx <= len(children):
            children.insert(idx, node)
        else:
            children.append(node)

    def check_allowed(uid: str, op_idx: int, what: str) -> bool:
        """分支越界校验：allowed 为 None（root）不限制。"""
        if allowed is None:
            return True
        if uid in allowed:
            return True
        errors.append(f"op[{op_idx}]: {what} uid {uid} 不在你的分支内（分支根 {branch_uid}）；其他分支由其他 agent 负责")
        return False

    for i, op in enumerate(ops):
        action = op.get("action")
        uid = op.get("uid", "")
        if action == "update_text":
            if not check_allowed(uid, i, "目标节点"):
                continue
            node = old_by_uid.get(uid)
            if not node:
                errors.append(f"op[{i}]: uid {uid} 不存在")
                continue
            t = op.get("text", "")
            if isinstance(t, str) and t and "<" not in t and node["data"].get("richText"):
                t = f"<p>{t}</p>"
            node["data"]["text"] = t
            applied += 1
        elif action == "add":
            parent_uid = op.get("parent_uid", "")
            parent = old_by_uid.get(parent_uid)
            if parent is None and op.get("parent_ref"):
                parent = old_by_uid.get(refs.get(op["parent_ref"], ""))
            if not parent:
                errors.append(f"op[{i}]: 父节点不存在（parent_uid/parent_ref）")
                continue
            # add 的父节点必须在分支内；新增子节点天然挂在分支内
            if not check_allowed(parent.get("data", {}).get("uid", ""), i, "父节点"):
                continue
            node = _node_from_spec(op)
            insert_child(parent, node, op.get("index"))
            _register_subtree(node, old_by_uid)
            if op.get("ref"):
                refs[op["ref"]] = node["data"]["uid"]
                created[op["ref"]] = node["data"]["uid"]
            applied += 1
        elif action == "delete":
            if uid == root_uid:
                errors.append(f"op[{i}]: 不允许删除根节点")
                continue
            if not check_allowed(uid, i, "目标节点"):
                continue
            node = old_by_uid.get(uid)
            if not node or not _delete_by_uid(root, uid):
                errors.append(f"op[{i}]: uid {uid} 不存在")
                continue
            _unregister_subtree(node, old_by_uid)
            applied += 1
        elif action == "move":
            if not check_allowed(uid, i, "目标节点"):
                continue
            node = old_by_uid.get(uid)
            if not node:
                errors.append(f"op[{i}]: uid {uid} 不存在")
                continue
            if uid == root_uid:
                errors.append(f"op[{i}]: 不允许移动根节点")
                continue
            new_parent = old_by_uid.get(op.get("new_parent_uid", ""))
            if new_parent is None and op.get("new_parent_ref"):
                new_parent = old_by_uid.get(refs.get(op["new_parent_ref"], ""))
            if not new_parent:
                errors.append(f"op[{i}]: 目标父节点不存在")
                continue
            np_uid = new_parent.get("data", {}).get("uid", "")
            if not check_allowed(np_uid, i, "目标父节点"):
                continue
            if np_uid == uid or _contains_uid(node, np_uid):
                errors.append(f"op[{i}]: 不能移动到自己或自己的子树下")
                continue
            _delete_by_uid(root, uid)
            insert_child(new_parent, node, op.get("index"))
            applied += 1
        else:
            errors.append(f"op[{i}]: 未知 action {action!r}")
    if applied:
        new_data = dict(file_md)
        new_data["root"] = root
        _commit_map(manager, key, doc, new_data, branch_uid)
    return {"applied": applied, "errors": errors, "created": created}


def _delete_by_uid(node: dict, uid: str) -> bool:
    children = node.get("children", [])
    for i, c in enumerate(children):
        if c.get("data", {}).get("uid") == uid:
            children.pop(i)
            return True
        if _delete_by_uid(c, uid):
            return True
    return False
