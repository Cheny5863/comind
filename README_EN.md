<h1 align="center">CoMind · AI Collaborative Mind Map</h1>

<p align="center">
  <b>See the big picture. Collaborate precisely with AI agents.</b><br>
  <a href="./README.md">中文</a> | English
</p>

CoMind is an all-in-one tool combining an **AI assistant** with a **mind map**. The mind map is the *visual canvas* — see the whole picture at a glance (project overview, progress status, todos); the AI assistant is the *core* — a deeply integrated agent that thinks with you, researches, organizes, and edits the map directly, making "thinking clearly" and "getting it done" seamless.

**A "keep asking why" tool for learners**: drill down any branch as deep as you want, skip what you already know — the agent helps you focus only on your knowledge gaps, mastering a domain both thoroughly and efficiently.

## Quick Start

### Linux one-line deployment

```bash
# Get install.sh and the release package (see Releases), then:
bash install.sh comind--linux-x64-<version>.tar.gz
```

The script will:
1. Extract to `~/.comind/app` (auto-backup the old version)
2. Install the AI assistant runtime: downloads node from nodejs.org and pi from npm if missing
3. Register a systemd daemon (system-level for root, user-level + linger otherwise)
4. Print the access URL; open it in your browser

> Update: `bash update.sh` (compare version → download → sha256 → backup → replace → restart)
> Rollback: `~/.comind/backup/app.prev` keeps the previous version

### Windows release package

Download from Releases. **Normal users: `CoMind-user-<version>-win-x64.zip`**; **developers/deployers: `CoMind-windows-dev-<version>.zip`**.

**Normal users (no Python / Node / git required)**:

1. Unzip the package (a short path like `C:\CoMind` or the Desktop is recommended)
2. Double-click `Start CoMind.cmd`
3. The browser should open `http://127.0.0.1:8789`
4. Open model settings in the AI panel (⚙️) and save your DeepSeek or Kimi API key

Double-click `Stop CoMind.cmd` to stop. If SmartScreen warns about an unsigned app, confirm the package came from official Releases, then click "More info" → "Run anyway".

**Developers / deployers**:

```powershell
cd CoMind-windows-dev-<version>
.\install.cmd        # first install or rebuild dependencies
.\start.cmd          # start CoMind
```

Daily commands: `start.cmd` / `stop.cmd` / `status.cmd` / `test.cmd`; autostart after login via `service-install.cmd` (remove with `service-uninstall.cmd`).

> Full usage notes: [WINDOWS_QUICKSTART.md](WINDOWS_QUICKSTART.md).

## Architecture

```
Browser (Vue + simple-mind-map + AI panel)
        │  HTTP / SSE
        ▼
FastAPI backend (backend.py + chat_service.py)
        │  spawn subprocess (RPC)
        ▼
pi Agent (AI assistant runtime, node)
        │  extension tools (mindmap-tools.ts)
        ▼
Mind map state (get_mindmap / get_mindmap_diff / get_subtree / update_mindmap)
```

