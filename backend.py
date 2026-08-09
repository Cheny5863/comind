from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import json, os, sys, zipfile, datetime, threading, traceback
import webbrowser
from pathlib import Path

import chat_service

_NULL_STREAMS = []


def _packaged_log_path() -> str:
    base = (
        os.environ.get("SMM_LOG_DIR")
        or os.environ.get("LOCALAPPDATA")
        or os.path.expanduser("~")
    )
    path = os.path.join(base, "CoMind")
    os.makedirs(path, exist_ok=True)
    return os.path.join(path, "comind-exe.log")


def _ensure_standard_streams() -> None:
    """PyInstaller --noconsole leaves stdio as None; uvicorn logging expects streams."""
    global _NULL_STREAMS
    log_path = _packaged_log_path() if getattr(sys, "frozen", False) else os.devnull
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r", encoding="utf-8", errors="replace")
        _NULL_STREAMS.append(sys.stdin)
    if sys.stdout is None:
        sys.stdout = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)
        _NULL_STREAMS.append(sys.stdout)
    if sys.stderr is None:
        sys.stderr = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)
        _NULL_STREAMS.append(sys.stderr)


_ensure_standard_streams()

app = FastAPI()


@app.exception_handler(Exception)
async def json_exception_handler(request: Request, exc: Exception):
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal Server Error. See local CoMind log for details.",
            "path": str(request.url.path),
        },
    )


# AI 助理会话管理器（pi --mode rpc 子进程池 + 脑图状态/diff/apply）
chat_manager = chat_service.ChatSessionManager()


@app.on_event("shutdown")
def _shutdown_cleanup():
    """后端退出时终止所有 pi 子进程（Windows 上避免孤儿 node 残留）。"""
    try:
        chat_manager.shutdown()
    except Exception:
        pass

# PyInstaller 打包后资源解压在 _MEIPASS；开发模式为项目根目录
if getattr(sys, "frozen", False):
    BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.environ.get("SMM_MAP_DIR") or os.path.expanduser("~/comind-maps")

# 挂载静态资源（目录不存在时跳过——dev/CI 无 dist 构建也能启动；打包后 _MEIPASS 内含 dist/ai-assistant）
def _mount_static(prefix: str, name: str, directory: str) -> None:
    if os.path.isdir(directory):
        app.mount(prefix, StaticFiles(directory=directory), name=name)
    else:
        print(f"[backend] 跳过挂载 {prefix}: 目录不存在 {directory}")

_mount_static("/dist", "dist", os.path.join(BASE_DIR, "dist"))
# AI 助理前端资源
_mount_static("/ai-assistant", "ai-assistant", os.path.join(BASE_DIR, "ai-assistant"))


def _resource_path(*parts: str) -> str:
    """Find packaged resources across PyInstaller and source layouts."""
    candidates = [os.path.join(BASE_DIR, *parts)]
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(exe_dir, *parts))
        candidates.append(os.path.join(exe_dir, "_internal", *parts))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), *parts))
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


# 脑图切换器
@app.get("/map-switcher.js")
def map_switcher_js():
    from fastapi.responses import FileResponse
    return FileResponse(_resource_path("map-switcher.js"), media_type="application/javascript")


# ─── 版本信息（供前端「检查更新」） ───

@app.get("/api/version")
def api_version():
    try:
        with open(os.path.join(BASE_DIR, "VERSION"), "r", encoding="utf-8") as f:
            version = f.read().strip()
    except OSError:
        version = "dev"
    return {"version": version, "name": "comind-server"}


# ─── XMind 解析 ───

def walk_xmind_topic(node, parent=None):
    """递归解析 XMind topic 树 → SimpleMindMap 节点树"""
    result = {
        "data": {
            "text": node.get("title", ""),
        },
        "children": []
    }
    # 超链接
    if node.get("href"):
        result["data"]["hyperlink"] = node["href"]
    # 备注
    notes = node.get("notes", {})
    if notes:
        plain = notes.get("plain", {})
        html = notes.get("realHTML", {})
        result["data"]["note"] = (html or plain or {}).get("content", "")
    # 标签
    labels = node.get("labels", [])
    if labels:
        result["data"]["tag"] = labels
    # 递归子节点
    children = node.get("children", {})
    for child in children.get("attached", []):
        result["children"].append(walk_xmind_topic(child, result))
    return result


def parse_xmind(path):
    """解析 .xmind 文件 → SimpleMindMap 完整数据格式"""
    with zipfile.ZipFile(path) as z:
        if "content.json" in z.namelist():
            raw = json.loads(z.read("content.json"))
        else:
            raise HTTPException(400, "不支持的老版 XMind 格式（无 content.json） / Unsupported old XMind format (no content.json)")

    canvas = raw[0] if isinstance(raw, list) else raw
    root_topic = canvas.get("rootTopic", {})
    root_title = root_topic.get("title", os.path.basename(path).replace(".xmind", ""))

    # 根据 structureClass 映射布局
    structure_map = {
        "org.xmind.ui.map.clockwise": "mindMap",
        "org.xmind.ui.map.anticlockwise": "mindMap",
        "org.xmind.ui.logicChart.right": "logicalStructure",
        "org.xmind.ui.logicChart.left": "logicalStructureLeft",
        "org.xmind.ui.tree.right": "catalogOrganization",
        "org.xmind.ui.tree.left": "catalogOrganization",
        "org.xmind.ui.orgChart": "organizationStructure",
        "org.xmind.ui.spreadsheet": "mindMap",
        "org.xmind.ui.timeline": "timeline",
    }
    layout = structure_map.get(root_topic.get("structureClass", ""), "mindMap")

    tree = walk_xmind_topic(root_topic)

    return {
        "mindMapData": {
            "root": tree,
            "theme": {"template": "avocado", "config": {}},
            "layout": layout,
            "config": {},
            "view": None,
        },
        "mindMapConfig": {},
        "lang": "zh",
        "localConfig": None,
    }


# ─── Workspace 操作 ───

def list_workspace_files():
    files = []
    if not os.path.isdir(WORKSPACE):
        return files
    for fname in os.listdir(WORKSPACE):
        fpath = os.path.join(WORKSPACE, fname)
        if os.path.islink(fpath) and not os.path.exists(os.readlink(fpath)):
            continue  # 断链
        if fname.endswith(".smm.json") or fname.endswith(".xmind"):
            stat = os.stat(fpath)
            files.append({
                "name": fname,
                "ext": fname.rsplit(".", 1)[-1],
                "size": stat.st_size,
                "mtime": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "_mtime": stat.st_mtime,
            })
    # 按修改时间倒序，越新越靠前
    files.sort(key=lambda f: f["_mtime"], reverse=True)
    for f in files:
        f.pop("_mtime", None)
    return files


def load_from_file(fname):
    fpath = os.path.join(WORKSPACE, fname)
    if not os.path.exists(fpath):
        raise HTTPException(404, f"文件 {fname} 不存在 / File {fname} not found")
    if fname.endswith(".xmind"):
        return parse_xmind(fpath)
    elif fname.endswith(".smm.json"):
        with open(fpath, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        raise HTTPException(400, f"不支持的文件类型: {fname} / Unsupported file type: {fname}")


WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}
WINDOWS_INVALID_FILENAME_CHARS = set('<>:"/\\|?*')


def _validate_windows_filename_stem(stem: str) -> None:
    if not stem:
        raise HTTPException(400, "文件名不能为空 / File name is required")
    if stem.endswith(".") or stem.endswith(" "):
        raise HTTPException(400, "文件名不能以空格或点结尾 / File name cannot end with a space or dot")
    bad = sorted({c for c in stem if c in WINDOWS_INVALID_FILENAME_CHARS or ord(c) < 32})
    if bad:
        chars = "".join(bad)
        raise HTTPException(400, f"文件名不能包含这些字符: {chars} / File name contains invalid characters: {chars}")
    if stem.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        raise HTTPException(400, "这是 Windows 保留文件名 / This is a reserved Windows file name")


