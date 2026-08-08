<h1 align="center">CoMind · AI 协作脑图</h1>

<p align="center">
  <b>掌控全局，精准沟通</b><br>
  <a href="./README_EN.md">English</a> | 中文
</p>

CoMind 是一个让 AI Agent 直接在思维导图上工作的协作工具。你在脑图的任意节点按快捷键向 AI 求助，Agent 会读懂上下文，调研、思考，然后**直接把结果写进脑图**——增加子节点、更新内容、整理结构——不是给你一段文字让你自己整理，而是精准地改图。

**学习场景**：沿着任意分支不断追问下去，Agent 把答案结构化成子节点树沉淀进脑图。已经懂的知识直接跳过，只啃盲区——全面高效地吃透一个领域。需要验证时，Agent 还能跑代码做实验。

**开发场景**：Agent 能直接查看、编辑、编译、测试代码。web 调研的结论直接写进脑图对应分支——研究即沉淀，不用手动搬运。

## 快速开始

### Linux 一键部署

```bash
# 下载 install.sh 与发布包（见 Releases），然后：
bash install.sh comind--linux-x64-<版本>.tar.gz
```

脚本会自动：
1. 解压到 `~/.comind/app`（旧版自动备份）
2. 安装 AI 助理运行时：检测不到 node 时自动从 nodejs.org 下载，并从 npm 安装 pi
3. 注册 systemd 守护（root 装系统级，普通用户装用户级 + linger）
4. 启动后打印访问地址，浏览器打开即可使用

> 更新：`bash update.sh`（对比版本 → 下载 → sha256 校验 → 备份 → 覆盖 → 重启）
> 回滚：`~/.comind/backup/app.prev` 保留上一版，手动恢复即可

### Windows 发布包

从 Releases 下载对应包，**普通用户选 `CoMind-user-<版本>-win-x64.zip`**，**开发者/部署者选 `CoMind-windows-dev-<版本>.zip`**。

**普通用户（免 Python / Node / git）**：

1. 解压 zip（建议放短路径，如 `C:\CoMind` 或桌面）
2. 双击 `Start CoMind.cmd`
3. 浏览器自动打开 `http://127.0.0.1:8789`
4. 在 AI 助理面板 ⚙️ 里粘贴 DeepSeek / Kimi API key 并保存

用完双击 `Stop CoMind.cmd` 停止。SmartScreen 提示未签名时，确认包来自官方 Releases 后选「更多信息 → 仍要运行」。

**开发者 / 部署者**：

```powershell
cd CoMind-windows-dev-<版本>
.\install.cmd        # 首次安装或重建依赖
.\start.cmd          # 启动
```

日常命令：`start.cmd` / `stop.cmd` / `status.cmd` / `test.cmd`；登录自启动 `service-install.cmd`（取消 `service-uninstall.cmd`）。

> 详细使用说明见 [WINDOWS_QUICKSTART.md](WINDOWS_QUICKSTART.md)。

## 架构

```
浏览器 (Vue + simple-mind-map + AI 面板)
        │  HTTP / SSE
        ▼
FastAPI 后端 (backend.py + chat_service.py)
        │  spawn 子进程 (RPC)
        ▼
pi Agent (AI 助理运行时，node)
        │  扩展工具 (mindmap-tools.ts)
        ▼
脑图状态 (get_mindmap / get_mindmap_diff / get_subtree / update_mindmap)
```

