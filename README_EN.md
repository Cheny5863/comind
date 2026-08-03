<h1 align="center">CoMind · AI Collaborative Mind Map</h1>

<p align="center">
  <b>See the big picture. Collaborate precisely with AI agents.</b><br>
  <a href="./README.md">中文</a> | English
</p>

CoMind is an all-in-one tool combining an **AI assistant** with a **mind map**. The mind map is the *visual canvas* — see the whole picture at a glance (project overview, progress status, todos); the AI assistant is the *core* — a deeply integrated agent that thinks with you, researches, organizes, and edits the map directly, making "thinking clearly" and "getting it done" seamless.

**A "keep asking why" tool for learners**: drill down any branch as deep as you want, skip what you already know — the agent helps you focus only on your knowledge gaps, mastering a domain both thoroughly and efficiently.

## Core Ideas

| Capability | Description |
|---|---|
| 🧭 Big picture | Supports mind maps with hundreds of nodes; progressive disclosure (skeleton → branch → subtree) lets you see the forest before the trees; nodes carry ✅/⚠️ status, notes, links |
| 🎓 Learning powerhouse | **Keep asking why**: one hotkey on a question node, and the AI structures the answer into a clean knowledge tree right in the map; skip branches you already know, go straight to your gaps, learn a domain efficiently |
| 🛠 One agent, four roles | **Research / investigate / search / execute** in one: run experiments or study specified materials while learning; view, edit, compile, and test code while developing — spanning the whole product & learning chain |
| 🤖 Precise agent collaboration | Press a hotkey on any node to ask the AI for help (NODE_ASSIST); the AI understands context, researches, and **updates the mind map incrementally** (only the changed nodes, never the whole tree); structured tools (diff/subtree/update) guarantee precise operations |
| 🔑 Bring your own model | Configure DeepSeek / Kimi API keys in a frontend "Model Settings" panel — saved locally, effective immediately, no code changes |
| 🚀 One-click deployment | Binary release packages (no Python/git required) + systemd daemon; `install.sh` sets everything up; `update.sh` upgrades with sha256 verification and rollback |
| 🌍 i18n | Frontend ships with zh-CN / zh-TW / en-US / vi-VN; the AI assistant replies in your UI language |

## What the Agent Can Do

The agent is not just chat — it's a full collaborator combining **research, investigation, search, and execution**, spanning your learning and development chain:

**🎓 Learning**
- **Keep asking why**: drill down any branch, the AI answers in context and writes structured knowledge into the map — an unclear node becomes a clear knowledge tree
- **Skip what you know**: focus only on gaps, never slowed down by repetition — master a domain thoroughly and efficiently
- **Run experiments / study given materials**: have the agent execute code to verify understanding, or learn from materials you specify and organize them into the map

**🛠 Development**
- **A complete toolchain**: the agent can view, edit, compile, and test code directly — it acts, not just advises
- **Research that lands**: web research → conclusions written straight into the matching branch, insights become knowledge

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

## Quick Start (Linux one-line deployment)

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

## Configure Model Keys

1. Open the page, click ⚙️ in the AI panel header
2. Paste a DeepSeek / Kimi API key and hit Save
3. Takes effect immediately (keys are stored locally in `~/.comind/private/keys.json`, mode 600, never uploaded)

## Development

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

## Data & Privacy

| Data | Location | Notes |
|---|---|---|
| Mind maps | `~/comind-maps/` | outside the program dir; upgrades never touch it |
| Model keys | `~/.comind/private/keys.json` | local only, mode 600 |
| Chat sessions | `~/.comind/chat-sessions/` | local only |

## FAQ

**Q: Do I need a GPU?** No. AI inference uses DeepSeek/Kimi cloud APIs.

**Q: Windows/macOS?** Releases target Linux x64; source runs on Windows/macOS (backend + frontend build).

**Q: Is my mind map data safe?** Everything is stored locally; the AI only reads/writes the map you authorize via extension tools.

## License

[MIT](./LICENSE) © 2026 CoMind Contributors. Frontend based on [simple-mind-map](https://github.com/wanglin2/mind-map) (MIT), agent based on [pi-coding-agent](https://www.npmjs.com/package/@earendil-works/pi-coding-agent) (MIT). Thanks to the upstream open-source community.