def _normalize_workspace_file_name(
    name: str,
    *,
    allowed_exts: tuple[str, ...] = (".smm.json", ".xmind"),
    default_ext: str | None = None,
) -> str:
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "文件名不能为空 / File name is required")
    if default_ext and not any(name.endswith(ext) for ext in allowed_exts):
        name += default_ext
    _check_map_key(name)

    ext = next((ext for ext in allowed_exts if name.endswith(ext)), None)
    if not ext:
        raise HTTPException(400, "不支持的文件类型 / Unsupported file type")
    _validate_windows_filename_stem(name[:-len(ext)])
    return name


def _normalize_new_map_name(name: str) -> str:
    return _normalize_workspace_file_name(
        name,
        allowed_exts=(".smm.json",),
        default_ext=".smm.json",
    )


# ─── API ──

@app.post("/api/save_xmind")
async def api_save_xmind(request: Request, name: str = Query(..., description="文件名")):
    """接收 XMind 二进制 zip 数据，直接写入文件"""
    name = _normalize_workspace_file_name(name, allowed_exts=(".xmind",), default_ext=".xmind")
    fpath = os.path.join(WORKSPACE, name)
    os.makedirs(WORKSPACE, exist_ok=True)
    body = await request.body()
    with open(fpath, "wb") as f:
        f.write(body)
    return {"status": "ok", "name": name}

@app.get("/api/list")
def api_list():
    return list_workspace_files()


@app.get("/api/load")
def api_load(name: str = Query(..., description="文件名")):
    name = _normalize_workspace_file_name(name)
    return load_from_file(name)


def _ensure_map_ver(name: str) -> int:
    """返回脑图的当前写版本号。内存中没有时从磁盘 _comind_ver 恢复（服务重启场景）。"""
    ver = chat_manager._map_ver.get(name)
    if ver is not None:
        return ver
    # 服务重启后内存归零，从磁盘文件恢复持久化的版本号
    fpath = Path(WORKSPACE) / name
    disk_ver = 0
    if fpath.is_file():
        try:
            doc = json.loads(fpath.read_text())
            disk_ver = doc.get("_comind_ver", 0)
        except Exception:
            pass
    chat_manager._map_ver[name] = disk_ver
    return disk_ver


@app.get("/api/ver")
def api_ver(name: str = Query(...)):
    """返回脑图当前写版本（AI 写盘次数）。前端画布 load 时初始化、收到
    mindmap_update 时对齐；保存时带版本，后端据此判断前端是否落后。"""
    name = _normalize_workspace_file_name(name, allowed_exts=(".smm.json",), default_ext=".smm.json")
    return {"version": _ensure_map_ver(name)}


@app.post("/api/save")
def api_save(name: str = Query(...), body: dict = None, version: int = 0):
    name = _normalize_workspace_file_name(name, allowed_exts=(".smm.json",), default_ext=".smm.json")
    fpath = Path(WORKSPACE) / name
    os.makedirs(WORKSPACE, exist_ok=True)
    # 与 apply_ops/apply_map 共用 per-map 写锁（写队列串行化）+ 原子写：
    # 防多 session 并发（AI 写盘 vs 前端保存）互相覆盖/截断损坏。
    lock = chat_manager._write_locks.setdefault(name, threading.Lock())
    with lock:
        # ⚠️ 版本号只反映 mindMapData 内容变化（2026-08-05 核心修复）：
        # 前端多条路径都调 /api/save——data_change(用户编辑)、
        # view_data_change(拖拽/缩放/折叠，300ms debounce)、saveMindMapConfig(主题/布局)。
        # 若任何保存都 +1，A 拖一下视图 B 没动也"落后"→ 误弹"其他设备修改"。
        # 正确语义：内容没变（view/config/回声保存）→ 版本不递增，只更新磁盘。
        cur_ver = _ensure_map_ver(name)
        disk_doc = None
        disk_read_failed = False
        if fpath.is_file():
            try:
                disk_doc = json.loads(fpath.read_text(encoding="utf-8"))
            except Exception:
                disk_doc = None
                disk_read_failed = True  # 磁盘读失败 → 放行覆盖重建（不误判 conflict）
        body_md = (body or {}).get("mindMapData") if isinstance(body, dict) else None
        disk_md = (disk_doc or {}).get("mindMapData") if isinstance(disk_doc, dict) else None
        # ⚠️ 比较时排除纯 UI 状态（2026-08-06 多端 false conflict 根治）：
        # - 顶层 view：视口滚动/缩放，每台设备不同
        # - 树节点 data.isActive：当前选中节点，每台设备不同
        # - 树节点 data.expand：折叠/展开状态，每台设备不同
        # 这些字段变化不算"内容修改"，不应递增版本号。
        # 顶层 smmVersion 也排除（copyRenderTree 可能注入）。
        _UI_TOP_KEYS = {'view'}
        _UI_DATA_KEYS = {'isActive', 'expand'}
        _UI_NODE_KEYS = {'smmVersion'}
        def _strip_ui(md):
            """深拷贝 mindMapData，剥离纯 UI 状态字段后用于比较"""
            if not isinstance(md, dict):
                return md
            result = {}
            for k, v in md.items():
                if k in _UI_TOP_KEYS:
                    continue
                if k == 'root' and isinstance(v, dict):
                    result[k] = _strip_ui_node(v)
                else:
                    result[k] = v
            return result
        def _strip_ui_node(node):
            if not isinstance(node, dict):
                return node
            out = {}
            for k, v in node.items():
                if k in _UI_NODE_KEYS:
                    continue
                if k == 'data' and isinstance(v, dict):
                    out[k] = {dk: dv for dk, dv in v.items() if dk not in _UI_DATA_KEYS}
                elif k == 'children' and isinstance(v, list):
                    out[k] = [_strip_ui_node(c) for c in v]
                else:
                    out[k] = v
            return out
        content_changed = _strip_ui(body_md) != _strip_ui(disk_md)

        if content_changed and version < cur_ver and not disk_read_failed:
            # 真修改 + 版本落后（另一设备/AI 已写盘更新）→ 拒绝保存，
            # 返回最新树让前端自动刷新。陈旧端保存永远覆盖不了活跃端成果。
            disk_root = (disk_md or {}).get("root", disk_md) if isinstance(disk_md, dict) else disk_md
            return {
                "status": "conflict",
                "version": cur_ver,
                "tree": disk_root,
            }
        # 写盘（内容没变时也写：view/config 变化需要落盘，但版本不递增）
        if content_changed:
            # 只有内容真正变化才递增版本号
            chat_manager._map_ver[name] = cur_ver + 1
        # 持久化版本号到文件，服务重启后可恢复
        if isinstance(body, dict):
            body["_comind_ver"] = chat_manager._map_ver.get(name, cur_ver)
        chat_service._atomic_write(fpath, json.dumps(body, ensure_ascii=False, indent=2))
        # 保持内存态与磁盘一致：后续 AI 的 diff/apply 基于保存后的树
        if isinstance(body, dict) and "mindMapData" in body:
            chat_manager._map_state[name] = body["mindMapData"]
    return {"status": "ok", "name": name, "version": chat_manager._map_ver.get(name, 0)}


@app.post("/api/new")
def api_new(name: str = Query(..., description="新脑图文件名")):
    name = _normalize_new_map_name(name)
    os.makedirs(WORKSPACE, exist_ok=True)
    fpath = os.path.join(WORKSPACE, name)
    if os.path.exists(fpath):
        raise HTTPException(409, f"文件 {name} 已存在 / File {name} already exists")
    default = {
        "mindMapData": {
            "root": {
                "data": {"text": name.replace(".smm.json", "")},
                "children": [{"data": {"text": "双击编辑 / Double-click to edit"}, "children": []}],
            },
            "theme": {"template": "avocado", "config": {}},
            "layout": "logicalStructure",
            "config": {},
            "view": None,
        },
        "mindMapConfig": {},
        "lang": "zh",
        "localConfig": None,
    }
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(default, f, ensure_ascii=False, indent=2)
    return {"status": "ok", "name": name}