- **Frontend**: based on open-source [simple-mind-map](https://github.com/wanglin2/mind-map) (MIT), with a self-built AI panel, model settings, and file manager
- **Backend**: FastAPI, self-built mind map state sync / diff / session management (pi subprocess pool)
- **Agent**: [pi-coding-agent](https://www.npmjs.com/package/@earendil-works/pi-coding-agent) (MIT) runs as an RPC subprocess and reads/writes the mind map via extension tools

## Configure Model Keys

1. Open the page, click ⚙️ in the AI panel header
2. Paste a DeepSeek / Kimi API key and hit Save
3. Takes effect immediately (keys are stored locally in Windows `%LOCALAPPDATA%\CoMind\private\keys.json` / Linux `~/.comind/private/keys.json`, never uploaded)

## Feature Overview

| Capability | Description |
|---|---|
| 🧭 Big picture | Supports mind maps with hundreds of nodes; progressive disclosure (skeleton → branch → subtree) lets you see the forest before the trees; nodes carry ✅/⚠️ status, notes, links |
| 🎓 Learning powerhouse | **Keep asking why**: one hotkey on a question node, and the AI structures the answer into a clean knowledge tree right in the map; skip branches you already know, go straight to your gaps, learn a domain efficiently |
| 🛠 One agent, four roles | **Research / investigate / search / execute** in one: run experiments or study specified materials while learning; view, edit, compile, and test code while developing — spanning the whole product & learning chain |
| 🤖 Precise agent collaboration | Press a hotkey on any node to ask the AI for help (NODE_ASSIST); the AI understands context, researches, and **updates the mind map incrementally** (only the changed nodes, never the whole tree); structured tools (diff/subtree/update) guarantee precise operations |
| 🔑 Bring your own model | Configure DeepSeek / Kimi API keys in a frontend "Model Settings" panel — saved locally, effective immediately, no code changes |
| 🚀 One-click deployment | Binary release packages (no Python/git required) + systemd daemon; `install.sh` sets everything up; `update.sh` upgrades with sha256 verification and rollback |
| 🌍 i18n | Frontend ships with zh-CN / zh-TW / en-US / vi-VN; the AI assistant replies in your UI language |

## Source Deployment

### Linux

```bash
# Backend (Python 3.12+)
pip install fastapi uvicorn pydantic
python3 backend.py          # default 0.0.0.0:8789

# Frontend (Node 20+)
cd web && npm install
npm run serve               # dev hot reload
npm run build               # output to ../dist, copy.js syncs index.html

# Tests
pytest                      # mind map diff/apply/ops + pi workflow
```

**AI assistant dependency**: requires the `pi` command (`npm i -g @earendil-works/pi-coding-agent`), or set the `PI_BIN` env var.

### Windows source deployment

```powershell
# Run from the repository root
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

You can also double-click [install.cmd](install.cmd) or run [update.cmd](update.cmd). On Windows, the script will:
1. Create `.venv` and install backend dependencies
2. Install or reuse Node.js, then install `pi-coding-agent`
3. Build the frontend into `dist/` and sync the root `index.html`
4. Start the backend and write logs to `%LOCALAPPDATA%\CoMind\comind.log`

For daily startup, double-click [start.cmd](start.cmd); to stop the backend, double-click [stop.cmd](stop.cmd). To start CoMind automatically after Windows login, run [service-install.cmd](service-install.cmd); to remove autostart, run [service-uninstall.cmd](service-uninstall.cmd). To inspect the current state, run [status.cmd](status.cmd). To run a local smoke test, run [test.cmd](test.cmd).

The Windows source deployment stores runtime data in `%LOCALAPPDATA%\CoMind`:
- `private\keys.json`: model API keys
- `chat-sessions\`: AI sessions
- `pi-runtime\`: CoMind-managed pi coding agent runtime
- `comind.log` / `comind.pid`: logs and process id

If `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` points at a disabled placeholder such as `127.0.0.1:9`, the backend does not pass it to pi by default so AI calls do not fail with `Connection error`. Set `SMM_PI_USE_SYSTEM_PROXY=1` before startup if pi should use the system proxy.

For updates, rerun [install.ps1](install.ps1) or [update.ps1](update.ps1); the script stops the previous process, rebuilds, and restarts the app.

### Windows release packaging

Maintainers can run [package-windows.cmd](package-windows.cmd) to create two Windows package types:

- `CoMind-user-<version>-win-x64.zip`: for nontechnical users. Unzip it and double-click `Start CoMind.cmd`. It includes `CoMind.exe`, frontend assets, portable Node.js, and the pi runtime, so users do not need Python / git / Node installed.
- `CoMind-windows-dev-<version>.zip`: for Windows developers or deployers. It keeps the source tree and one-click scripts; unzip it and run `install.cmd`.

See [WINDOWS_QUICKSTART.md](WINDOWS_QUICKSTART.md) for package usage. See [WINDOWS_PACKAGING.md](WINDOWS_PACKAGING.md) for build prerequisites, commands, and offline Node zip usage.

## Data & Privacy

| Data | Location | Notes |
|---|---|---|
| Mind maps | `~/comind-maps/` (Windows `%USERPROFILE%\comind-maps`) | outside the program dir; upgrades never touch it |
| Model keys | `~/.comind/private/keys.json` (Windows `%LOCALAPPDATA%\CoMind\private\keys.json`) | local only |
| Chat sessions | `~/.comind/chat-sessions/` (Windows `%LOCALAPPDATA%\CoMind\chat-sessions`) | local only |

## FAQ

**Q: Do I need a GPU?** No. AI inference uses DeepSeek/Kimi cloud APIs.

**Q: Windows/macOS?** Windows has release packages (normal user + developer) and a source deployment path; macOS can still run from source.

**Q: Is my mind map data safe?** Everything is stored locally; the AI only reads/writes the map you authorize via extension tools.

## License

[MIT](./LICENSE) © 2026 CoMind Contributors. Frontend based on [simple-mind-map](https://github.com/wanglin2/mind-map) (MIT), agent based on [pi-coding-agent](https://www.npmjs.com/package/@earendil-works/pi-coding-agent) (MIT). Thanks to the upstream open-source community.
