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
from copy import deepcopy
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


def _display_label(label: str, max_len: int = 20) -> str:
    """给用户 UI 看的分支名：前 max_len 个字符，超出加省略号。

    与 _branch_label（给 AI system prompt 的完整名）分开：AI 需要全名
    理解上下文，用户界面显示适度短标签。默认 20 字（2026-08-05 从 5 提上来：
    5 字太短，"ART Hook" 都被砍成 "ART H…"；超长标题由前端 CSS
    max-width 140px + ellipsis 兜底截断，双保险）。
    """
    if not label:
        return label
    if len(label) <= max_len:
        return label
    return label[:max_len] + "…"


def _branch_label(map_key: str, branch_uid: str, state: dict | None = None, lang: str = "zh") -> str:
    """取分支根节点的文本标签，用于注入 system prompt 分支职责说明。

    磁盘优先（用户手动编辑/AI 改图最终都落盘，磁盘是权威；_map_state
    快照只在 sync/apply 时更新，会过期），state 兜底（覆盖磁盘上还不存在
    的新节点——用户在 UI 新建未保存）。两处都找不到 = 分支节点已被删除，
    返回「已删除」标记（按 lang 中英文），不再显示 uid 本身；
    节点存在但文本为空时仍退回 uid（罕见，保留可追踪性）。
    """
    found = False
    try:
        # 磁盘优先
        fpath = Path(PROJECT_CWD) / map_key
        if map_key.endswith(".smm.json") and fpath.is_file():
            doc = json.loads(fpath.read_text())
            md = doc.get("mindMapData") or {}
            root = _state_root(md)
            if isinstance(root, dict):
                idx = _index_by_uid(root)
                node = idx.get(branch_uid)
                if node and isinstance(node.get("data"), dict):
                    found = True
                    t = _strip_html(node["data"].get("text", ""))
                    if t:
                        return t[:40]
        # state 兜底
        if state:
            root = _state_root(state)
            if isinstance(root, dict):
                idx = _index_by_uid(root)
                node = idx.get(branch_uid)
                if node and isinstance(node.get("data"), dict):
                    found = True
                    t = _strip_html(node["data"].get("text", ""))
                    if t:
                        return t[:40]
    except Exception:
        pass
    if not found:
        return "Deleted" if (lang or "").startswith("en") else "已删除"
    return branch_uid


def _branch_deleted(map_key: str, branch_uid: str, state: dict | None = None) -> bool:
    """分支节点是否已被删除（磁盘 + _map_state 都找不到）。

    与 _branch_label 的查找范围一致，但返回布尔标记而不是本地化文本——
    UI 文案由前端 I18N 字典翻译（项目习惯），后端只负责给标记。
    """
    try:
        # 磁盘优先
        fpath = Path(PROJECT_CWD) / map_key
        if map_key.endswith(".smm.json") and fpath.is_file():
            doc = json.loads(fpath.read_text())
            md = doc.get("mindMapData") or {}
            root = _state_root(md)
            if isinstance(root, dict) and branch_uid in _index_by_uid(root):
                return False
        # state 兜底
        if state:
            root = _state_root(state)
            if isinstance(root, dict) and branch_uid in _index_by_uid(root):
                return False
    except Exception:
        pass
    return True


# provider id → 环境变量名（也用作 private/keys.json 的键名）
# 包含 pi agent 支持的所有 API key 类型的 provider
PROVIDER_ENV = {
    "deepseek": "DEEPSEEK_API_KEY",
    "moonshotai-cn": "MOONSHOT_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "xai": "XAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "together": "TOGETHER_API_KEY",
    "kimi-coding": "KIMI_API_KEY",
}