@app.post("/api/delete")
def api_delete(name: str = Query(..., description="文件名")):
    name = _normalize_workspace_file_name(name)
    fpath = os.path.join(WORKSPACE, name)
    if not os.path.exists(fpath):
        raise HTTPException(404, f"文件 {name} 不存在")
    os.remove(fpath)
    return {"status": "ok", "name": name}


@app.post("/api/rename")
def api_rename(old_name: str = Query(...), new_name: str = Query(...)):
    old_name = _normalize_workspace_file_name(old_name)
    old_path = os.path.join(WORKSPACE, old_name)
    if not os.path.exists(old_path):
        raise HTTPException(404, f"文件 {old_name} 不存在 / File {old_name} not found")
    if old_name.endswith(".smm.json"):
        ext = ".smm.json"
    elif old_name.endswith(".xmind"):
        ext = ".xmind"
    else:
        raise HTTPException(400, "不支持的文件类型 / Unsupported file type")
    new_name = _normalize_workspace_file_name(new_name, allowed_exts=(ext,), default_ext=ext)
    new_path = os.path.join(WORKSPACE, new_name)
    if os.path.exists(new_path):
        raise HTTPException(409, f"文件 {new_name} 已存在 / File {new_name} already exists")
    os.rename(old_path, new_path)
    return {"status": "ok", "old_name": old_name, "new_name": new_name}


# ─── AI 助理 API ───

def _check_map_key(map_key: str):
    if "/" in map_key or ".." in map_key or "\\" in map_key:
        raise HTTPException(400, "非法的脑图名 / Invalid mind map name")

class ChatPromptBody(BaseModel):
    message: str
    context: dict | None = None
    branch_uid: str = ""

class SwitchBody(BaseModel):
    session_file: str

class RollbackBody(BaseModel):
    user_msg_idx: int
    branch_uid: str = ""

class BackgroundBody(BaseModel):
    content: str

class ApplyBody(BaseModel):
    key: str
    tree: dict
    branch_uid: str = ""

class OpsBody(BaseModel):
    key: str
    ops: list
    branch_uid: str = ""

@app.post("/api/chat/{map_key}/prompt")
def api_chat_prompt(map_key: str, body: ChatPromptBody, request: Request):
    _check_map_key(map_key)
    if len(body.message) > 8000:
        raise HTTPException(400, "消息过长（限 8000 字符） / Message too long (max 8000 chars)")
    lang = request.headers.get("X-Lang") or None
    chat_manager.prompt(map_key, body.message, body.context, lang=lang, branch_uid=body.branch_uid)
    return {"status": "ok"}

@app.get("/api/chat/{map_key}/events")
def api_chat_events(map_key: str, branch: str = ""):
    _check_map_key(map_key)
    return StreamingResponse(
        chat_manager.events(map_key, branch),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@app.post("/api/chat/{map_key}/abort")
def api_chat_abort(map_key: str, branch: str = ""):
    _check_map_key(map_key)
    return {"aborted": chat_manager.abort(map_key, branch)}

@app.get("/api/chat/{map_key}/status")
def api_chat_status(map_key: str, branch: str = ""):
    _check_map_key(map_key)
    return chat_manager.get_status(map_key, branch)

@app.post("/api/chat/{map_key}/reset")
def api_chat_reset(map_key: str, branch: str = ""):
    _check_map_key(map_key)
    chat_manager.reset(map_key, branch)
    return {"status": "ok"}

@app.post("/api/chat/{map_key}/switch")
def api_chat_switch(map_key: str, body: SwitchBody, branch: str = ""):
    _check_map_key(map_key)
    if not chat_manager.switch_session(map_key, body.session_file, branch):
        raise HTTPException(400, "切换失败：会话文件不存在或不属于该脑图 / Switch failed: session file not found or not owned by this map")
    return {"status": "ok"}

@app.get("/api/chat/{map_key}/history")
def api_chat_history(map_key: str, branch: str = ""):
    _check_map_key(map_key)
    return chat_manager.get_history(map_key, branch)

@app.get("/api/chat/{map_key}/turns")
def api_chat_turns(map_key: str, branch: str = ""):
    """轮次列表（回滚弹层数据源）：时间 + 用户消息 + 该轮 AI 改动统计。"""
    _check_map_key(map_key)
    return chat_manager.list_turns(map_key, branch)

@app.post("/api/chat/{map_key}/rollback")
def api_chat_rollback(map_key: str, body: RollbackBody):
    """回滚到指定轮次（用户发那句话之前）：session 截断 + 脑图反向 diff。"""
    _check_map_key(map_key)
    result = chat_manager.rollback(map_key, body.branch_uid, body.user_msg_idx)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "回滚失败 / Rollback failed"))
    return result

@app.get("/api/chat/{map_key}/sessions")
def api_chat_sessions(map_key: str, branch: str = ""):
    _check_map_key(map_key)
    return chat_manager.list_sessions(map_key, branch)

@app.get("/api/chat/{map_key}/all_sessions")
def api_chat_all_sessions(map_key: str):
    _check_map_key(map_key)
    return chat_manager.all_sessions(map_key)

@app.get("/api/chat/{map_key}/agents")
def api_chat_agents(map_key: str):
    _check_map_key(map_key)
    return chat_manager.list_agents(map_key)

class ModelBody(BaseModel):
    provider: str
    model_id: str

@app.get("/api/chat/{map_key}/models")
def api_chat_models(map_key: str):
    _check_map_key(map_key)
    return chat_manager.get_models(map_key)

@app.post("/api/chat/{map_key}/model")
def api_chat_model(map_key: str, body: ModelBody):
    _check_map_key(map_key)
    if not chat_manager.set_model(map_key, body.provider, body.model_id):
        raise HTTPException(400, "切换模型失败 / Model switch failed")
    return {"status": "ok"}

class ThinkingBody(BaseModel):
    level: str

@app.get("/api/chat/{map_key}/thinking_levels")
def api_chat_thinking_levels(map_key: str):
    _check_map_key(map_key)
    return chat_manager.get_thinking_levels(map_key)

@app.post("/api/chat/{map_key}/thinking_level")
def api_chat_thinking_level(map_key: str, body: ThinkingBody):
    _check_map_key(map_key)
    if not chat_manager.set_thinking_level(map_key, body.level):
        raise HTTPException(400, "切换思考等级失败 / Thinking level switch failed")
    return {"status": "ok"}

class PanelBody(BaseModel):
    open: bool

@app.get("/api/chat/{map_key}/panel")
def api_chat_panel_get(map_key: str):
    _check_map_key(map_key)
    return {"open": chat_manager.get_panel_open(map_key)}

@app.post("/api/chat/{map_key}/panel")
def api_chat_panel_post(map_key: str, body: PanelBody):
    _check_map_key(map_key)
    chat_manager.set_panel_open(map_key, body.open)
    return {"status": "ok"}

class KeysBody(BaseModel):
    provider: str
    key: str | None = None

@app.get("/api/keys")
def api_keys_get():
    """返回各 provider 是否已配置 key（绝不回传明文）。"""
    keys = chat_service.PROVIDER_KEYS
    configured = {}
    for prov_id, env_var in chat_service.PROVIDER_ENV.items():
        configured[prov_id] = bool(keys.get(env_var))
    return {"configured": configured}

@app.post("/api/keys")
def api_keys_post(body: KeysBody):
    """保存/清除 provider key，并重启 pi 子进程池使新 key 立即生效。"""
    try:
        chat_service.save_key(body.provider, body.key)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except OSError as exc:
        raise HTTPException(500, f"保存 key 失败 / Failed to save key: {exc}")
    chat_service.reload_keys()
    n = chat_manager.restart_all()
    return {"status": "ok", "restarted": n}

