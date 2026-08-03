<h1 align="center">CoMind · AI 协作脑图</h1>

<p align="center">
  <b>把握全局，精准地与 AI Agent 协作</b><br>
  <a href="./README_EN.md">English</a> | 中文
</p>

CoMind 是一个「AI 助理 + 思维导图」一体的协作工具。思维导图是**可视化载体**——让你在一张图上把握全局（项目全貌、进度状态、待办事项）；AI 助理是**核心**——通过深度集成的 Agent 帮你思考、整理、调研、直接改图，让"想清楚"和"做出来"无缝衔接。

**学习者的"打破砂锅问到底"利器**：沿着任意分支不断追问下去，已经懂的知识直接跳过——Agent 帮你只啃知识盲区，全面而高效地吃透一个领域。

## 核心理念

| 能力 | 说明 |
|---|---|
| 🧭 把握全局 | 支持数百节点的超大脑图；渐进式披露（骨架 → 分支 → 子树）让你先看全貌再看细节；节点带 ✅/⚠️ 状态标记、备注、链接，一图掌握项目进展 |
| 🎓 学习利器 | **打破砂锅问到底**：对问题节点一键求助，AI 把答案结构化成优雅的子节点树沉淀进脑图；已懂的分支跳过不重复，直达盲区，高效学完一个领域 |
| 🛠 Agent 四位一体 | **研究 / 调研 / 搜索 / 执行**集于一身：学习时可做实验、按指定材料学；开发时可直接查看、编辑、编译、测试——贯穿整条产品与学习链条 |
| 🤖 精准 Agent 协作 | 在任意节点上按快捷键向 AI 求助（NODE_ASSIST），AI 能读懂上下文、调研资料、**直接增量更新脑图**（只发改动，不动整树）；结构化工具（diff/subtree/update）保证精确操作 |
| 🔑 模型自由 | 前端「模型设置」面板直接配置 DeepSeek / Kimi 等 provider 的 key，保存在本机，立即生效，无需改代码 |
| 🚀 一键部署 | 二进制发布包（免 Python、免 git）+ systemd 守护，`install.sh` 一行装好；`update.sh` 一键升级（sha256 校验 + 旧版回滚） |
| 🌍 国际化 | 前端内置 中/英/繁中/越南语；AI 助理回复跟随界面语言 |

## Agent 能做什么

Agent 不只是聊天——它是集**研究、调研、搜索、执行**四位一体的完整协作者，贯穿你的学习与开发链条：

**🎓 学习场景**
- **打破砂锅问到底**：沿分支不断追问，AI 结合上下文回答，并把知识结构化写入脑图——不懂的节点自动变成清晰的知识树
- **跳过已懂知识**：只针对盲区节点深入，不被重复内容拖慢，全面高效地掌握一个领域
- **做实验 / 学指定材料**：让 Agent 跑代码验证理解、按你指定的资料学习并整理成脑图

**🛠 开发场景**
- **完整的开发工具链**：Agent 能直接查看、编辑、编译、测试代码——不只是给建议，而是真正动手做
- **调研落地**：web 调研 → 结论直接写进脑图对应分支，研究结果即沉淀

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

## 快速开始（Linux 一键部署）

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

## 配置模型 Key

1. 打开网页，点击 AI 助理面板右上角 ⚙️
2. 粘贴 DeepSeek / Kimi 的 API key，点「保存」
3. 立即生效（key 仅保存在本机 `~/.comind/private/keys.json`，权限 600，不会上传）

## 开发指南

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

## 数据与隐私

| 数据 | 位置 | 说明 |
|---|---|---|
| 脑图文件 | `~/comind-maps/` | 程序目录外，升级不动 |
| 模型 key | `~/.comind/private/keys.json` | 仅本机，权限 600 |
| 会话记录 | `~/.comind/chat-sessions/` | 仅本机 |

## 常见问题

**Q: 需要 GPU 吗？** 不需要。AI 推理走 DeepSeek/Kimi 的云端 API。

**Q: 支持 Windows/macOS？** 当前发布包面向 Linux x64；Windows/macOS 可跑源码（后端 + 前端构建）。

**Q: 脑图数据安全吗？** 所有数据存本机，AI 只通过扩展工具读写你授权的脑图。

## 许可证

[MIT](./LICENSE) © 2026 CoMind Contributors。前端基于 [simple-mind-map](https://github.com/wanglin2/mind-map)（MIT），Agent 基于 [pi-coding-agent](https://www.npmjs.com/package/@earendil-works/pi-coding-agent)（MIT），感谢上游开源社区。
