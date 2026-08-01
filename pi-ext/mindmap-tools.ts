import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const API_BASE = process.env.SMM_API_BASE || "http://localhost:8789";
const MAP_KEY = process.env.MAP_KEY || "";
const K = encodeURIComponent(MAP_KEY);

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
      "获取当前脑图的骨架结构：全部节点（含 #uid），文本截断到 60 字符、备注只显示〔含备注〕标记。定位目标分支用；需要某分支完整文本/备注时调 get_subtree(uid)。修改脑图前先调本工具拿最新 uid。",
    parameters: Type.Object({}),
    async execute() {
      const raw = await apiFetch(`/api/mindmap/outline?key=${K}`);
      return { content: [{ type: "text" as const, text: truncate(raw, 16000) }], details: {} };
    },
  });

  // Tool 2: get_subtree — full subtree detail with breadcrumb path
  pi.registerTool({
    name: "get_subtree",
    label: "Get Subtree Detail",
    description:
      "获取指定节点（uid）的完整子树：面包屑路径 + 完整文本和备注（不截断）。脑图很大时先用 get_mindmap 拿骨架定位，再调本工具展开目标分支细节。",
    parameters: Type.Object({
      uid: Type.String({ description: "目标节点 uid（来自 get_mindmap / get_mindmap_diff 的 #uid）" }),
    }),
    async execute(_toolCallId: string, params: { uid: string }) {
      const raw = await apiFetch(`/api/mindmap/subtree?key=${K}&uid=${encodeURIComponent(params.uid)}`);
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
      const raw = await apiFetch(`/api/mindmap/diff?key=${K}`);
      return { content: [{ type: "text" as const, text: truncate(raw, 16000) }], details: {} };
    },
  });

  // Tool 3: get_background — user-written background info for this map
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
      "{action:'add',parent_uid|parent_ref,text,children?,index?,ref?} 新增子节点/子树，ref 给新节点起临时名供同批次后续 op 引用；" +
      "{action:'delete',uid} 删除节点；" +
      "{action:'move',uid,new_parent_uid|new_parent_ref,index?} 移动节点。" +
      "小改动只发对应 op，不要给整棵树。",
    parameters: Type.Object({
      ops: Type.Array(Type.Object({
        action: Type.String({ description: "update_text / add / delete / move" }),
        uid: Type.Optional(Type.String({ description: "目标节点 uid（update_text/delete/move 必填）" })),
        parent_uid: Type.Optional(Type.String({ description: "父节点 uid（add 时与 parent_ref 二选一）" })),
        parent_ref: Type.Optional(Type.String({ description: "同批次前面 add 的 ref（add 时与 parent_uid 二选一）" })),
        new_parent_uid: Type.Optional(Type.String({ description: "新父节点 uid（move 必填）" })),
        new_parent_ref: Type.Optional(Type.String({ description: "新父节点 ref（move 时替代 new_parent_uid）" })),
        text: Type.Optional(Type.String({ description: "节点文本（update_text/add 必填）" })),
        index: Type.Optional(Type.Number({ description: "插入位置（add/move 可选，默认末尾）" })),
        ref: Type.Optional(Type.String({ description: "给新节点起临时名（add 可选），同批次可引用" })),
        children: Type.Optional(Type.Array(Type.Object({}, { additionalProperties: true }), { description: "子树（add 可选），递归 {text, children}" })),
      })),
    }),
    async execute(_toolCallId: string, params: { ops: any[] }) {
      const raw = await apiFetch(`/api/mindmap/apply_ops`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: MAP_KEY, ops: params.ops }),
      });
      const isErr = raw.includes('"error"');
      return { content: [{ type: "text" as const, text: truncate(raw, 2000) }], details: {}, isError: isErr };
    },
  });

  // Tool 5: replace_mindmap — full tree replacement (ONLY for large restructures)
  pi.registerTool({
    name: "replace_mindmap",
    label: "Replace Mindmap",
    description:
      "整树替换脑图（仅在大范围重构时使用，小改动请用 update_mindmap）。root 必须是完整新树根节点；未改动节点的 uid 必须原样保留，其余字段系统会自动保留。",
    parameters: Type.Object({
      root: Type.Object({}, { additionalProperties: true, description: "完整的新树根节点 {data:{text,...}, children:[...]}" }),
    }),
    async execute(_toolCallId: string, params: { root: any }) {
      const raw = await apiFetch(`/api/mindmap/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: MAP_KEY, tree: params.root }),
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