def _load_provider_keys() -> dict:
    """Read provider API keys. Never logged.

    读取优先级（从高到低）——本地私人工具，前端明确设置的 key 最优先：
    1. private/keys.json（前端「模型设置」写入，本机私有、git ignore）
    2. 环境变量（部署/进程级配置）
    """
    keys = {env_var: "" for env_var in PROVIDER_ENV.values()}
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
    """Filesystem-safe slug for a map key (session file naming).

    ⚠️ 必须保留中文等 Unicode 字符！旧实现 `re.sub(r"[^A-Za-z0-9_.-]+", "_", ...)`
    把所有中文替换成下划线，导致「comind正式版管理.smm.json」和「comind项目.smm.json」
    slug 碰撞成同一个 `comind_.smm.json` → list_sessions 的 glob 把不同脑图的
    session 文件混在一起（跨脑图串台 bug，2026-08-08 修复）。
    现在只替换路径分隔符、控制字符和 Windows 非法字符，中文原样保留。
    """
    return re.sub(r'[/\\:*?"<>|\x00-\x1f]+', "_", map_key)


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
        # 最近一次 mindmap_update 事件（AI 改图广播）。与 _buffer 不同：
        # _buffer 只缓存 pi 回合事件，mindmap_update 不进 _buffer；SSE 重连时
        # 若只重放 _buffer，重连窗口期的改图广播会永久丢失（前端画布不更新，
        # 直到手动刷新）。subscribe(replay=True) 时重放它兜底。
        self.last_map_event: str | None = None
        self.last_active = time.time()
        self._reader_thread: threading.Thread | None = None
        self._alive = False
        self._in_turn = False  # True between agent_start and agent_end
        self._abort_requested = False  # True after abort() until agent_end/kill
        self._abort_gen = 0  # incremented on each abort; _force_kill only acts if unchanged

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
            branch_label = _branch_label(self.map_key, self.branch_uid, state, self.lang)
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
            "--thinking", "max",
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
                        self._abort_requested = False
                        self._abort_gen += 1  # invalidate any pending _force_kill timer
                        sf = ev.get("sessionFile") or ev.get("session_file")
                        if sf:
                            self.session_file = sf
                    # abort 后吞掉内容事件（thinking/text delta 等），
                    # 只放行 agent_end/agent_settled 让前端知道回合结束
                    if self._abort_requested and ev_type not in (
                        "agent_end", "agent_settled", "agent_start",
                    ):
                        continue
                    self._buffer.append(line)
                    # Keep session alive while pi is producing output
                    self.last_active = time.time()
                    if ev_type in ("agent_end", "agent_settled"):
                        self._in_turn = False
                        self._abort_requested = False
                        self._buffer.clear()
                except json.JSONDecodeError:
                    pass
                # Broadcast to all listeners (drop on full, never remove —
                # real disconnect cleanup is in events() finally → unsubscribe)
                for q in list(self.listeners):
                    try:
                        q.put_nowait(line)
                    except Exception:
                        pass
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
            # 重放最近一次 AI 改图广播（重连窗口期的 mindmap_update 兜底，
            # 否则前端画布停在旧状态直到手动刷新才看到 AI 改动）
            if self.last_map_event:
                try:
                    q.put_nowait(self.last_map_event)
                except Exception:
                    pass
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
        # per-map 写版本：AI 每次写盘 +1。前端画布版本与此对齐，
        # 保存时版本一致 = 前端看到全部 → 直接覆盖；版本落后 = 前端未同步 AI 改动 → merge
        self._map_ver: dict[str, int] = {}
        # 人类编辑锁：key → {uid, ts}。前端双击编辑节点时上报，60 秒超时自动释放。
        # apply_ops 遇到被锁 uid 时跳过该 op（不整批失败），反馈给 AI 稍后重试。
        self._human_editing: dict[str, dict] = {}
        # Mind-map state: key → current mindMapData / AI-synced snapshot
        self._map_state: dict[str, dict] = {}
        self._map_snapshot: dict[str, dict] = {}
        # 轮次回滚：skey → {user_msg, user_msg_idx, ts}（该轮的元数据）
        self._turn_before: dict[str, dict] = {}
        # 轮次 ops 收集器：skey → list[diff_entry]。mutation 现场直接记录，
        # 天然按 session 隔离，不需要事后快照对比推断 AI 做了什么。
        self._turn_ops: dict[str, list[dict]] = {}
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
        quoted_nodes = []
        if context:
            qn = context.get("quoted_nodes") or []
            if not qn and context.get("quoted_node"):
                qn = [context["quoted_node"]]
            quoted_nodes = [q for q in qn if isinstance(q, dict)]
        if quoted_nodes:
            first = quoted_nodes[0]
            uid = first.get("uid", "")
            text = first.get("text", "")
            prefix = f"[NODE_ASSIST uid={uid}]" if uid else "[NODE_ASSIST]"
            parts.append(f"{prefix} 用户在节点「{text}」上求助")
            note = first.get("note", "")
            if note:
                parts.append(f"[该节点的备注内容：{note}]")
            if len(quoted_nodes) > 1:
                parts.append("引用节点（消息中的 [引用N] 占位符指代这里的节点）：")
                for i, q in enumerate(quoted_nodes, 1):
                    qtext = q.get("text", "")
                    quid = q.get("uid", "")
                    qnote = q.get("note", "")
                    line = f"[引用{i}] uid={quid} 「{qtext}」"
                    if qnote:
                        line += f"（备注：{qnote}）"
                    parts.append(line)
        parts.append(message)
        full_msg = "\n".join(parts)
        self.record_turn_start(map_key, branch_uid, message)
        sess.send({"type": "prompt", "message": full_msg})

    def abort(self, map_key: str, branch_uid: str = "") -> bool:
        sess = self._sessions.get(session_key(map_key, branch_uid))
        if sess and sess.alive:
            # 立即标记 abort —— _read_stdout 会吞掉后续内容事件，前端不再收到残留流
            sess._abort_requested = True
            sess._abort_gen += 1
            gen = sess._abort_gen  # capture for closure
            sess._buffer.clear()
            sess.send({"type": "abort"})
            # 秒停：pi 正常响应 abort 很快；短窗口（1.5s）后仍未结束则强制
            # 广播 agent_end + kill，保证前端不再收到残留流内容
            def _force_kill():
                time.sleep(1.5)
                # Only act if THIS abort is still the active one.
                # If a new prompt arrived (agent_start resets _abort_requested)
                # or another abort was issued, gen will have changed — bail out.
                if sess._abort_gen != gen:
                    return
                if sess.alive and (sess._in_turn or sess._abort_requested):
                    logger.warning("abort timeout for %s, force-killing pi", map_key)
                    sess._in_turn = False
                    sess._abort_requested = False
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
        file is KEPT on disk so it stays available in the history list.

        新分支（mapping 无条目）：创建空占位条目，让 agents 列表能列出它；
        已存在分支：杀掉 pi、删条目，下次访问 spawn 新 session 文件。
        """
        skey = session_key(map_key, branch_uid)
        with self._lock:
            sess = self._sessions.pop(skey, None)
            if sess:
                sess.kill()
            self._mapping.pop(skey, None)
            if branch_uid:
                # 占位：分支 agent 已创建但尚无会话文件（label 从 _map_state 取）
                self._mapping[skey] = ""
            self._save_mapping()

    def events(self, map_key: str, branch_uid: str = "") -> Iterator[str]:
        sess = self.get_or_spawn(map_key, branch_uid)
        q = sess.subscribe(replay=True)
        try:
            while True:
                try:
                    line = q.get(timeout=10)
                except Empty:
                    yield "event: ping\ndata: {}\n\n"
                    continue
                if line is None:
                    break
                # Forward as SSE
                try:
                    ev = json.loads(line)
                    ev_type = ev.get("type", "unknown")
                    # agent_end 先落盘轮次记录再广播：前端收到事件立即查 turns 时数据已就绪
                    if ev_type == "agent_end":
                        self.finalize_turn(map_key, branch_uid)
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
                # 分支 session 文件名含 __<branch_slug>__，root 不含（去掉 map 前缀后判断）
                stem = f.name[len(prefix):]
                is_branch = "__" in stem
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

    def _last_message_ts(self, session_file: str) -> float:
        """session 最后一条消息的时间戳（秒）。

        不依赖文件 mtime——选中/读取 session 不会改文件，但 pi 可能
        在加载时写 session 元数据导致 mtime 变化。真正的"最后对话时间"
        应来自 jsonl 里最后一条 message 记录的 timestamp。
        """
        try:
            last = 0.0
            for line in Path(session_file).read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("type") != "message":
                    continue
                ts = e.get("timestamp")
                if isinstance(ts, str):
                    try:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        v = dt.timestamp()
                        if v > last:
                            last = v
                    except Exception:
                        pass
            return last
        except Exception:
            return 0.0

    def _session_streaming(self, map_key: str, session_file: str, branch_uid: str = "") -> bool:
        """该 session 是否正在流式输出（分支进程存在、进行中、且活跃文件正是它）。

        用于历史列表显示进行中状态：只有\"当前活跃文件 + 进程 _in_turn\"才算，
        旧历史文件或空闲进程一律 False。活跃文件以 mapping 为准（磁盘权威）——
        sess.session_file 可能被 pi 返回的 sessionFile 覆盖且无 SSE 连接时未同步回 mapping。
        """
        skey = session_key(map_key, branch_uid)
        sess = self._sessions.get(skey)
        if sess is None or not sess._in_turn:
            return False
        active = self._mapping.get(skey) or str(sess.session_file or "")
        return str(active) == str(session_file)

    def all_sessions(self, map_key: str) -> list[dict]:
        """该脑图全部 session（root + 各分支）按最后对话时间倒序混排，每条带分支标签。

        前端历史记录不再按 agent/分支分组：人的思维链是顺序的，多个分支
        session 协作解决同一件事，按时间线看最符合直觉。分支降级为每行的
        标签（branch_uid + display_label），点击即切换（自动带上分支约束）。
        """
        prefix = safe_key_slug(map_key) + "__"
        sessions = []
        root = _state_root(self._map_state.get(map_key))
        uid_index = _index_by_uid(root) if root else {}
        parents = _index_with_parent(root) if root else {}
        for f in SESSION_DIR.glob(f"{prefix}*.jsonl"):
            try:
                stem = f.name[len(prefix):]
                # 分支 session 文件名 <map>__<branch_slug>__<ts>.jsonl：去掉 map 前缀后
                # 含 "__"（slug__ts）；root 是 <map>__<ts>.jsonl：不含
                is_branch = "__" in stem
                branch_uid = ""
                if is_branch:
                    # 文件名里 slug 不是完整 uid，用 mapping 反查该文件属于哪个分支
                    branch_uid = self._branch_uid_for_file(map_key, str(f))
                text = f.read_text()
                user_count = text.count('"role": "user"') + text.count('"role":"user"')
                # 跟随对焦目标：从最新一轮往回找第一个有实际改动（且节点仍在
                # 树里）的轮次，取其改动批次的根节点 uid；全部没有则空（前端
                # 退回 session 绑定分支）。数据源 = turns（持久化，重启不丢）
                focus_uid = ""
                focus_uids: list[str] = []
                for turn in reversed(_load_turns(str(f))):
                    diff = turn.get("diff") or []
                    uids = [d.get("uid") for d in diff
                            if d.get("uid") and d.get("uid") in uid_index]
                    if branch_uid:
                        # 分支 session 只对焦本分支内的改动：防御修复前旧 turns 数据
                        # （并发时可能混入其他分支节点）+ 并发窗口兜底
                        uids = [u for u in uids if _belongs_to_branch(u, branch_uid, parents)]
                    if not uids:
                        continue
                    focus_uids = uids
                    focus_uid = _lca_uid(uids, root)
                    if focus_uid:
                        break
                branch_lang = self._lang_pref.get(session_key(map_key, branch_uid), "zh")
                sessions.append({
                    "file": str(f),
                    "name": f.stem,
                    "modified": self._last_message_ts(str(f)),
                    "size": f.stat().st_size,
                    "user_messages": user_count,
                    "streaming": self._session_streaming(map_key, str(f), branch_uid),
                    "branch_uid": branch_uid,
                    "focus_uid": focus_uid,
                    "focus_uids": focus_uids,
                    "deleted": _branch_deleted(map_key, branch_uid, self._map_state.get(map_key)) if branch_uid else False,
                    "branch_label": _branch_label(map_key, branch_uid, self._map_state.get(map_key), branch_lang) if branch_uid else "",
                    "display_label": _display_label(
                        _branch_label(map_key, branch_uid, self._map_state.get(map_key), branch_lang)
                    ) if branch_uid else "",
                })
            except Exception:
                continue
        # 按最后对话时间倒序（不是文件 mtime / 创建时间）
        sessions.sort(key=lambda s: s["modified"], reverse=True)
        return sessions

    def _branch_uid_for_file(self, map_key: str, session_file: str) -> str:
        """从 session 文件反查它属于哪个分支（找不到返回空 = root）。

        两条路径：
        1. mapping 反查：当前活跃的 session 文件能直接命中
           （map_key::branch_uid → session_file）。
        2. 文件名 slug 匹配：旧 session（已被 reset 替换、不在 mapping 中）
           从文件名提取分支 slug（branch_uid 前 8 位），在脑图节点 uid 里
           前缀匹配——保证同一分支的历史 session 仍显示正确的分支标签。
        """
        for skey, sf in self._mapping.items():
            mkey, b = split_session_key(skey)
            if mkey == map_key and b and sf == session_file:
                return b
        # mapping 反查不到（旧 session）：文件名 <map>__<branch_slug8>__<ts>.jsonl
        fname = Path(session_file).name
        prefix = safe_key_slug(map_key) + "__"
        if fname.startswith(prefix):
            stem = fname[len(prefix):]
            if "__" in stem:
                slug = stem.split("__", 1)[0]
                if len(slug) >= 4:  # 真实 uid slug 8 位；测试用短 uid 也支持
                    return self._uid_by_slug_prefix(map_key, slug)
        return ""

    def _uid_by_slug_prefix(self, map_key: str, slug: str) -> str:
        """在脑图节点中找 uid 前几位匹配 slug 的节点（取第一个）。

        _map_state 为空（服务重启未 sync）时从磁盘脑图文件兜底。
        """
        state = self._map_state.get(map_key)
        if not state:
            fpath = Path(PROJECT_CWD) / map_key
            if map_key.endswith(".smm.json") and fpath.is_file():
                try:
                    doc = json.loads(fpath.read_text())
                    state = doc.get("mindMapData") or {}
                except Exception:
                    state = None
        if not state:
            return ""
        root = _state_root(state)
        if not isinstance(root, dict):
            return ""
        for uid in _index_by_uid(root):
            if uid.startswith(slug):
                return uid
        return ""

    def _session_has_history(self, session_file: str | None) -> bool:
        """该 agent 是否实际交流过：活跃 session 文件存在且含用户消息。

        「+」语义决策依据：有历史 = 新开一轮（reset）；无历史 = 幂等切换。
        """
        if not session_file or not Path(session_file).is_file():
            return False
        try:
            text = Path(session_file).read_text()
            return '"role": "user"' in text or '"role":"user"' in text
        except Exception:
            return False

    def list_agents(self, map_key: str) -> list[dict]:
        """列出该脑图的所有 agent（root + 各分支），供前端多 agent 列表显示。

        数据来源：_map_state 里的分支节点 + mapping 里已存在的会话键。
        返回每个 agent 的 branch_uid（"" = root）、分支名、活跃会话文件、
        是否正在流式输出、是否实际交流过（has_history）。
        """
        agents: dict[str, dict] = {}
        # root agent
        root_sf = self._mapping.get(map_key)
        agents[""] = {
            "branch_uid": "",
            "deleted": False,
            "label": "",
            "display_label": "",
            "session_file": root_sf,
            "has_history": self._session_has_history(root_sf),
            "streaming": self._sessions.get(map_key, None) is not None
            and self._sessions[map_key]._in_turn,
        }
        # 各分支 agent（来自 mapping 键 map_key::branch_uid）
        for skey, sf in self._mapping.items():
            mkey, b = split_session_key(skey)
            if mkey != map_key or not b:
                continue
            sess = self._sessions.get(skey)
            full = _branch_label(map_key, b, self._map_state.get(map_key), self._lang_pref.get(skey, "zh"))
            agents[b] = {
                "branch_uid": b,
                "deleted": _branch_deleted(map_key, b, self._map_state.get(map_key)),
                "label": full,
                "display_label": _display_label(full),
                "session_file": sf,
                "has_history": self._session_has_history(sf),
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
            # 幂等：目标就是当前活跃会话且进程还活着 → 不 kill（保留进行中回合，
            # 前端 SSE replay 自然接上进度，避免"只是想查看却中断了 agent 工作"）
            if str(self._mapping.get(skey, "")) == session_file:
                sess = self._sessions.get(skey)
                if sess is not None and sess.alive:
                    return True
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
        """Available models + current model + thinking level for this map's session."""
        sess = self.get_or_spawn(map_key)
        ev = self._rpc_request(sess, {"type": "get_available_models"})
        models = []
        if ev and ev.get("success"):
            models = (ev.get("data") or {}).get("models", [])
        st = self._rpc_request(sess, {"type": "get_state"})
        current = None
        thinking_level = "max"
        if st and st.get("success"):
            current = (st.get("data") or {}).get("model")
            thinking_level = (st.get("data") or {}).get("thinkingLevel", "max")
        return {"models": models, "current": current, "thinkingLevel": thinking_level}

    def set_model(self, map_key: str, provider: str, model_id: str) -> bool:
        sess = self.get_or_spawn(map_key)
        ev = self._rpc_request(sess, {
            "type": "set_model", "provider": provider, "modelId": model_id,
        })
        ok = bool(ev and ev.get("success"))
        if ok:
            self._model_pref[map_key] = {"provider": provider, "modelId": model_id}
            self._save_json(MODELS_PATH, self._model_pref)
        return ok

    def get_thinking_levels(self, map_key: str) -> dict:
        """Get available thinking levels for the current model."""
        sess = self.get_or_spawn(map_key)
        ev = self._rpc_request(sess, {"type": "get_available_thinking_levels"})
        levels = []
        if ev and ev.get("success"):
            levels = (ev.get("data") or {}).get("levels", [])
        return {"levels": levels}

    def set_thinking_level(self, map_key: str, level: str) -> bool:
        sess = self.get_or_spawn(map_key)
        ev = self._rpc_request(sess, {"type": "set_thinking_level", "level": level})
        return bool(ev and ev.get("success"))

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

    # ─── 轮次回滚（按对话轮次线性回滚：脑图 + pi session 一起回到那一刻）───

    def record_turn_start(self, map_key: str, branch_uid: str, message: str) -> None:
        """prompt 时记录轮次起点：用户消息 + jsonl 中 user 消息序号 + 初始化 ops 收集器。

        diff 不再用快照对比推断——改为 mutation 现场直接记录（_turn_ops），
        天然按 session 隔离，不会混入用户手动编辑或其他分支的改动。
        """
        skey = session_key(map_key, branch_uid)
        sess = self._sessions.get(skey)
        if not sess or not sess.session_file:
            return
        self._turn_before[skey] = {
            "user_msg": message or "",
            "ts": time.time(),
            "user_msg_idx": _user_message_count(Path(sess.session_file)) + 1,
        }
        self._turn_ops[skey] = []

    def finalize_turn(self, map_key: str, branch_uid: str) -> None:
        """agent_end 时把本轮 diff 追加进轮次记录（脑图回滚的数据源）。

        diff 来自 _turn_ops（mutation 现场记录），不再事后快照对比。
        """
        skey = session_key(map_key, branch_uid)
        rec = self._turn_before.pop(skey, None)
        diff = self._turn_ops.pop(skey, [])
        sess = self._sessions.get(skey)
        if not rec or not sess or not sess.session_file:
            return
        sf = Path(sess.session_file)
        if not sf.is_file():
            return
        turns = _load_turns(sess.session_file)
        turns.append({
            "turn_id": uuid.uuid4().hex,
            "user_msg": rec.get("user_msg", ""),
            "ts": rec.get("ts", time.time()),
            "user_msg_idx": rec.get("user_msg_idx", 0),
            "diff": diff,
        })
        _save_turns(sess.session_file, turns)

    def list_turns(self, map_key: str, branch_uid: str = "") -> list[dict]:
        """当前 session 的轮次列表（前端回滚列表用）。

        轮次清单权威来源 = jsonl 的 user 消息（所有 session 天然有，含旧会话）；
        turns.json 的 diff 按 user_msg_idx 附加（有 diff 的轮次脑图可精确回滚）。
        """
        skey = session_key(map_key, branch_uid)
        sess = self._sessions.get(skey)
        session_file = sess.session_file if sess and sess.session_file else self._mapping.get(skey)
        if not session_file or not Path(session_file).is_file():
            return []
        turns = _load_turns(session_file)
        diff_by_idx = {t.get("user_msg_idx"): (t.get("diff") or []) for t in turns}
        msg_by_idx = {t.get("user_msg_idx"): t.get("user_msg", "") for t in turns}
        out = []
        for um in _user_messages_from_jsonl(Path(session_file)):
            diff = diff_by_idx.get(um["user_msg_idx"], [])
            summary = {"add": 0, "delete": 0, "update_text": 0, "move": 0}
            for x in diff:
                a = x.get("action", "")
                summary[a] = summary.get(a, 0) + 1
            out.append({
                "user_msg_idx": um["user_msg_idx"],
                "user_msg": msg_by_idx.get(um["user_msg_idx"], um["user_msg"]),
                "quoted_list": um.get("quoted_list") or [],
                "quoted": um.get("quoted"),
                "ts": um["ts"],
                "diff_summary": summary,
                "has_diff": bool(diff),
            })
        return out

    def rollback(self, map_key: str, branch_uid: str, user_msg_idx: int) -> dict:
        """回滚到指定轮次（用户发那句话之前）：
        kill pi → 截断 session jsonl → 清轮次记录 → 脑图反向 diff → 保持 mapping。

        轮次 = jsonl 里的 user 消息序号（权威）；diff 可选——有 diff 的轮次
        脑图精确回滚，无 diff（旧会话/未记录）只回滚对话并提示。

        返回 {"ok", "skipped", "user_msg", "map_restored"}。
        """
        skey = session_key(map_key, branch_uid)
        sess = self._sessions.get(skey)
        session_file = sess.session_file if sess and sess.session_file else self._mapping.get(skey)
        if not session_file or not Path(session_file).is_file():
            return {"ok": False, "error": "session 不存在"}
        before_state = self._map_state.get(map_key)
        turns = _load_turns(session_file)
        # 1. 直接 kill pi（回滚语义 = 回到那轮之前，正在进行的回复本就该丢弃）
        if sess and sess.alive:
            sess.kill()
        # 注意：先不 pop _sessions——_apply_reverse → _commit_map 要广播
        # mindmap_update 给本 session 的 SSE 订阅者，pop 早了前端画布收不到
        # （toast 成功但界面不刷新）。改图广播完成后才移出活跃池。
        # 2. 截断 jsonl 到目标轮 user 消息之前（保留 header + 之前所有行）
        lines = Path(session_file).read_text().splitlines()
        cut = None
        user_count = 0
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("type") == "message" and (e.get("message") or {}).get("role") == "user":
                user_count += 1
                if user_count == user_msg_idx:
                    cut = i
                    break
        if cut is None:
            return {"ok": False, "error": "找不到目标轮次消息（session 文件已变化）"}
        target_msg = ""
        target_quoted_list = []
        ums = _user_messages_from_jsonl(Path(session_file))
        for um in ums:
            if um["user_msg_idx"] == user_msg_idx:
                target_msg = um["user_msg"]
                target_quoted_list = um.get("quoted_list") or []
                break
        keep = lines[:cut]
        _atomic_write(Path(session_file), "\n".join(keep) + ("\n" if keep else ""))
        # 3. 收集目标轮及之后的脑图 diff（该轮用户消息之后的 AI 改动都要撤销），
        #    清掉这些轮次记录（目标轮消息已被截断回输入框，不再存在）
        later_diffs = [d for t in turns if (t.get("user_msg_idx") or 0) >= user_msg_idx
                       for d in (t.get("diff") or [])]
        # 分支隔离：旧 turns 数据可能混入其他分支的改动（并发污染 bug）——
        # 回滚只撤销本分支内的改动，避免误删/误改其他分支的节点
        if branch_uid and later_diffs:
            cur_root = _state_root(before_state) if before_state else None
            cur_parents = _index_with_parent(cur_root) if cur_root else {}
            later_diffs = _filter_diff_to_branch(later_diffs, branch_uid, cur_parents)
        turns = [t for t in turns if (t.get("user_msg_idx") or 0) < user_msg_idx]
        _save_turns(session_file, turns)
        # 4. 脑图反向应用（只撤销该 session 的 AI 改动，冲突节点跳过）
        map_restored = bool(later_diffs)
        skipped = _apply_reverse(self, map_key, later_diffs, branch_uid) if later_diffs else []
        # 4.5 广播完成（_commit_map 已发 mindmap_update 给本 session 的 SSE 订阅者），
        #     现在才把 session 移出活跃池——旧 SSE 连接由前端 connectSSE() 重连替换
        self._sessions.pop(skey, None)
        # 5. mapping 指向同一 session 文件——前端 SSE 重连时 get_or_spawn 加载截断文件
        self._mapping[skey] = session_file
        self._save_mapping()
        # 6. 检查目标轮各引用节点在回滚后的树中是否存在（供前端决定是否放回 chip）
        quoted_list_exists = []
        new_root = None
        try:
            st = self._map_state.get(map_key)
            if st:
                new_root = _state_root(st)
            if new_root is None:
                # 服务重启后未 sync 的脑图：_map_state 为空，从磁盘兜底（回滚已落盘）
                _, file_md, _, _ = _load_doc_for_write(self, map_key)
                new_root = file_md.get("root", file_md)
            uid_index = _index_by_uid(new_root) if new_root else {}
        except Exception:
            uid_index = {}
        for q in target_quoted_list:
            quoted_list_exists.append(bool(q.get("uid") and q["uid"] in uid_index))
        return {
            "ok": True,
            "skipped": skipped,
            "user_msg": target_msg,
            "quoted_list": target_quoted_list,
            "quoted": target_quoted_list[0] if target_quoted_list else None,
            "quoted_list_exists": quoted_list_exists,
            "map_restored": map_restored,
            # 回滚后的完整树：_commit_map 已更新 _map_state 为反向应用后的树。
            # 前端用它直接 setData 刷新画布——SSE 广播在 kill 后靠 EventSource
            # 自动重连不可靠（重连晚于广播，旧 queue 已 unsub），响应带树最稳。
            "tree": new_root if map_restored else None,
            # 回滚净变化（相对回滚前），前端用来展示 "+N -M" 动画
            "stats": _map_uid_stats(before_state, self._map_state.get(map_key)),
        }


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