@app.post("/api/chat/{map_key}/sync")
def api_chat_sync(map_key: str, body: dict):
    _check_map_key(map_key)
    chat_service.sync_map(chat_manager, map_key, body)
    return {"status": "ok"}

BACKGROUND_DIR = os.path.join(WORKSPACE, "backgrounds")

def _background_path(name: str) -> str:
    _check_map_key(name)
    base = name[:-9] if name.endswith(".smm.json") else name.rsplit(".", 1)[0]
    return os.path.join(BACKGROUND_DIR, base + ".md")

@app.get("/api/background")
def api_background_get(name: str = Query(...)):
    path = _background_path(name)
    if not os.path.exists(path):
        return {"content": ""}
    with open(path, "r", encoding="utf-8") as f:
        return {"content": f.read()}

@app.post("/api/background")
def api_background_post(body: BackgroundBody, name: str = Query(...)):
    path = _background_path(name)
    os.makedirs(BACKGROUND_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body.content)
    return {"status": "ok"}

@app.get("/api/mindmap/full")
def api_mindmap_full(key: str = Query(...)):
    _check_map_key(key)
    state = chat_manager._map_state.get(key)
    if state is None:
        raise HTTPException(400, "前端尚未同步脑图状态（POST /api/chat/{key}/sync） / Frontend has not synced map state (POST /api/chat/{key}/sync)")
    root = chat_service._state_root(state)
    return {"tree": chat_service.slim_tree(root)}

@app.get("/api/mindmap/outline")
def api_mindmap_outline(key: str = Query(...)):
    _check_map_key(key)
    result = chat_service.outline_tree(chat_manager, key)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result

@app.get("/api/mindmap/subtree")
def api_mindmap_subtree(key: str = Query(...), uid: str = Query(...)):
    _check_map_key(key)
    result = chat_service.subtree_slim(chat_manager, key, uid)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result

@app.get("/api/mindmap/diff")
def api_mindmap_diff(key: str = Query(...), branch: str = ""):
    _check_map_key(key)
    result = chat_service.diff_map(chat_manager, key, branch)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result

@app.post("/api/mindmap/apply")
def api_mindmap_apply(body: ApplyBody):
    _check_map_key(body.key)
    err = chat_service.apply_map(chat_manager, body.key, body.tree, body.branch_uid)
    if err:
        raise HTTPException(400, err)
    return {"status": "ok"}

class OpsBody(BaseModel):
    key: str
    ops: list
    branch_uid: str = ""

@app.post("/api/mindmap/apply_ops")
def api_mindmap_apply_ops(body: OpsBody):
    _check_map_key(body.key)
    result = chat_service.apply_ops(chat_manager, body.key, body.ops, body.branch_uid)
    if not result["applied"] and result["errors"]:
        raise HTTPException(400, json.dumps(result["errors"], ensure_ascii=False))
    return result


# ─── P2: 人类编辑锁 ───
# 前端双击编辑节点时 POST lock（上报 uid），编辑完成时 POST unlock。
# apply_ops 会检查该锁，跳过被人编辑的节点（不整批失败）。60 秒超时自动释放。

import time as _time

@app.post("/api/editing/{map_key}/lock")
def api_editing_lock(map_key: str, body: dict):
    uid = body.get("uid", "")
    if not uid:
        raise HTTPException(400, "uid required")
    chat_manager._human_editing[map_key] = {"uid": uid, "ts": _time.time()}
    return {"status": "locked", "uid": uid}


@app.post("/api/editing/{map_key}/unlock")
def api_editing_unlock(map_key: str):
    chat_manager._human_editing.pop(map_key, None)
    return {"status": "unlocked"}


