# Contributing to CoMind

感谢你愿意为 CoMind 贡献力量！以下是参与指南（中英双语）。

Thanks for your interest in contributing to CoMind! Here's how to get involved.

## 快速开始 / Getting Started

1. Fork 本仓库并 clone 到本地（Fork the repo and clone it locally）
2. 按 [README](./README.md#开发指南) 搭建开发环境（Set up the dev environment per README）
3. 创建特性分支：`git checkout -b feat/your-feature`
4. 开发并确保 `pytest` 全部通过（keep `pytest` green）
5. 提交 PR，描述清楚改动与测试情况（open a PR with a clear description）

## 开发约定 / Conventions

- 后端：Python 3.12+，FastAPI；改动需配套测试（`tests/`）
- 前端：Vue 2 + vue-i18n；新文案必须加进 `web/src/lang/` 的全部语言文件
- AI 面板（`ai-assistant/`）：独立于 Vue 应用，文案走面板内 i18n 字典
- 部署脚本（`install.sh` / `update.sh` / `scripts/release.sh`）：改动需在干净 Linux 上验证
- 提交信息用英文，描述改动意图（commit messages in English, describe intent）

## 国际化的要求 / i18n Requirement

本项目定位面向全球用户。**任何面向用户的新文案都必须提供中英双语**：

- 前端组件 → 添加到 `web/src/lang/zh_cn.js` 与 `en_us.js`（及 zh_tw/vi_vn，若方便）
- AI 面板 → 添加到 `ai-assistant/` 的 i18n 字典
- README / 文档 → 同时更新 `README.md` 与 `README_EN.md`

Any user-facing copy must ship with both zh-CN and en-US.

## 报告问题 / Reporting Issues

请包含：环境（OS/浏览器）、复现步骤、期望行为与实际行为、相关日志。

Include: environment (OS/browser), steps to reproduce, expected vs actual behavior, and relevant logs.

## 行为准则 / Code of Conduct

友善、尊重、建设性。Be kind, respectful, and constructive.

## 感谢 / Thanks

你的每一份贡献都在让「人机协作思考」变得更好。Every contribution makes human-AI collaborative thinking better.