- **前端**：基于开源 [simple-mind-map](https://github.com/wanglin2/mind-map)（MIT），自研 AI 助理面板、模型设置、文件管理
- **后端**：FastAPI，自研脑图状态同步 / 差异计算 / 会话管理（pi 子进程池）
- **Agent**：[pi-coding-agent](https://www.npmjs.com/package/@earendil-works/pi-coding-agent)（MIT）以 RPC 子进程方式运行，通过扩展工具直接读写脑图

## 配置模型 Key

1. 打开网页，点击 AI 助理面板右上角 ⚙️
2. 粘贴 DeepSeek / Kimi 的 API key，点「保存」
3. 立即生效（key 仅保存在本机 Windows `%LOCALAPPDATA%\CoMind\private\keys.json` / Linux `~/.comind/private/keys.json`，不会上传）

## 特性一览

| 能力 | 说明 |
|---|---|
| 🧭 把握全局 | 支持数百节点的超大脑图；渐进式披露（骨架 → 分支 → 子树）让你先看全貌再看细节；节点带 ✅/⚠️ 状态标记、备注、链接，一图掌握项目进展 |
| 🎓 学习利器 | **打破砂锅问到底**：对问题节点一键求助，AI 把答案结构化成优雅的子节点树沉淀进脑图；已懂的分支跳过不重复，直达盲区，高效学完一个领域 |
| 🛠 Agent 四位一体 | **研究 / 调研 / 搜索 / 执行**集于一身：学习时可做实验、按指定材料学；开发时可直接查看、编辑、编译、测试——贯穿整条产品与学习链条 |
| 🤖 精准 Agent 协作 | 在任意节点上按快捷键向 AI 求助（NODE_ASSIST），AI 能读懂上下文、调研资料、**直接增量更新脑图**（只发改动，不动整树）；结构化工具（diff/subtree/update）保证精确操作 |
| 🔑 模型自由 | 前端「模型设置」面板直接配置 DeepSeek / Kimi 等 provider 的 key，保存在本机，立即生效，无需改代码 |
| 🚀 一键部署 | 二进制发布包（免 Python、免 git）+ systemd 守护，`install.sh` 一行装好；`update.sh` 一键升级（sha256 校验 + 旧版回滚） |
| 🌍 国际化 | 前端内置 中/英/繁中/越南语；AI 助理回复跟随界面语言 |

## 源码部署

### Linux

```bash
# 后端（Python 3.12+）
pip install fastapi uvicorn pydantic
python3 backend.py          # 默认 0.0.0.0:8789

# 前端（Node 20+）
cd web && npm install
npm run serve               # 开发热更新
npm run build               # 构建产物到 ../dist，copy.js 同步 index.html

# 测试
pytest                      # 脑图 diff/apply/ops + pi 工作流
```

**AI 助理依赖**：需要 `pi` 命令（`npm i -g @earendil-works/pi-coding-agent`），或设置 `PI_BIN` 环境变量指定路径。

### Windows 源码部署

```powershell
# 在仓库根目录执行
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

也可以直接双击 [install.cmd](install.cmd) 或运行 [update.cmd](update.cmd)。Windows 方案会自动完成这些事：
1. 创建 `.venv` 并安装后端依赖
2. 安装或复用 Node.js，然后安装 `pi-coding-agent`
3. 构建前端到 `dist/`，并同步根目录 `index.html`
4. 启动后端并把日志写到 `%LOCALAPPDATA%\CoMind\comind.log`

日常启动可以直接双击 [start.cmd](start.cmd)，停止服务可以双击 [stop.cmd](stop.cmd)。想要登录 Windows 后自动后台启动，可以运行 [service-install.cmd](service-install.cmd)；取消自启动运行 [service-uninstall.cmd](service-uninstall.cmd)。查看运行状态运行 [status.cmd](status.cmd)，做一次本机冒烟测试运行 [test.cmd](test.cmd)。

Windows 版会把运行数据放在 `%LOCALAPPDATA%\CoMind`：
- `private\keys.json`：模型 API key
- `chat-sessions\`：AI 会话
- `pi-runtime\`：CoMind 自己管理的 pi coding agent
- `comind.log` / `comind.pid`：日志和进程号

如果环境里有 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` 指向 `127.0.0.1:9` 这类失效占位代理，后端会默认不传给 pi，避免 AI 返回 `Connection error`。确实需要让 pi 使用系统代理时，启动前设置 `SMM_PI_USE_SYSTEM_PROXY=1`。

更新时重新执行 [install.ps1](install.ps1) 或 [update.ps1](update.ps1)，脚本会先停掉旧进程再重新构建并启动。

### Windows 发布打包

维护者可以运行 [package-windows.cmd](package-windows.cmd) 生成两类 Windows 包：

- `CoMind-user-<version>-win-x64.zip`：给普通用户，解压后双击 `Start CoMind.cmd` 即可使用，内置 `CoMind.exe`、前端资源、便携 Node 和 pi runtime，不要求用户安装 Python / git / Node。
- `CoMind-windows-dev-<version>.zip`：给 Windows 开发者或部署者，保留源码和一键脚本，解压后运行 `install.cmd` 即可部署。

Windows 包的使用入口见 [WINDOWS_QUICKSTART.md](WINDOWS_QUICKSTART.md)。详细构建说明、前置要求和离线 Node 包用法见 [WINDOWS_PACKAGING.md](WINDOWS_PACKAGING.md)。

## 数据与隐私

| 数据 | 位置 | 说明 |
|---|---|---|
| 脑图文件 | `~/comind-maps/`（Windows `%USERPROFILE%\comind-maps`） | 程序目录外，升级不动 |
| 模型 key | `~/.comind/private/keys.json`（Windows `%LOCALAPPDATA%\CoMind\private\keys.json`） | 仅本机，权限 600 |
| 会话记录 | `~/.comind/chat-sessions/`（Windows `%LOCALAPPDATA%\CoMind\chat-sessions`） | 仅本机 |

## 常见问题

**Q: 需要 GPU 吗？** 不需要。AI 推理走 DeepSeek/Kimi 的云端 API。

**Q: 支持 Windows/macOS？** Windows 有发布包（普通用户版 + 开发者版）和源码部署两种方式；macOS 仍建议按源码方式运行。

**Q: 脑图数据安全吗？** 所有数据存本机，AI 只通过扩展工具读写你授权的脑图。

## 许可证

[MIT](./LICENSE) © 2026 CoMind Contributors。前端基于 [simple-mind-map](https://github.com/wanglin2/mind-map)（MIT），Agent 基于 [pi-coding-agent](https://www.npmjs.com/package/@earendil-works/pi-coding-agent)（MIT），感谢上游开源社区。