def _atomic_write(path: Path, text: str) -> None:
    """原子写盘：先写临时文件再 rename，避免并发写盘时文件被截断损坏。

    所有写盘路径（apply_ops/apply_map 的 _commit_map、前端 /api/save）都必须走这里，
    配合 per-map 写锁（manager._write_locks）防多 session 并发互相覆盖。
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _collect_uids(root: dict, out: set) -> None:
    uid = (root.get("data") or {}).get("uid")
    if uid:
        out.add(uid)
    for c in root.get("children", []) or []:
        _collect_uids(c, out)


def _front_covers_disk(front_root: dict, disk_root: dict) -> bool:
    """前端画布是否包含磁盘所有节点（uid 集合）。是 → 前端是完整视图，
    用户删除/移动/顺序都是真实意图，保存直接覆盖（零副作用，不 merge）。"""
    f, d = set(), set()
    _collect_uids(front_root, f)
    _collect_uids(disk_root, d)
    return d <= f


def _merge_save_tree(disk_root: dict, front_root: dict) -> dict:
    """前端保存 merge（仅当前端落后于磁盘、含 AI 未同步改动时调用）。

    背景（2026-08-02 movexbot 多 session 并发）：前端 saveMindMapData 用
    画布整树覆盖写盘，如果画布没同步 AI 的改动（SSE 未连/未刷新），
    AI 刚写盘的节点会被覆盖丢失。merge 规则（用户意图优先）：
    - children 顺序以**前端**为准（用户移动/排序不丢）
    - 同 uid → 取前端 data，子节点递归 merge
    - 磁盘独有节点（AI 新增、前端未同步）→ 追加到末尾（保留 AI 改动）
    代价：用户删除的节点若磁盘仍有会复活（AI 协作场景防丢为主）。
    """
    if not isinstance(disk_root, dict) or "data" not in disk_root:
        return front_root
    if not isinstance(front_root, dict) or "data" not in front_root:
        return disk_root
    front_children = front_root.get("children", []) or []
    disk_by_uid = {}
    for c in disk_root.get("children", []) or []:
        uid = (c.get("data") or {}).get("uid")
        if uid:
            disk_by_uid[uid] = c
    merged_children = []
    for c in front_children:
        uid = (c.get("data") or {}).get("uid")
        if uid and uid in disk_by_uid:
            merged_children.append(_merge_save_tree(disk_by_uid.pop(uid), c))
        else:
            merged_children.append(c)  # 前端独有（用户新增）
    for c in disk_by_uid.values():
        merged_children.append(c)  # 磁盘独有（AI 新增）追加
    return {
        "data": front_root.get("data", disk_root.get("data", {})),
        "children": merged_children,
    }


def _lca_uid(uids: list[str], root: dict | None) -> str:
    """给定一批节点 uid，返回它们的最小公共祖先（含自身）的 uid；找不到返回空。

    用于切换 session 时对焦到『最近一次改图那批节点的根节点』：
    - 单节点 → 它自己
    - 同一分支内多个节点 → 包含它们的最小子树根
    - 节点已被删除 / 不在树里 → 跳过（全跳过则返回空）
    """
    if not uids or root is None:
        return ""
    parent: dict[str, str] = {}

    def walk(n: dict, p: str) -> None:
        uid = (n.get("data") or {}).get("uid") or ""
        if uid:
            parent[uid] = p
        for c in n.get("children") or []:
            walk(c, uid)

    walk(root, "")
    chains: list[list[str]] = []
    for u in uids:
        chain, cur = [], u
        while cur and cur in parent:
            chain.append(cur)
            cur = parent[cur]
        if chain:
            chains.append(chain)
    if not chains:
        return ""
    base = chains[0]
    others = [set(c) for c in chains[1:]]
    for n in base:  # 自身 → 根，第一个同时出现在所有链里的就是最深公共祖先
        if all(n in s for s in others):
            return n
    return ""


def _map_uid_stats(old_state, new_state) -> dict:
    """对比两次 mindMapData 状态的 uid 集合，统计 added/removed/updated 数量。

    前端 mindmap_update 用它显示 "+N -M" 动画提示；updated 按 data.text 变化粗算。
    """
    old_root = _state_root(old_state) if old_state else None
    new_root = _state_root(new_state)
    old_idx = _index_by_uid(old_root) if old_root else {}
    new_idx = _index_by_uid(new_root)
    old_uids = set(old_idx)
    new_uids = set(new_idx)
    added = len(new_uids - old_uids)
    removed = len(old_uids - new_uids)
    updated = 0
    for uid in old_uids & new_uids:
        ot = (old_idx.get(uid) or {}).get("data", {}).get("text")
        nt = (new_idx.get(uid) or {}).get("data", {}).get("text")
        if ot != nt:
            updated += 1
    return {"added": added, "removed": removed, "updated": updated}


def _broadcast_map_update(manager: ChatSessionManager, key: str, new_data: dict, old_state: dict | None, source: str = "ai", branch_uid: str = "") -> None:
    """广播 mindmap_update 给同脑图的所有 SSE 会话（root + 各分支）。

    当前只由 AI 写盘（_commit_map）调用：让并行工作的其他 agent 前端
    实时看到结构变化。带 ver 供前端对齐画布版本，带 stats 供前端播放
    修改提示，带 branch（写者分支）供前端区分「本会话自己的改图」vs
    「其他分支的改图」——气泡提示只在来源匹配当前选中会话时显示。
    ⚠️ 不要给前端人类保存广播（v0.1.25 实测：广播回自己会触发 updateData
    重建画布，折叠弹开/Tab 节点被删）。
    """
    stats = _map_uid_stats(old_state, new_data)
    event = json.dumps(
        {"type": "mindmap_update", "tree": new_data.get("root"),
         "ver": manager._map_ver[key], "stats": stats, "source": source,
         "branch": branch_uid},
        ensure_ascii=False,
    )
    for skey, sess in list(manager._sessions.items()):
        if skey == key or skey.startswith(key + "::"):
            # 缓存最近一次改图广播：SSE 重连时 subscribe(replay) 重放兜底
            sess.last_map_event = event
            for q in list(sess.listeners):
                try:
                    q.put_nowait(event)
                except Exception:
                    pass


def _ensure_map_ver_cs(manager: ChatSessionManager, key: str) -> int:
    """返回脑图当前写版本。内存中没有时从磁盘 _comind_ver 恢复（服务重启场景）。"""
    ver = manager._map_ver.get(key)
    if ver is not None:
        return ver
    fpath = Path(PROJECT_CWD) / key
    disk_ver = 0
    if fpath.is_file():
        try:
            doc = json.loads(fpath.read_text())
            disk_ver = doc.get("_comind_ver", 0)
        except Exception:
            pass
    manager._map_ver[key] = disk_ver
    return disk_ver


def _commit_map(manager: ChatSessionManager, key: str, doc: dict, new_data: dict, branch_uid: str = "") -> None:
    """提交新 mindMapData：更新内存、备份、落盘、SSE 广播。

    branch_uid 是写者（哪个 agent 提交的）。写者自己的 diff 快照前移
    （自己改的不会重复出现在自己的 diff 里）；其他 agent 的快照不动，
    这样它们下一次 get_mindmap_diff 能看到本次变化（多 agent 协作关键）。
    """
    old_state = manager._map_state.get(key)
    manager._map_state[key] = new_data
    manager._map_ver[key] = _ensure_map_ver_cs(manager, key) + 1
    writer_key = session_key(key, branch_uid)
    manager._map_snapshot[writer_key] = json.loads(json.dumps(new_data))
    # 落盘（原子写）
    fpath = Path(PROJECT_CWD) / key
    if key.endswith(".smm.json"):
        try:
            if fpath.is_file():
                backup = fpath.with_suffix(fpath.suffix + ".aibak")
                backup.write_text(fpath.read_text())
            doc["mindMapData"] = new_data
            doc["_comind_ver"] = manager._map_ver[key]
            _atomic_write(fpath, json.dumps(doc, ensure_ascii=False, indent=2))
        except Exception as exc:
            # 写盘失败绝不能静默：apply_ops 会返回成功而磁盘没写（假成功），
            # agent 以为更新了实际丢失——用 error 级别留痕
            logger.error("persist %s failed: %s", key, exc)
    # 广播 mindmap_update 给同脑图的所有 agent 会话（root + 各分支），
    # 让并行工作的其他 agent 前端实时看到结构变化；带 ver 供前端对齐画布版本，
    # 带 stats 供前端播放 "+N -M" 修改动画（不闪的增量更新提示）
    _broadcast_map_update(manager, key, new_data, old_state, source="ai", branch_uid=branch_uid)


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
        old_root = file_md.get("root", file_md)
        merged_root = _merge_ai_tree(tree, old_by_uid)
        new_data = dict(file_md)
        new_data["root"] = merged_root
        # 轮次 ops 记录：整树替换没有逐条 op，用 old/new diff 补充记录
        skey = session_key(key, branch_uid)
        turn_ops = manager._turn_ops.get(skey)
        if turn_ops is not None and old_root and merged_root:
            turn_ops.extend(_compute_net_diff(old_root, merged_root))
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


def _belongs_to_branch(uid: str, branch_uid: str, parents: dict) -> bool:
    """节点是否在以 branch_uid 为根的子树内（含分支根自身）。

    Ian 方案（2026-08-02）：不预计算白名单集合，而是沿 parent 链向上爬，
    祖先链中存在 branch_uid 即属于该分支。实时位置即真相：
    - 同批次新建节点天然满足（挂在分支内父节点下）
    - move 移入/移出分支后归属实时变化，无需维护过期集合
    """
    if not branch_uid:
        return True
    cur = uid or ""
    guard = 0
    while cur:
        if cur == branch_uid:
            return True
        cur = parents.get(cur) or ""  # 根节点的 parent 是 ""，爬到头自然终止
        guard += 1
        if guard > 100000:  # 防环保险
            return False
    return False


def _register_parents(node: dict, parent_uid: str, parents: dict) -> None:
    """把新节点子树注册进 parent 索引（add 后调用）。"""
    uid = node.get("data", {}).get("uid")
    if uid:
        parents[uid] = parent_uid
    for c in node.get("children", []) or []:
        _register_parents(c, uid, parents)


def _unregister_parents(node: dict, parents: dict) -> None:
    """把删除节点的子树从 parent 索引移除（delete 后调用）。"""
    uid = node.get("data", {}).get("uid")
    if uid:
        parents.pop(uid, None)
    for c in node.get("children", []) or []:
        _unregister_parents(c, parents)


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
    # parent 链索引：uid → parent_uid。分支归属用祖先链判断（见 _belongs_to_branch），
    # 不预计算白名单——add/move 后归属自动正确，无需手动维护集合
    parents = _index_with_parent(root)
    applied, errors, created, refs = 0, [], {}, {}
    skipped_human: list[str] = []     # 被人类编辑锁跳过的 uid
    root_uid = root.get("data", {}).get("uid")

    # 轮次 ops 收集器：mutation 现场直接记录 diff，按 session 隔离
    skey = session_key(key, branch_uid)
    turn_ops = manager._turn_ops.get(skey)  # None = 不在 turn 中，不记录

    # 人类编辑锁：检查是否有人正在编辑某节点（60 秒超时自动失效）
    human_lock = manager._human_editing.get(key)
    human_locked_uid = ""
    if human_lock and (time.time() - human_lock.get("ts", 0)) < 60:
        human_locked_uid = human_lock.get("uid", "")

    def insert_child(parent: dict, node: dict, idx) -> None:
        children = parent.setdefault("children", [])
        if isinstance(idx, int) and 0 <= idx <= len(children):
            children.insert(idx, node)
        else:
            children.append(node)

    def check_allowed(uid: str, op_idx: int, what: str) -> bool:
        """分支越界校验：branch_uid 空（root agent）不限制。"""
        if not branch_uid:
            return True
        if _belongs_to_branch(uid, branch_uid, parents):
            return True
        errors.append(f"op[{op_idx}]: {what} uid {uid} 不在你的分支内（分支根 {branch_uid}）；其他分支由其他 agent 负责")
        return False

    for i, op in enumerate(ops):
        action = op.get("action")
        uid = op.get("uid", "")
        # P3: 人类编辑锁——目标节点正被人编辑时跳过该 op（不整批失败）
        if human_locked_uid and uid and uid == human_locked_uid and action in ("update_text", "delete", "move"):
            skipped_human.append(uid)
            continue
        if action == "update_text":
            if not check_allowed(uid, i, "目标节点"):
                continue
            node = old_by_uid.get(uid)
            if not node:
                errors.append(f"op[{i}]: uid {uid} 不存在")
                continue
            old_text = node["data"].get("text", "")
            old_note = node["data"].get("note", "")
            t = op.get("text", "")
            if isinstance(t, str) and t and "<" not in t and node["data"].get("richText"):
                t = f"<p>{t}</p>"
            node["data"]["text"] = t
            applied += 1
            if turn_ops is not None:
                turn_ops.append({
                    "uid": uid, "action": "update_text",
                    "before": {"text": old_text, "note": old_note},
                    "after": {"text": t, "note": node["data"].get("note", "")},
                })
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
            # 新节点挂在分支内父节点下，天然属于本分支；注册进 parent 链，
            # 同批次后续 op 用 ref 引用它（往新节点下加子节点等）时归属判断自动通过
            actual_parent_uid = parent.get("data", {}).get("uid", "")
            _register_parents(node, actual_parent_uid, parents)
            if op.get("ref"):
                refs[op["ref"]] = node["data"]["uid"]
                created[op["ref"]] = node["data"]["uid"]
            applied += 1
            if turn_ops is not None:
                turn_ops.append({
                    "uid": node["data"]["uid"], "action": "add",
                    "before": None,
                    "after": {"node": deepcopy(node), "parent_uid": actual_parent_uid},
                })
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
            deleted_parent_uid = parents.get(uid, "")
            _unregister_subtree(node, old_by_uid)
            _unregister_parents(node, parents)
            applied += 1
            if turn_ops is not None:
                turn_ops.append({
                    "uid": uid, "action": "delete",
                    "before": {"node": deepcopy(node), "parent_uid": deleted_parent_uid},
                    "after": None,
                })
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
            old_parent_uid = parents.get(uid, "")
            _delete_by_uid(root, uid)
            insert_child(new_parent, node, op.get("index"))
            parents[uid] = np_uid  # move 后 parent 链更新（子树内部关系不变）
            applied += 1
            if turn_ops is not None:
                turn_ops.append({
                    "uid": uid, "action": "move",
                    "before": {"parent_uid": old_parent_uid},
                    "after": {"parent_uid": np_uid},
                })
        else:
            errors.append(f"op[{i}]: 未知 action {action!r}")
    if applied:
        new_data = dict(file_md)
        new_data["root"] = root
        _commit_map(manager, key, doc, new_data, branch_uid)
    result: dict = {"applied": applied, "errors": errors, "created": created}
    if skipped_human:
        result["skipped_human_editing"] = skipped_human
    return result


def _delete_by_uid(node: dict, uid: str) -> bool:
    children = node.get("children", [])
    for i, c in enumerate(children):
        if c.get("data", {}).get("uid") == uid:
            children.pop(i)
            return True
        if _delete_by_uid(c, uid):
            return True
    return False


# ─── 轮次回滚：turns 存储 / 净 diff / 反向应用 ───

def _turns_path(session_file: str) -> Path:
    return Path(session_file).with_suffix(".turns.json")


def _load_turns(session_file: str) -> list[dict]:
    p = _turns_path(session_file)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_turns(session_file: str, turns: list[dict]) -> None:
    _atomic_write(_turns_path(session_file), json.dumps(turns, ensure_ascii=False, indent=1))


def _user_message_count(path: Path) -> int:
    """session jsonl 里 user 消息条数（轮次记录的 user_msg_idx 依据）。"""
    return len(_user_messages_from_jsonl(path))


def _user_messages_from_jsonl(path: Path) -> list[dict]:
    """从 session jsonl 提取全部轮次（user 消息）——轮次清单的权威来源。

    所有 session 天然有轮次（对话历史），不依赖 turns.json 的 diff 记录。
    返回 [{user_msg_idx, user_msg, ts, quoted}]；
    user_msg 剥离 NODE_ASSIST 前缀（含备注行），quoted 是解析出的引用信息
    {uid, text, note}（非引用轮次为 None）。
    """
    out = []
    try:
        lines = path.read_text().splitlines()
    except Exception:
        return out
    idx = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("type") != "message":
            continue
        msg = e.get("message") or {}
        if msg.get("role") != "user":
            continue
        idx += 1
        text = ""
        content = msg.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                c.get("text", "") for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            )
        quoted_list, body = _parse_node_assist(text)
        ts = msg.get("timestamp") or e.get("timestamp") or 0
        if ts and ts > 1e12:
            ts = ts / 1000.0
        out.append({
            "user_msg_idx": idx,
            "user_msg": body,
            "quoted_list": quoted_list,
            "quoted": quoted_list[0] if quoted_list else None,
            "ts": ts,
        })
    return out


def _parse_node_assist(text: str) -> tuple[list[dict], str]:
    """从 jsonl user 消息原文解析 NODE_ASSIST 引用信息。

    新格式（prompt() 拼装，多引用）：
        [NODE_ASSIST uid=xxx] 用户在节点「text」上求助
        [该节点的备注内容：note]（可选，第一个引用的）
        引用节点（消息中的 [引用N] 占位符指代这里的节点）：
        [引用1] uid=xxx 「text1」（备注：note1）
        [引用2] uid=yyy 「text2」
        用户消息原文（含 [引用N] 占位符）

    旧格式（单引用，向后兼容）：
        [NODE_ASSIST uid=xxx] 用户在节点「text」上求助
        [该节点的备注内容：note]（可选）
        用户消息原文

    返回 (quoted_list, body)：
        quoted_list: [{uid, text, note}, ...] 按消息中引用出现顺序
        body: 用户消息原文（剥离前缀/备注/引用列表段，保留 [引用N] 占位符）
        非引用消息返回 ([], text)。
    """
    if not text or not text.startswith("[NODE_ASSIST"):
        return [], text
    lines = text.split("\n")
    m = re.match(r"^\[NODE_ASSIST(?: uid=([^\]]*))?\]\s*用户在节点「(.+)」上求助$", lines[0])
    if not m:
        return [], text
    quoted_list = [{"uid": m.group(1) or "", "text": m.group(2), "note": ""}]
    # 首行后的内容：备注行（第一个引用的）/ 引用列表段 / 用户消息原文
    body_lines = []
    ref_re = re.compile(r"^\[引用(\d+)\] uid=([^\s「」]*) 「(.+)」(?:（备注：(.+)）)?$")
    for ln in lines[1:]:
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("[该节点的备注内容：") and len(quoted_list) == 1:
            note = ln[len("[该节点的备注内容："):]
            if note.endswith("]"):
                note = note[:-1]
            quoted_list[0]["note"] = note
            continue
        rm = ref_re.match(ln)
        if rm:
            ref = {
                "uid": rm.group(2),
                "text": rm.group(3),
                "note": rm.group(4) or "",
            }
            idx = int(rm.group(1))
            while len(quoted_list) < idx:
                quoted_list.append({"uid": "", "text": "", "note": ""})
            quoted_list[idx - 1] = ref
            continue
        if ln.startswith("引用节点"):
            continue  # 引用列表段标题
        body_lines.append(ln)
    body = "\n".join(body_lines).strip()
    return quoted_list, body


def _diff_uid_in_branch(d: dict, branch_uid: str, parents: dict) -> bool:
    """单条 diff 的 uid 是否属于该分支（用给定 parent 链判断）。

    uid 不在树里（已被删除）时用 diff 记录的父节点（before.parent_uid）判断；
    父也不在 / 无法判定 → 保守返回 False（宁可跳过，不误伤其他分支）。
    """
    if not branch_uid:
        return True
    uid = d.get("uid", "")
    if not uid:
        return False
    if uid in parents:
        return _belongs_to_branch(uid, branch_uid, parents)
    p = ((d.get("before") or {}).get("parent_uid") or "")
    if p and p in parents:
        return _belongs_to_branch(p, branch_uid, parents)
    return False


def _filter_diff_to_branch(diff: list[dict], branch_uid: str, parents: dict) -> list[dict]:
    """diff 按分支隔离：只保留属于本分支的改动。

    多 session 并发时，轮前/轮后全图对比会混入其他分支的改动（A 的 turn
    进行中 B 提交）；分支 agent 被约束只能改自己分支（apply_ops
    check_allowed / apply_map 拒绝越界），按归属过滤即等价于按 session 隔离。
    """
    if not branch_uid or not diff:
        return diff
    return [d for d in diff if _diff_uid_in_branch(d, branch_uid, parents)]


def _compute_net_diff(before_root: dict, after_root: dict) -> list[dict]:
    """轮前 vs 轮后 → 节点级净 diff（回滚的数据源）。

    同一节点多 op 合并：add/delete 只记顶层变化（父也变的由父带出整子树）；
    update_text 记录前后文本；move 记录前后父节点。
    """
    if not isinstance(before_root, dict) or not isinstance(after_root, dict):
        return []
    before_idx = _index_by_uid(before_root)
    after_idx = _index_by_uid(after_root)
    before_parents = _index_with_parent(before_root)
    after_parents = _index_with_parent(after_root)
    deleted = {u for u in before_idx if u not in after_idx}
    added = {u for u in after_idx if u not in before_idx}
    diffs = []
    for uid in deleted:
        if before_parents.get(uid) in deleted:  # 父也被删 → 由父的恢复带出
            continue
        node = before_idx[uid]
        diffs.append({
            "uid": uid, "action": "delete",
            "before": {"node": node, "parent_uid": before_parents.get(uid) or ""},
            "after": None,
        })
    for uid in added:
        if after_parents.get(uid) in added:  # 父也新增 → 由父的 add 带出
            continue
        diffs.append({
            "uid": uid, "action": "add",
            "before": None,
            "after": {"node": after_idx[uid], "parent_uid": after_parents.get(uid) or ""},
        })
    for uid in after_idx:
        if uid not in before_idx:
            continue
        old_d = before_idx[uid].get("data") or {}
        new_d = after_idx[uid].get("data") or {}
        if old_d.get("text") != new_d.get("text") or old_d.get("note") != new_d.get("note"):
            diffs.append({
                "uid": uid, "action": "update_text",
                "before": {"text": old_d.get("text", ""), "note": old_d.get("note", "")},
                "after": {"text": new_d.get("text", ""), "note": new_d.get("note", "")},
            })
        elif before_parents.get(uid) != after_parents.get(uid):
            diffs.append({
                "uid": uid, "action": "move",
                "before": {"parent_uid": before_parents.get(uid) or ""},
                "after": {"parent_uid": after_parents.get(uid) or ""},
            })
    return diffs


def _build_reverse_ops(later_diffs: list[dict], current_idx: dict, current_parents: dict):
    """把目标轮之后的 AI 改动转成反向 ops（含冲突预校验）。

    返回 (ops, skipped)；skipped 是用户已手动改过/删除，无法安全反向的节点。
    """
    ops, skipped = [], []
    for d in later_diffs:
        uid = d.get("uid", "")
        action = d.get("action")
        if action == "add":
            # 反向 = 删除 AI 新增节点；校验 uid 当前存在（用户可能删了）
            if uid in current_idx:
                # 预存祖先链（原始状态，删除后索引会清，祖先覆盖检查依赖它）
                anc = []
                cur = current_parents.get(uid)
                guard = 0
                while cur:
                    anc.append(cur)
                    cur = current_parents.get(cur) or ""
                    guard += 1
                    if guard > 100000:
                        break
                ops.append({"action": "delete", "uid": uid, "ancestors": anc})
            else:
                skipped.append({"uid": uid, "why": "节点已不存在"})
        elif action == "delete":
            # 反向 = 恢复被删节点（原 uid 整子树）；校验 uid 不存在 + 父存在
            parent_uid = (d.get("before") or {}).get("parent_uid", "")
            if uid in current_idx:
                skipped.append({"uid": uid, "why": "节点已存在（可能是你重建的）"})
            elif parent_uid and parent_uid not in current_idx:
                skipped.append({"uid": uid, "why": "父节点已不存在"})
            else:
                ops.append({
                    "action": "restore", "uid": uid,
                    "node": d["before"]["node"], "parent_uid": parent_uid,
                })
        elif action == "update_text":
            node = current_idx.get(uid)
            after_text = (d.get("after") or {}).get("text", "")
            if not node:
                skipped.append({"uid": uid, "why": "节点已不存在"})
            elif (node.get("data") or {}).get("text", "") != after_text:
                skipped.append({"uid": uid, "why": "你已手动修改过该节点"})
            else:
                ops.append({
                    "action": "update_text", "uid": uid,
                    "text": d["before"].get("text", ""),
                    "note": d["before"].get("note", ""),
                })
        elif action == "move":
            node = current_idx.get(uid)
            after_parent = (d.get("after") or {}).get("parent_uid", "")
            if not node:
                skipped.append({"uid": uid, "why": "节点已不存在"})
            elif current_parents.get(uid) != after_parent:
                skipped.append({"uid": uid, "why": "你已手动移动过该节点"})
            else:
                ops.append({
                    "action": "move", "uid": uid,
                    "parent_uid": d["before"].get("parent_uid", ""),
                })
    # 顺序：先恢复被删（父先于子），再删 AI 新增，再文本/移动
    order = {"restore": 0, "delete": 1, "update_text": 2, "move": 3}
    ops.sort(key=lambda o: order.get(o.get("action"), 9))
    return ops, skipped


def _apply_reverse(manager: ChatSessionManager, key: str, later_diffs: list[dict], branch_uid: str) -> list[dict]:
    """脑图回滚：把目标轮之后的 AI 改动反向应用（只撤销该 session 的改动）。

    复用写锁/原子写/版本号/人类编辑锁；冲突节点跳过并返回 skipped。
    """
    if not later_diffs:
        return []
    lock = manager._write_locks.setdefault(key, threading.Lock())
    with lock:
        doc, file_md, old_by_uid, _ = _load_doc_for_write(manager, key)
        root = file_md.get("root", file_md)
        if not root or not isinstance(root, dict) or "data" not in root:
            return [{"uid": "?", "why": "脑图为空"}]
        parents = _index_with_parent(root)
        ops, skipped = _build_reverse_ops(later_diffs, old_by_uid, parents)
        # 本批要删除的 uid 集合（用于识别"被祖先删除带走"的非冲突场景）
        delete_targets = {op.get("uid") for op in ops if op.get("action") == "delete"}
        human_lock = manager._human_editing.get(key)
        human_locked_uid = ""
        if human_lock and (time.time() - human_lock.get("ts", 0)) < 60:
            human_locked_uid = human_lock.get("uid", "")
        for op in ops:
            uid = op.get("uid", "")
            if human_locked_uid and uid and uid == human_locked_uid:
                skipped.append({"uid": uid, "why": "节点正在被编辑"})
                continue
            if op["action"] == "restore":
                if uid in old_by_uid:
                    skipped.append({"uid": uid, "why": "节点已存在"})
                    continue
                parent = old_by_uid.get(op.get("parent_uid", ""))
                if not parent:
                    skipped.append({"uid": uid, "why": "父节点不存在"})
                    continue
                node = op["node"]
                parent.setdefault("children", []).append(node)
                _register_subtree(node, old_by_uid)
                _register_parents(node, op.get("parent_uid", ""), parents)
            elif op["action"] == "delete":
                node = old_by_uid.get(uid)
                if not node:
                    # 祖先也在本批删除中？被父节点删除连带带走，属正常回滚非冲突
                    if any(a in delete_targets for a in op.get("ancestors", [])):
                        continue
                    skipped.append({"uid": uid, "why": "节点已不存在"})
                    continue
                parent_uid = parents.get(uid) or ""
                parent = old_by_uid.get(parent_uid) if parent_uid else None
                if parent:
                    parent["children"] = [c for c in parent.get("children", []) or []
                                          if (c.get("data") or {}).get("uid") != uid]
                _unregister_subtree(node, old_by_uid)
                _unregister_parents(node, parents)
            elif op["action"] == "update_text":
                node = old_by_uid.get(uid)
                if not node:
                    skipped.append({"uid": uid, "why": "节点已不存在"})
                    continue
                t = op.get("text", "")
                if t and "<" not in t and node["data"].get("richText"):
                    t = f"<p>{t}</p>"
                node["data"]["text"] = t
                if "note" in op:
                    node["data"]["note"] = op.get("note") or ""
            elif op["action"] == "move":
                node = old_by_uid.get(uid)
                new_parent = old_by_uid.get(op.get("parent_uid", ""))
                if not node or not new_parent:
                    skipped.append({"uid": uid, "why": "节点或父节点不存在"})
                    continue
                cur_parent_uid = parents.get(uid) or ""
                cur_parent = old_by_uid.get(cur_parent_uid) if cur_parent_uid else None
                if cur_parent:
                    cur_parent["children"] = [c for c in cur_parent.get("children", []) or []
                                              if (c.get("data") or {}).get("uid") != uid]
                new_parent.setdefault("children", []).append(node)
                parents[uid] = op.get("parent_uid", "")
        if ops:
            new_data = dict(file_md)
            new_data["root"] = root
            _commit_map(manager, key, doc, new_data, branch_uid)
        return skipped
