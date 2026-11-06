import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const API_BASE = process.env.SMM_API_BASE || "http://localhost:8789";
const MAP_KEY = process.env.MAP_KEY || "";
const BRANCH_UID = process.env.BRANCH_UID || "";
const K = encodeURIComponent(MAP_KEY);
const B = encodeURIComponent(BRANCH_UID);

async function apiFetch(path: string, init?: RequestInit): Promise<string> {
  const url = `${API_BASE}${path}`;
  const resp = await fetch(url, { signal: AbortSignal.timeout(30000), ...init });
  if (!resp.ok) {
    return JSON.stringify({ error: `HTTP ${resp.status}`, url, body: await resp.text().catch(() => "") });
  }
  return resp.text();
}

function truncate(text: string, maxLen: number = 8000): string {
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen) + `\n...[truncated, ${text.length - maxLen} chars omitted]`;
}

function stripHtml(html: string): string {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&")
    .replace(/[ \t]+/g, " ")
    .replace(/\n\s*\n+/g, "\n")
    .trim();
}

export default function (pi: ExtensionAPI) {
  // Tool 1: get_mindmap — outline skeleton (progressive disclosure entry)
  pi.registerTool({
    name: "get_mindmap",
    label: "Get Mindmap Outline",
    description:
      "获取脑图的骨架结构：全部节点（含 #uid），文本截断到 60 字符、备注只显示〔含备注〕标记。修改脑图前先调本工具拿最新 uid。若你绑定分支，返回的只是你分支（分支根及其子孙）的骨架；需要分支内某节点完整文本/备注时调 get_subtree(uid)。",
    parameters: Type.Object({}),
    async execute() {
      const raw = await apiFetch(`/api/mindmap/outline?key=${K}&branch=${B}`);
      return { content: [{ type: "text" as const, text: truncate(raw, 16000) }], details: {} };
    },
  });

  // Tool 2: get_subtree — full subtree detail with breadcrumb path
  pi.registerTool({
    name: "get_subtree",
    label: "Get Subtree Detail",
    description:
      "获取指定节点（uid）的完整子树：面包屑路径 + 完整文本和备注（不截断）。若你绑定分支，uid 必须是你分支内的节点（分支根或其后代），分支外的 uid 会被拒绝。脑图很大时先用 get_mindmap 拿骨架定位，再调本工具展开目标分支细节。",
    parameters: Type.Object({
      uid: Type.String({ description: "目标节点 uid（来自 get_mindmap / get_mindmap_diff 的 #uid，须在你的分支内）" }),
    }),
    async execute(_toolCallId: string, params: { uid: string }) {
      const raw = await apiFetch(`/api/mindmap/subtree?key=${K}&uid=${encodeURIComponent(params.uid)}&branch=${B}`);
      return { content: [{ type: "text" as const, text: truncate(raw, 16000) }], details: {} };
    },
  });

  // Tool 3: get_mindmap_diff — incremental changes since last sync
  pi.registerTool({
    name: "get_mindmap_diff",
    label: "Get Mindmap Diff",
    description:
      "获取脑图自上次同步以来的增量变化（新增/删除/修改的节点列表）。每次收到用户消息后先调它跟上用户思路；首轮返回骨架（结构完整、文本截断，细节用 get_subtree）。",
    parameters: Type.Object({}),
    async execute() {
      const raw = await apiFetch(`/api/mindmap/diff?key=${K}&branch=${B}`);
      return { content: [{ type: "text" as const, text: truncate(raw, 16000) }], details: {} };
    },
  });

  // Tool 3: get_node_image — 看节点图片（多模态，返回真实图片给模型）
  pi.registerTool({
    name: "get_node_image",
    label: "Get Node Image",
    description:
      "读取某个节点上的图片，直接把图片内容返回给你看（多模态）。get_mindmap / get_subtree 里标了〔含图片〕的节点才用；uid 必须在你的分支内。图片很大时只用于理解内容，不要尝试把 base64 抄进文本。",
    parameters: Type.Object({
      uid: Type.String({ description: "目标节点 uid（来自 get_mindmap / get_subtree / get_mindmap_diff）" }),
    }),
    async execute(_toolCallId: string, params: { uid: string }) {
      const raw = await apiFetch(
        `/api/mindmap/node_image?key=${K}&uid=${encodeURIComponent(params.uid)}&branch=${B}`
      );
      let res: any = null;
      try {
        res = JSON.parse(raw);
      } catch (_) {}
      if (!res || res.error) {
        return {
          content: [{ type: "text" as const, text: `读取节点图片失败：${res?.error || raw}` }],
          details: {},
          isError: true,
        };
      }
      return {
        content: [
          {
            type: "text" as const,
            text: `节点「${res.text || res.uid}」的图片（${res.mime}，${res.width}x${res.height}）`,
          },
          { type: "image" as const, data: res.data, mimeType: res.mime },
        ],
        details: {},
      };
    },
  });

  // Tool 4: get_background — user-written background info for this map
  pi.registerTool({
    name: "get_background",
    label: "Get Background",
    description: "读取用户为该脑图编写的背景信息，帮助理解脑图主题和上下文。",
    parameters: Type.Object({}),
    async execute() {
      const raw = await apiFetch(`/api/background?name=${K}`);
      return { content: [{ type: "text" as const, text: truncate(raw, 4000) }], details: {} };
    },
  });

  // Tool 4: update_mindmap — incremental ops (PREFERRED for small edits)
  pi.registerTool({
    name: "update_mindmap",
    label: "Update Mindmap",
    description:
      "增量修改脑图（首选）。ops 数组：" +
      "{action:'update_text',uid,text} 改节点文本；" +
      "{action:'add',parent_uid|parent_ref,text,children?,index?,ref?,image?,note?} 新增子节点/子树，ref 给新节点起临时名供同批次后续 op 引用，image 可直接给新节点配图；" +
      "{action:'set_image',uid,source,imageTitle?} 给节点加/换/删图片（source 为空 = 删图）；" +
      "{action:'delete',uid} 删除节点；" +
      "{action:'move',uid,new_parent_uid|new_parent_ref,index?} 移动节点。" +
      "小改动只发对应 op，不要给整棵树。" +
      "图片：source 可以是本地文件绝对路径（如 /tmp/chart.png）、http(s) URL 或 data URL；" +
      "支持 png/jpeg/gif/webp/bmp，单张 ≤8MB。要画图就先用 bash 跑脚本（matplotlib 可用）生成 png 再 set_image，不要手写 base64。" +
      "⚠️ 若你绑定分支，只能操作你分支内的节点（分支根及其子孙），涉及分支外节点会被后端拒绝；且不能删除/移动你的分支根节点。",
    parameters: Type.Object({
      ops: Type.Array(Type.Object({
        action: Type.String({ description: "update_text / add / delete / move / set_image" }),
        uid: Type.Optional(Type.String({ description: "目标节点 uid（update_text/delete/move/set_image 必填）" })),
        parent_uid: Type.Optional(Type.String({ description: "父节点 uid（add 时与 parent_ref 二选一）" })),
        parent_ref: Type.Optional(Type.String({ description: "同批次前面 add 的 ref（add 时与 parent_uid 二选一）" })),
        new_parent_uid: Type.Optional(Type.String({ description: "新父节点 uid（move 必填）" })),
        new_parent_ref: Type.Optional(Type.String({ description: "新父节点 ref（move 时替代 new_parent_uid）" })),
        text: Type.Optional(Type.String({ description: "节点文本（update_text/add 必填）" })),
        index: Type.Optional(Type.Number({ description: "插入位置（add/move 可选，默认末尾）" })),
        ref: Type.Optional(Type.String({ description: "给新节点起临时名（add 可选），同批次可引用" })),
        image: Type.Optional(Type.String({ description: "图片源（add 可选）：本地路径 / http(s) URL / data URL" })),
        source: Type.Optional(Type.String({ description: "图片源（set_image 必填，空串 = 删除图片）：本地路径 / http(s) URL / data URL" })),
        imageTitle: Type.Optional(Type.String({ description: "图片悬停标题（可选）" })),
        note: Type.Optional(Type.String({ description: "节点备注 HTML（add 可选）" })),
        children: Type.Optional(Type.Array(Type.Object({}, { additionalProperties: true }), { description: "子树（add 可选），递归 {text, children, image?}" })),
      })),
    }),
    async execute(_toolCallId: string, params: { ops: any[] }) {
      const raw = await apiFetch(`/api/mindmap/apply_ops`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: MAP_KEY, ops: params.ops, branch_uid: BRANCH_UID }),
      });
      // 解析结果，将 skipped_human_editing 转为人类可读的提示
      let text = truncate(raw, 2000);
      try {
        const result = JSON.parse(raw);
        if (result.skipped_human_editing && result.skipped_human_editing.length) {
          text += "\n⚠️ 以下节点正在被用户编辑，已跳过：" + result.skipped_human_editing.join(", ") + "。请稍后重试这些节点的修改。";
        }
      } catch (_) {}
      const isErr = raw.includes('"error"');
      return { content: [{ type: "text" as const, text }], details: {}, isError: isErr };
    },
  });

  // Tool 5: replace_mindmap — full tree replacement (ONLY for large restructures)
  pi.registerTool({
    name: "replace_mindmap",
    label: "Replace Mindmap",
    description:
      "整树替换脑图（仅在大范围重构时使用，小改动请用 update_mindmap）。root 必须是完整新树根节点；未改动节点的 uid 必须原样保留，其余字段系统会自动保留。⚠️ 若你绑定分支，此工具不可用（后端会拒绝整树替换）；大范围重构请分多次 update_mindmap 增量完成。",
    parameters: Type.Object({
      root: Type.Object({}, { additionalProperties: true, description: "完整的新树根节点 {data:{text,...}, children:[...]}" }),
    }),
    async execute(_toolCallId: string, params: { root: any }) {
      const raw = await apiFetch(`/api/mindmap/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: MAP_KEY, tree: params.root, branch_uid: BRANCH_UID }),
      });
      const isErr = raw.includes('"error"');
      return { content: [{ type: "text" as const, text: truncate(raw, 2000) }], details: {}, isError: isErr };
    },
  });

  // Tool 5: web_fetch — fetch a URL and return readable text
  pi.registerTool({
    name: "web_fetch",
    label: "Web Fetch",
    description:
      "抓取指定 URL 的网页内容并返回纯文本（去 HTML 标签，截断 8000 字符）。用于调研时阅读文档、博客、GitHub 页面等。",
    parameters: Type.Object({
      url: Type.String({ description: "要抓取的完整 URL（http/https）" }),
    }),
    async execute(_toolCallId: string, params: { url: string }) {
      try {
        const resp = await fetch(params.url, {
          signal: AbortSignal.timeout(20000),
          headers: { "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) mindmap-assistant" },
        });
        if (!resp.ok) {
          return {
            content: [{ type: "text" as const, text: JSON.stringify({ error: `HTTP ${resp.status}`, url: params.url }) }],
            details: {},
            isError: true,
          };
        }
        const html = await resp.text();
        return { content: [{ type: "text" as const, text: truncate(stripHtml(html)) }], details: {} };
      } catch (e: any) {
        return {
          content: [{ type: "text" as const, text: JSON.stringify({ error: String(e?.message || e), url: params.url }) }],
          details: {},
          isError: true,
        };
      }
    },
  });
}