# ─── 前端页面 ───

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(content=FILE_BROWSER_HTML, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


# ─── 编辑器页面 ───

@app.get("/editor", response_class=HTMLResponse)
def editor(name: str = Query("", description="文件名")):
    # 读取原始 index.html 并注入文件名
    index_path = _resource_path("index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        html = f.read()

    inject = f"""<script>
// ===== 后端接管模式 =====
window.externalPublicPath = '/dist/';
window.takeOverApp = true;
window.currentFileName = {json.dumps(name)};
window.currentFileExt = {json.dumps(name.rsplit('.', 1)[-1] if '.' in name else 'json')};
let mindMapInstance = null;

// 语言检测（与文件浏览器一致）：由 localStorage 的 SIMPLE_MIND_MAP_LANG 控制，默认中文
const _L = (zh, en) => {{ try {{ const s = localStorage.getItem('SIMPLE_MIND_MAP_LANG'); if (s) return s === 'en' ? en : zh; }} catch (e) {{}} return zh; }};

// 捕获 mindMap 实例（app_inited 事件）
const origOnload = window.onload;
window._origBusOn = null;
window.initCapture = () => {{
  if (window.$bus) {{
    window.$bus.$on('app_inited', (mindMap) => {{
      mindMapInstance = mindMap;
      installClipboardFix(mindMap);
      installExportFix(mindMap);
      // P2: 人类编辑锁——双击编辑节点时上报后端，AI 的 apply_ops 会跳过被人编辑的节点
      mindMap.on('before_show_text_edit', () => {{
        const active = mindMap.renderer.activeNodeList;
        const node = active && active[0];
        if (node && node.uid) {{
          fetch('/api/editing/' + encodeURIComponent(window.currentFileName) + '/lock', {{
            method: 'POST', headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{uid: node.uid}})
          }}).catch(() => {{}});
        }}
      }});
      mindMap.on('hide_text_edit', () => {{
        fetch('/api/editing/' + encodeURIComponent(window.currentFileName) + '/unlock', {{
          method: 'POST'
        }}).catch(() => {{}});
      }});
    }});
  }}
}};

// ===== 剪贴板修复 =====
// 生成节点树的文本表示（用于粘到微信等外部应用）
function getNodeTreeText(nodeDataList) {{
  function getText(d) {{
    var t = (d.data && d.data.text) || '';
    // 去掉 HTML 标签
    t = t.replace(/<[^>]+>/g, '');
    // 还原 HTML 实体（&lt; &gt; &amp; &quot; 等）
    var el = document.createElement('textarea');
    el.innerHTML = t;
    t = el.value;
    return t.trim();
  }}
  function fmt(n, prefix, last) {{
    var line = prefix + (prefix ? (last ? '└── ' : '├── ') : '') + getText(n);
    if (n.children && n.children.length > 0) {{
      var cp = prefix + (prefix ? (last ? '    ' : '│   ') : '');
      n.children.forEach(function(c, i) {{ line += String.fromCharCode(10) + fmt(c, cp, i === n.children.length - 1); }});
    }}
    return line;
  }}
  if (!nodeDataList || nodeDataList.length === 0) return '';
  return nodeDataList.map(function(it) {{ return fmt(it, '', nodeDataList.length === 1); }}).join(String.fromCharCode(10, 10));
}}

// 写入系统剪贴板（navigator.clipboard + execCommand 回退）
function writeClipboard(text) {{
  try {{
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(text).catch(function() {{
        fallbackCopy(text);
      }});
      return;
    }}
  }} catch(e) {{}}
  fallbackCopy(text);
}}
function fallbackCopy(text) {{
  try {{
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;left:-999999px;top:0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
  }} catch(e) {{ console.warn(_L('回退复制失败:', 'Fallback copy failed:'), e); }}
}}

function installClipboardFix(mindMap) {{
  if (!mindMap || !mindMap.renderer) return;
  var renderer = mindMap.renderer;
  var origPaste = renderer.paste.bind(renderer);
  // 跨脑图剪贴板通道：http 内网下 navigator.clipboard 不可用，
  // 用同源 localStorage 传递 smm 节点数据（剪贴板语义：不清空，直到下次 copy 覆盖）
  var CLIP_KEY = 'comind_clipboard';
  renderer.copy = function() {{
    this.beingCopyData = this.copyNode();
    if (!this.beingCopyData) return;
    try {{
      localStorage.setItem(CLIP_KEY, JSON.stringify(this.beingCopyData));
    }} catch (e) {{}}
    var treeText = getNodeTreeText(this.beingCopyData);
    writeClipboard(treeText);
  }};
  // 剪切同写 localStorage（原版 cut 只写内存+系统剪贴板，跨脑图粘贴会丢）
  renderer.cut = function() {{
    this.mindMap.execCommand('CUT_NODE', copyData => {{
      this.beingCopyData = copyData;
      if (!copyData) return;
      try {{
        localStorage.setItem(CLIP_KEY, JSON.stringify(copyData));
      }} catch (e) {{}}
      var treeText = getNodeTreeText(copyData);
      writeClipboard(treeText);
    }});
  }};
  renderer.paste = async function() {{
    if (this.beingCopyData) {{
      this.mindMap.execCommand('PASTE_NODE', this.beingCopyData);
      this.beingCopyData = null;
      return;
    }}
    // 跨脑图：读上次复制的节点数据（同页内存丢了也能粘）
    try {{
      var saved = localStorage.getItem(CLIP_KEY);
      if (saved) {{
        var data = JSON.parse(saved);
        if (data && data.length > 0) {{
          this.mindMap.execCommand('PASTE_NODE', data);
          return;
        }}
      }}
    }} catch (e) {{}}
    await origPaste();
  }};
}}

// readBlob 的本地副本（访问不了 core 模块内部作用域）
function localReadBlob(blob) {{
  return new Promise(function(resolve, reject) {{
    var reader = new FileReader();
    reader.onload = function(e) {{ resolve(e.target.result); }};
    reader.onerror = function(e) {{ reject(e); }};
    reader.readAsDataURL(blob);
  }});
}}

// ===== 导出编码修复：txt/md 加 BOM + charset 防 GBK 误识别 =====
function installExportFix(mindMap) {{
  if (!mindMap || !mindMap.doExport) return;
  var exp = mindMap.doExport;
  var BOM_CHAR = String.fromCharCode(0xFEFF);
  var wrap = ['txt', 'md'];
  wrap.forEach(function(m) {{
    if (typeof exp[m] !== 'function') return;
    var orig = exp[m].bind(exp);
    exp[m] = async function(name) {{
      var args = arguments;
      var result = await orig.apply(exp, args);
      var raw = atob(result.split(',')[1]);
      var text = decodeURIComponent(escape(raw));
      var blob = new Blob([BOM_CHAR + text], {{ type: 'text/plain;charset=utf-8' }});
      result = await localReadBlob(blob);
      return result;
    }};
  }});
}}

const getDataFromBackend = async () => {{
  const res = await fetch('/api/load?name=' + encodeURIComponent(window.currentFileName));
  if (!res.ok) {{ alert(_L('加载失败: ', 'Load failed: ') + await res.text()); return; }}
  const data = await res.json();
  return data;
}};

// 画布写版本：AI 写盘时后端版本 +1（SSE mindmap_update 带 ver 更新）；
// 保存时带版本，后端据此判断画布是否落后（防旧画布覆盖 AI 改动）
// ⚠️ 版本号必须同步注入（服务端渲染）——异步 fetch 有竞态：
// 页面初始化触发 data_change 自动保存时 version 还是 0 → 后端误判冲突 → 覆盖用户数据
window.__comindMapVer = {_ensure_map_ver(name)};

const setTakeOverAppMethods = (data) => {{
  window.takeOverAppMethods = {{}};
  window.takeOverAppMethods.getMindMapData = () => data.mindMapData;
  window.takeOverAppMethods.saveMindMapData = async (d) => {{
    // ⚠️ 回声保存抑制（双人协作死循环修复）：updateData 同步画布时会触发
    // data_change → 自动保存（内容=服务端推来的树，与磁盘一致）。若不抑制，
    // 每次 conflict/AI 广播后的 updateData 都会回声保存 → 版本无意义 +1 →
    // 另一设备永远落后 → 每次都 conflict → 疯狂弹"其他设备修改"。
    // 抑制窗口内（updateData 同步调用链）跳过自动保存，版本只在真实编辑时递增。
    if (window.__comindSuppressSave) return;
    // ⚠️ 保存串行化（2026-08-09 慢网络竞态修复）：连续编辑会并发触发多次
    // saveMindMapData，若不加锁，多个请求携带相同旧 version 同时到达后端，
    // 后到的会被乐观锁误判 conflict（单端也会弹"其他设备修改"！）。
    // 保存中再来 → 记下最新待保存数据，当前请求完成后用最新版本重发。
    if (window.__comindSaving) {{
      window.__comindPendingSave = d;
      return;
    }}
    window.__comindSaving = true;
    try {{
      if (window.currentFileExt === 'xmind') {{
        // 使用原生 ExportXMind 插件导出为 XMind zip
        if (!mindMapInstance) {{ alert(_L('编辑器尚未初始化', 'Editor not initialized')); return; }}
        try {{
          const zipBlob = await mindMapInstance.doExport.xmind(d, window.currentFileName);
          await fetch('/api/save_xmind?name=' + encodeURIComponent(window.currentFileName), {{
            method: 'POST',
            body: zipBlob
          }});
        }} catch(e) {{ alert(_L('XMind 保存失败: ', 'XMind save failed: ') + e.message); }}
      }} else {{
        // copyRenderTree 会把渲染根节点的非 data/children 字段复制进 getData()
        // 输出（含 root 冗余快照）。保存前剥离，防止磁盘被污染：
        // root 树里的 root key 会让后端 sync_map 规范化失效，AI 读到旧快照。
        if (d && d.root && d.root.root && typeof d.root.root === 'object') {{
          delete d.root.root;
        }}
        const current = await (await fetch('/api/load?name=' + encodeURIComponent(window.currentFileName))).json();
        current.mindMapData = d;
        // ⚠️ version 必须用 load 返回的磁盘版本（current._comind_ver），不能只用
        // 内存 window.__comindMapVer——慢网络下内存可能滞后（上一次保存还没返回），
        // 提交旧版本 → 后端误判 conflict。load 的版本是磁盘权威，且串行锁保证
        // 这次 load 到本次保存之间没有其他写。
        const verForSave = (current && typeof current._comind_ver === 'number') ? current._comind_ver : (window.__comindMapVer || 0);
        const resp = await fetch('/api/save?name=' + encodeURIComponent(window.currentFileName) + '&version=' + verForSave, {{
          method: 'POST', headers: {{'Content-Type':'application/json'}},
          body: JSON.stringify(current)
        }});
        const j = await resp.json().catch(() => ({{}}));
        if (j && j.status === 'conflict') {{
          // 乐观锁冲突：画布是陈旧视图（另一设备/AI 已更新）→ 自动刷新为最新树
          window.__comindMapVer = j.version || 0;
          if (j.tree && mindMapInstance) {{
            // 抑制回声保存：updateData 会触发 data_change → 自动保存，若不禁
            // 会把刚同步的树再保存一次 → 版本 +1 → 对方设备永远落后（死循环）
            window.__comindSuppressSave = true;
            try {{
              mindMapInstance.updateData(j.tree);
            }} finally {{
              window.__comindSuppressSave = false;
            }}
          }}
          try {{
            const hint = _L('检测到其他设备的修改，画布已刷新为最新版本', 'Detected changes from another device, canvas refreshed to latest');
            if (window.toast) window.toast(hint); else alert(hint);
          }} catch (e) {{}}
          return;
        }}
        // 保存成功 → 后端版本已递增，本地对齐（否则下次保存仍带旧版本号，
        // 被误判"落后"触发冲突；多端并发时也保证本地版本与磁盘一致）
        if (typeof j.version === 'number') window.__comindMapVer = j.version;
      }}
    }} finally {{
      window.__comindSaving = false;
      // 保存期间有新编辑（被锁挡住记在 pending）→ 用最新版本补发一次
      if (window.__comindPendingSave) {{
        const pending = window.__comindPendingSave;
        window.__comindPendingSave = null;
        window.takeOverAppMethods.saveMindMapData(pending);
      }}
    }}
  }};
  window.takeOverAppMethods.getMindMapConfig = () => data.mindMapConfig;
  window.takeOverAppMethods.saveMindMapConfig = async (c) => {{
    // config 是追加修改（主题/布局），乐观锁冲突时重试一次（重新加载最新再保存）
    for (let attempt = 0; attempt < 3; attempt++) {{
      const current = await (await fetch('/api/load?name=' + encodeURIComponent(window.currentFileName))).json();
      current.mindMapConfig = c;
      const resp = await fetch('/api/save?name=' + encodeURIComponent(window.currentFileName) + '&version=' + (window.__comindMapVer || 0), {{
        method: 'POST', headers: {{'Content-Type':'application/json'}},
        body: JSON.stringify(current)
      }});
      const j = await resp.json().catch(() => ({{}}));
      if (j && j.status === 'conflict') {{
        window.__comindMapVer = j.version || 0;
        continue;  // 重试：基于最新磁盘再写 config
      }}
      if (typeof j.version === 'number') window.__comindMapVer = j.version;
      return;
    }}
  }};
  window.takeOverAppMethods.getLanguage = () => {{
    // 语言偏好的唯一来源：localStorage（全局，非 per-file）
    try {{
      var stored = localStorage.getItem('SIMPLE_MIND_MAP_LANG');
      if (stored) return stored;
    }} catch(e) {{}}
    // 首次访问：读文件 lang 字段作为 seed，写入 localStorage
    var lang = data.lang || 'zh';
    try {{ localStorage.setItem('SIMPLE_MIND_MAP_LANG', lang); }} catch(e) {{}}
    return lang;
  }};
  window.takeOverAppMethods.saveLanguage = (l) => {{
    // 只写 localStorage，不写文件。语言是全局用户偏好，不是 per-file 属性
    try {{ localStorage.setItem('SIMPLE_MIND_MAP_LANG', l); }} catch(e) {{}}
  }};
  window.takeOverAppMethods.getLocalConfig = () => data.localConfig;
  window.takeOverAppMethods.saveLocalConfig = async (c) => {{
    const current = await (await fetch('/api/load?name=' + encodeURIComponent(window.currentFileName))).json();
    current.localConfig = c;
    await fetch('/api/save?name=' + encodeURIComponent(window.currentFileName), {{
      method: 'POST', headers: {{'Content-Type':'application/json'}},
      body: JSON.stringify(current)
    }});
  }};
}};

window.onload = async () => {{
  if (!window.takeOverApp) return;
  window.initCapture();
  const data = await getDataFromBackend();
  if (!data) return;
  document.title = data.mindMapData.root.data.text.replace(/<[^>]+>/g, '') + ' - CoMind';
  setTakeOverAppMethods(data);
  window.initApp();
}};
</script>"""

    # 去掉所有原始 inline script 标签（保留 src= 标签），注入接管脚本
    # 用字符串操作：过滤掉不含 src= 的 <script> 块
    import re as _re
    def _keep_script(m):
        tag = m.group(0)
        if ' src=' in tag[:tag.index('>')]:
            return tag  # 保持外部脚本
        return ''      # 去掉 inline 脚本
    html = _re.sub(r'<script[^>]*>.*?</script>', _keep_script, html, flags=_re.DOTALL)
    # 在 </head> 之前注入接管脚本
    html = html.replace('</head>', inject + '</head>')

    # 注入脑图切换器 + AI 助理
    ai_js = os.path.join(BASE_DIR, "ai-assistant", "ai-assistant.js")
    ai_ver = int(os.path.getmtime(ai_js)) if os.path.exists(ai_js) else 0
    switcher_js = _resource_path("map-switcher.js")
    switcher_ver = int(os.path.getmtime(switcher_js)) if os.path.exists(switcher_js) else 0
    extra_inject = (
        f'<script src="/map-switcher.js?v={switcher_ver}"></script>'
        f'<script src="/ai-assistant/ai-assistant.js?v={ai_ver}"></script>'
    )
    html = html.replace('</body>', extra_inject + '</body>')

    # 注入中文字体优化 CSS（解决导出图片乱码问题）
    font_css = """
<style>
/* 导出时提供更全面的中文字体回退 */
.smm-richtext-node-wrap,
foreignObject div,
text.smm-text-node-wrap,
.exportContainer * {
  font-family: "Microsoft YaHei", "微软雅黑", "Noto Sans SC", "PingFang SC", "Hiragino Sans GB", "WenQuanYi Micro Hei", "Noto Sans CJK SC", "Source Han Sans SC", sans-serif !important;
}
</style>"""
    html = html.replace('</head>', font_css + '</head>')
    return HTMLResponse(content=html, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


# ─── 内嵌文件浏览器 HTML ───

FILE_BROWSER_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>CoMind - Mind Map Directory</title>
<script>
// 语言检测：由 localStorage 的 SIMPLE_MIND_MAP_LANG 控制（默认中文，不跟浏览器语言）
function _L(zh, en) {
  try {
    var s = localStorage.getItem('SIMPLE_MIND_MAP_LANG');
    if (s) return s === 'en' ? en : zh;
  } catch (e) {}
  return zh;
}
</script>
<link rel="icon" href="dist/logo.ico">
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #fafafa; color: #2c3e50; min-height: 100vh;
}
.header {
  background: #fff; padding: 16px 20px;
  display: flex; align-items: center; justify-content: space-between;
  position: sticky; top: 0; z-index: 10;
  border-bottom: 1px solid rgba(0,0,0,0.06);
  box-shadow: 0 2px 16px 0 rgba(0,0,0,0.04);
}
.header h1 { font-size: 18px; font-weight: 600; color: #2c3e50; }
.header .sub { font-size: 12px; color: #999; margin-top: 2px; }
.btn {
  background: #f0f0f0; color: #2c3e50; border: none; padding: 8px 16px;
  border-radius: 6px; font-size: 14px; cursor: pointer;
  transition: background .2s;
}
.btn:hover { background: #e0e0e0; }
.btn-primary { background: #549688; color: #fff; }
.btn-primary:hover { background: #478a7c; }
.container { max-width: 800px; margin: 0 auto; padding: 16px; }
.file-list { display: flex; flex-direction: column; gap: 8px; }
.file-item {
  background: #fff; border-radius: 8px; padding: 14px 16px;
  cursor: pointer; display: flex; align-items: center; justify-content: space-between;
  position: relative; z-index: 1;
  transition: transform 0.2s ease, background .15s;
  box-shadow: 0 1px 4px rgba(0,0,0,0.04);
  border: 1px solid rgba(0,0,0,0.06);
}
.file-item.swiping { transition: none; }
.file-item:hover { background: #f5f9f8; }
.file-item:active { background: #eef5f3; }
.file-icon { font-size: 20px; margin-right: 12px; flex-shrink: 0; }
.file-info { flex: 1; min-width: 0; }
.file-name { font-size: 15px; font-weight: 500; color: #2c3e50; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.file-meta { font-size: 12px; color: #999; margin-top: 2px; }
.file-ext {
  font-size: 11px; padding: 2px 6px; border-radius: 4px;
  background: #f0f0f0; color: #888; flex-shrink: 0;
}
.empty-state {
  text-align: center; padding: 60px 20px; color: #999;
}
.empty-state p { margin-bottom: 16px; }
.modal-overlay {
  display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.3);
  z-index: 100; align-items: center; justify-content: center;
}
.modal-overlay.active { display: flex; }
.modal {
  background: #fff; border-radius: 12px; padding: 24px;
  width: 90%; max-width: 360px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.12);
}
.modal h2 { font-size: 16px; margin-bottom: 12px; color: #2c3e50; }
.modal input {
  width: 100%; padding: 10px 12px; border: 1px solid #ddd;
  border-radius: 6px; background: #fafafa; color: #2c3e50;
  font-size: 14px; outline: none;
}
.modal input:focus { border-color: #549688; }
.modal-actions { display: flex; gap: 8px; margin-top: 16px; justify-content: flex-end; }
.modal-actions .btn { flex: 1; }
.loading { text-align: center; padding: 40px; color: #999; }
.swipe-wrapper {
  position: relative; overflow: hidden; border-radius: 8px;
  touch-action: pan-y;
}
.swipe-action-delete {
  position: absolute; right: 0; top: 0; bottom: 0; width: 80px;
  background: #e74c3c; color: #fff; display: flex;
  align-items: center; justify-content: center;
  font-size: 14px; font-weight: 500; cursor: pointer; user-select: none;
}
.swipe-action-delete:active { background: #c0392b; }
.toast {
  position: fixed; bottom: 40px; left: 50%; transform: translateX(-50%);
  background: rgba(40,40,40,0.88); color: #fff; padding: 10px 24px;
  border-radius: 20px; font-size: 14px; z-index: 200;
  opacity: 0; transition: opacity 0.3s; pointer-events: none;
}
.toast.show { opacity: 1; }
.ctx-menu {
  position: fixed; z-index: 300; background: #fff; border-radius: 8px;
  box-shadow: 0 4px 24px rgba(0,0,0,0.12); border: 1px solid rgba(0,0,0,0.08);
  min-width: 140px; padding: 4px 0; overflow: hidden;
}
.ctx-item {
  padding: 8px 16px; font-size: 14px; color: #2c3e50; cursor: pointer;
  transition: background .15s;
}
.ctx-item:hover { background: #f5f9f8; }
.ctx-danger { color: #e74c3c; }
.ctx-danger:hover { background: #fef2f2; }
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>🧠 CoMind</h1>
    <div class="sub">~/comind-maps/</div>
  </div>
  <button class="btn btn-primary" onclick="showNewModal()" data-zh="+ 新建" data-en="+ New">+ 新建</button>
</div>
<div class="container">
  <div class="file-list" id="fileList">
    <div class="loading" data-zh="加载中..." data-en="Loading...">加载中...</div>
  </div>
</div>

<div class="modal-overlay" id="newModal">
  <div class="modal">
    <h2 data-zh="新建脑图" data-en="New Mind Map">新建脑图</h2>
    <input type="text" id="newName" data-zh-ph="文件名" data-en-ph="File name" placeholder="文件名" autofocus
           onkeydown="if(event.key==='Enter') createNew()">
    <div class="modal-actions">
      <button class="btn" onclick="hideNewModal()" data-zh="取消" data-en="Cancel">取消</button>
      <button class="btn btn-primary" onclick="createNew()" data-zh="创建" data-en="Create">创建</button>
    </div>
  </div>
</div>

<div class="modal-overlay" id="renameModal">
  <div class="modal">
    <h2 data-zh="重命名" data-en="Rename">重命名</h2>
    <input type="text" id="renameName" data-zh-ph="新文件名" data-en-ph="New file name" placeholder="新文件名"
           onkeydown="if(event.key==='Enter') doRename()">
    <div class="modal-actions">
      <button class="btn" onclick="hideRenameModal()" data-zh="取消" data-en="Cancel">取消</button>
      <button class="btn btn-primary" onclick="doRename()" data-zh="确定" data-en="OK">确定</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
// ─── State ───
let touchStartX = 0, touchStartY = 0, touchStartOffset = 0;
let isSwiping = false, isScrolling = false;
let longPressTimer = null, longPressFired = false;
let activeWrapper = null, activeItem = null;
let openWrapper = null;
let preventClick = false;
let renameTarget = null;

// ─── Toast ───
function showToast(msg, ms) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), ms || 2000);
}

// ─── Swipe helpers ───
function closeOpenSwipe() {
  if (!openWrapper) return;
  const item = openWrapper.querySelector('.file-item');
  if (item) { item.classList.remove('swiping'); item.style.transform = ''; }
  openWrapper = null;
}

// ─── Touch handlers ───
function onPointerStart(e, wrapper) {
  if (!wrapper || !wrapper.dataset) return;
  var pt = e.touches[0];
  touchStartX = pt.clientX; touchStartY = pt.clientY;
  isSwiping = false; isScrolling = false;
  longPressFired = false; preventClick = false;
  activeWrapper = wrapper;
  activeItem = wrapper.querySelector('.file-item');
  if (!activeItem) { activeWrapper = null; return; }

  var del = wrapper.querySelector('.swipe-action-delete');
  if (del && del.contains(e.target)) {
    activeWrapper = null; activeItem = null; return;
  }

  var m = (activeItem.style.transform || '').match(/-?\\d+/);
  touchStartOffset = m ? parseInt(m[0]) : 0;

  if (openWrapper && openWrapper !== wrapper) {
    closeOpenSwipe(); preventClick = true; return;
  }

  if (!openWrapper) {
    longPressTimer = setTimeout(function() {
      longPressFired = true; longPressTimer = null; preventClick = true;
      if (navigator.vibrate) navigator.vibrate(50);
      showRenameModal(decodeURIComponent(wrapper.dataset.name));
    }, 600);
  }
}

function onPointerMove(e) {
  if (!activeItem || longPressFired) return;
  var pt = e.touches[0];
  var dx = pt.clientX - touchStartX, dy = pt.clientY - touchStartY;
  if (!isSwiping && !isScrolling) {
    if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
    if (Math.abs(dy) > Math.abs(dx)) {
      isScrolling = true;
      if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; }
      return;
    }
    isSwiping = true;
    if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; }
    activeItem.classList.add('swiping');
  }
  if (isScrolling) return;
  var total = Math.max(-80, Math.min(80, touchStartOffset + dx));
  activeItem.style.transform = 'translateX(' + total + 'px)';
  e.preventDefault();
}

function onPointerEnd(e) {
  if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; }
  if (!activeWrapper) return;
  if (longPressFired) { preventClick = true; activeWrapper = null; activeItem = null; return; }
  if (isSwiping && activeItem) {
    preventClick = true;
    var m = (activeItem.style.transform || '').match(/-?\\d+/);
    var total = m ? parseInt(m[0]) : 0;
    activeItem.classList.remove('swiping');
    if (total <= -40) {
      activeItem.style.transform = 'translateX(-80px)';
      openWrapper = activeWrapper;
    } else {
      activeItem.style.transform = '';
      if (openWrapper === activeWrapper) openWrapper = null;
      if (touchStartOffset === 0 && total >= 40) {
        copyFilePath(decodeURIComponent(activeWrapper.dataset.name));
      }
    }
    activeWrapper = null; activeItem = null; return;
  }
  if (openWrapper) {
    closeOpenSwipe(); preventClick = true;
  }
  activeWrapper = null; activeItem = null;
}

function onPointerCancel() {
  if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; }
  if (activeItem) { activeItem.classList.remove('swiping'); activeItem.style.transform = ''; }
  activeWrapper = null; activeItem = null;
}

// ─── PC right-click context menu ───
var ctxMenu = null;
var ctxTarget = null;

function onContextMenu(e, wrapper) {
  e.preventDefault();
  closeContextMenu();
  ctxTarget = wrapper;
  var name = decodeURIComponent(wrapper.dataset.name);
  ctxMenu = document.createElement('div');
  ctxMenu.className = 'ctx-menu';
  ctxMenu.innerHTML = '<div class="ctx-item" data-action="rename">' + _L('重命名', 'Rename') + '</div>'
    + '<div class="ctx-item" data-action="copy">' + _L('复制路径', 'Copy Path') + '</div>'
    + '<div class="ctx-item ctx-danger" data-action="delete">' + _L('删除', 'Delete') + '</div>';
  ctxMenu.addEventListener('click', function(ev) {
    var action = ev.target.dataset.action;
    if (!action) return;
    if (action === 'rename') showRenameModal(name);
    else if (action === 'copy') copyFilePath(name);
    // 用闭包捕获的 wrapper/name，不用 ctxTarget——document 捕获阶段的
    // closeContextMenu 会在点击菜单项时先把 ctxTarget 清成 null，
    // 之前 deleteFile(ctxTarget) 因此抛 TypeError，删除静默失效
    else if (action === 'delete') { if (confirm(_L('确定删除 ', 'Delete ') + name + '?')) deleteFile(wrapper); }
    closeContextMenu();
  });
  document.body.appendChild(ctxMenu);
  // Position
  var x = e.clientX, y = e.clientY;
  var mw = ctxMenu.offsetWidth, mh = ctxMenu.offsetHeight;
  if (x + mw > window.innerWidth) x = window.innerWidth - mw - 4;
  if (y + mh > window.innerHeight) y = window.innerHeight - mh - 4;
  ctxMenu.style.left = x + 'px';
  ctxMenu.style.top = y + 'px';
}

function closeContextMenu() {
  if (ctxMenu) { ctxMenu.remove(); ctxMenu = null; }
  ctxTarget = null;
}
document.addEventListener('click', closeContextMenu, true);

function onItemClick(e, wrapper) {
  if (preventClick) { preventClick = false; return; }
  openFile(decodeURIComponent(wrapper.dataset.name));
}

// ─── File operations ───
function openFile(name) {
  window.location.href = '/editor?name=' + encodeURIComponent(name);
}

async function deleteFile(wrapper) {
  const name = decodeURIComponent(wrapper.dataset.name);
  wrapper.style.display = 'none';
  openWrapper = null;
  try {
    const res = await fetch('/api/delete?name=' + encodeURIComponent(name), { method: 'POST' });
    if (res.ok) { showToast(_L('已删除', 'Deleted')); loadList(); }
    else {
      wrapper.style.display = '';
      const d = await res.json(); showToast(_L('删除失败: ', 'Delete failed: ') + (d.detail || ''), 3000);
    }
  } catch(e) {
    wrapper.style.display = '';
    showToast(_L('删除失败: ', 'Delete failed: ') + e.message, 3000);
  }
}

function copyFilePath(name) {
  const p = '~/comind-maps/' + name;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(p).then(() => showToast(_L('已复制路径', 'Path copied'))).catch(() => {
      fbCopy(p); showToast(_L('已复制路径', 'Path copied'));
    });
  } else { fbCopy(p); showToast(_L('已复制路径', 'Path copied')); }
}

function fbCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text; ta.style.cssText = 'position:fixed;left:-9999px';
  document.body.appendChild(ta); ta.select();
  document.execCommand('copy'); document.body.removeChild(ta);
}

// ─── Rename ───
function showRenameModal(name) {
  renameTarget = name;
  document.getElementById('renameModal').classList.add('active');
  const input = document.getElementById('renameName');
  input.value = name.replace(/\\.(smm\\.json|xmind)$/, '');
  setTimeout(() => { input.focus(); input.select(); }, 100);
}

function hideRenameModal() {
  document.getElementById('renameModal').classList.remove('active');
  renameTarget = null;
}

async function doRename() {
  const newName = document.getElementById('renameName').value.trim();
  const oldName = renameTarget;
  if (!newName || !oldName) return;
  hideRenameModal();
  try {
    const res = await fetch('/api/rename?old_name=' + encodeURIComponent(oldName) + '&new_name=' + encodeURIComponent(newName), { method: 'POST' });
    const d = await res.json();
    if (res.ok) { showToast(_L('已重命名', 'Renamed')); loadList(); }
    else { showToast(_L('重命名失败: ', 'Rename failed: ') + (d.detail || ''), 3000); }
  } catch(e) { showToast(_L('重命名失败: ', 'Rename failed: ') + e.message, 3000); }
}

// ─── New file ───
function showNewModal() {
  document.getElementById('newModal').classList.add('active');
  document.getElementById('newName').value = '';
  setTimeout(() => document.getElementById('newName').focus(), 100);
}

function hideNewModal() {
  document.getElementById('newModal').classList.remove('active');
}

async function createNew() {
  const name = document.getElementById('newName').value.trim();
  if (!name) return;
  hideNewModal();
  try {
    const res = await fetch('/api/new?name=' + encodeURIComponent(name), { method: 'POST' });
    const text = await res.text();
    let d = {};
    try { d = text ? JSON.parse(text) : {}; } catch (_) { d = { detail: text || res.statusText }; }
    if (res.ok) { openFile(d.name); }
    else { showToast(_L('创建失败: ', 'Create failed: ') + (d.detail || ''), 3000); }
  } catch(e) { showToast(_L('创建失败: ', 'Create failed: ') + e.message, 3000); }
}

// ─── Load file list ───
async function loadList() {
  const el = document.getElementById('fileList');
  try {
    const res = await fetch('/api/list');
    const files = await res.json();
    if (files.length === 0) {
      el.innerHTML = '<div class="empty-state"><p>' + _L('还没有脑图文件', 'No mind map files yet') + '</p><button class="btn btn-primary" onclick="showNewModal()">' + _L('创建第一个', 'Create the first one') + '</button></div>';
      return;
    }
    el.innerHTML = files.map(f => {
      const icon = f.ext === 'xmind' ? '🔶' : '🧠';
      const mt = new Date(f.mtime).toLocaleString(_L('zh-CN', 'en-US'), {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
      const dn = f.name.replace(/\\.(smm\\.json|xmind)$/, '');
      const sz = (f.size/1024).toFixed(0);
      return `<div class="swipe-wrapper" data-name="${encodeURIComponent(f.name)}" ontouchstart="onPointerStart(event,this)" ontouchmove="onPointerMove(event)" ontouchend="onPointerEnd(event)" ontouchcancel="onPointerCancel()" oncontextmenu="onContextMenu(event,this)"><div class="swipe-action-delete" onclick="deleteFile(this.closest('.swipe-wrapper'))">${_L('删除', 'Delete')}</div><div class="file-item" onclick="onItemClick(event,this.closest('.swipe-wrapper'))"><span class="file-icon">${icon}</span><div class="file-info"><div class="file-name">${dn}</div><div class="file-meta">${mt} · ${sz}KB</div></div><span class="file-ext">${f.ext}</span></div></div>`;
    }).join('');
  } catch(e) {
    el.innerHTML = '<div class="empty-state"><p>' + _L('加载失败: ', 'Load failed: ') + e.message + '</p></div>';
  }
}

// ─── i18n 静态文本替换 ───
document.title = _L('CoMind - 脑图工作目录', 'CoMind - Mind Map Directory');
document.querySelectorAll('[data-zh]').forEach(function (el) {
  el.textContent = _L(el.getAttribute('data-zh'), el.getAttribute('data-en'));
});
document.querySelectorAll('[data-zh-ph]').forEach(function (el) {
  el.setAttribute('placeholder', _L(el.getAttribute('data-zh-ph'), el.getAttribute('data-en-ph')));
});

// ─── Global listeners ───
window.addEventListener('scroll', closeOpenSwipe, { passive: true });
document.addEventListener('click', function(e) {
  if (openWrapper && !e.target.closest('.swipe-wrapper')) closeOpenSwipe();
}, true);

loadList();
</script>
</body>
</html>"""

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("SMM_PORT", "8789"))
    if getattr(sys, "frozen", False) and os.environ.get("SMM_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    uvicorn.run(app, host="0.0.0.0", port=port)
