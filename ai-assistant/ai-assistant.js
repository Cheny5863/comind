/* ai-assistant.js — 脑图 AI 助理（pi agent 后端） */
(function () {
  "use strict";

  /* ── i18n：面板独立轻量字典（zh/en，语言跟随主应用/localStorage）── */
  const I18N = {
    zh: {
      assistant: "AI 助理",
      newChat: "新建对话",
      history: "历史会话",
      modelSettings: "模型设置",
      modelTitle: "切换模型",
      background: "背景信息",
      close: "关闭",
      inputPlaceholder: "输入问题…（Ctrl+J 求助当前节点）",
      send: "发送 ➤",
      abort: "中止 ■",
      bgPlaceholder: "给 AI 的背景信息：这张脑图的主题、项目上下文、你的目标…",
      save: "保存",
      keysHint: "Key 仅保存在本机 private/keys.json，不会上传",
      pasteKey: "粘贴 sk-… 开头的 key",
      nodeAssistFallback: "（引用节点求助）",
      thinking: "思考中…",
      mapUpdated: "🗺️ <em>脑图已更新</em>",
      noHistory: "暂无历史会话",
      nMessages: " 条",
      aborting: "正在终止…",
      switchingModel: "切换模型…",
      switchFailed: "切换失败",
      loading: "加载中…",
      loadFailed: "加载失败",
      configured: "已配置 ✓",
      notConfigured: "未配置",
      clear: "清除",
      showHide: "显示/隐藏",
      keyRequired: "请输入 key",
      saveFailed: "保存失败",
      clearedKey: "已清除 {name} key",
      savedKey: "已保存，服务已重启生效",
    },
    en: {
      assistant: "AI Assistant",
      newChat: "New chat",
      history: "History",
      modelSettings: "Model Settings",
      modelTitle: "Switch model",
      background: "Background",
      close: "Close",
      inputPlaceholder: "Ask a question… (Ctrl+J to assist the current node)",
      send: "Send ➤",
      abort: "Stop ■",
      bgPlaceholder: "Background info for AI: this map's topic, project context, your goals…",
      save: "Save",
      keysHint: "Keys are stored locally in private/keys.json, never uploaded",
      pasteKey: "Paste a key starting with sk-…",
      nodeAssistFallback: "(Assist with node)",
      thinking: "Thinking…",
      mapUpdated: "🗺️ <em>Mind map updated</em>",
      noHistory: "No sessions yet",
      nMessages: " msgs",
      aborting: "Stopping…",
      switchingModel: "Switching model…",
      switchFailed: "Switch failed",
      loading: "Loading…",
      loadFailed: "Load failed",
      configured: "Configured ✓",
      notConfigured: "Not configured",
      clear: "Clear",
      showHide: "Show/Hide",
      keyRequired: "Please enter a key",
      saveFailed: "Save failed",
      clearedKey: "Cleared {name} key",
      savedKey: "Saved, service restarted",
    },
  };
  let _lang = null;
  function detectLang() {
    try {
      // localStorage 是全局语言偏好的唯一来源（getLanguage/saveLanguage 都走这里）
      const s = localStorage.getItem("SIMPLE_MIND_MAP_LANG");
      if (s) return s;
      if (window.takeOverAppMethods && window.takeOverAppMethods.getLanguage) {
        const l = window.takeOverAppMethods.getLanguage();
        if (l) return l;
      }
    } catch (_) {}
    return "zh"; // 默认中文
  }
  // 将前端语言 key（zh/zhtw/en/vi）映射到 I18N 字典 key
  // zhtw/vi 等没有独立翻译的变体 fallback 到最接近的基础语言
  function resolveI18nKey(raw) {
    if (I18N[raw]) return raw;
    // zh 变体（zhtw 等）→ zh
    if (raw.startsWith("zh")) return "zh";
    // 其他未知语言 → en
    return "en";
  }
  function lang() {
    if (!_lang) _lang = detectLang();
    return _lang;
  }
  function t(key, vars) {
    const resolved = resolveI18nKey(lang());
    const d = I18N[resolved] || I18N.zh;
    let s = d[key] !== undefined ? d[key] : (I18N.zh[key] !== undefined ? I18N.zh[key] : key);
    if (vars) {
      Object.keys(vars).forEach((k) => { s = s.replace("{" + k + "}", vars[k]); });
    }
    return s;
  }

  let _es = null;            // EventSource
  let _streaming = false;
  let _currentBubble = null;
  let _mindMap = null;

  function mapKey() { return window.currentFileName || ""; }
  function api(suffix) {
    return "/api/chat/" + encodeURIComponent(mapKey()) + "/" + suffix;
  }
  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  /* ── DOM ── */
  function createDOM() {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/ai-assistant/ai-assistant.css?v=" + Date.now();
    document.head.appendChild(link);

    const fab = document.createElement("button");
    fab.id = "ai-fab"; fab.className = "ai-fab"; fab.title = t("assistant");
    fab.textContent = "🤖";
    document.body.appendChild(fab);

    const panel = document.createElement("div");
    panel.id = "ai-panel"; panel.className = "ai-panel hidden";
    panel.innerHTML = `
      <div class="ai-header" id="ai-header">
        <span class="ai-title">${t("assistant")}</span>
        <span class="ai-status" id="ai-status"></span>
        <select class="ai-model-select" id="ai-model" title="${t("modelTitle")}"></select>
        <button class="ai-btn-sm" id="ai-new" title="${t("newChat")}">＋</button>
        <button class="ai-btn-sm" id="ai-hist" title="${t("history")}">📂</button>
        <button class="ai-btn-sm" id="ai-keys" title="${t("modelSettings")}">⚙️</button>
        <button class="ai-btn-sm" id="ai-bg" title="${t("background")}">📝</button>
        <button class="ai-btn-sm" id="ai-close" title="${t("close")}">✕</button>
      </div>
      <div class="ai-session-list hidden" id="ai-session-list"></div>
      <div class="ai-messages" id="ai-messages"></div>
      <div class="ai-input-area">
        <div class="ai-input-wrap">
          <div class="ai-input" id="ai-input" contenteditable="true"
               data-placeholder="${t("inputPlaceholder")}"></div>
        </div>
        <div class="ai-input-buttons">
          <button class="ai-send" id="ai-send">${t("send")}</button>
          <button class="ai-abort hidden" id="ai-abort">${t("abort")}</button>
        </div>
      </div>
      <div class="ai-bg-drawer hidden" id="ai-bg-drawer">
        <textarea id="ai-bg-text" placeholder="${t("bgPlaceholder")}"></textarea>
        <div class="bar">
          <button class="ai-bg-close" id="ai-bg-close">${t("close")}</button>
          <button class="ai-bg-save" id="ai-bg-save">${t("save")}</button>
        </div>
      </div>
      <div class="ai-bg-drawer hidden" id="ai-keys-drawer">
        <div class="ai-keys-title">${t("modelSettings")}</div>
        <div class="ai-keys-hint">${t("keysHint")}</div>
        <div class="ai-keys-list" id="ai-keys-list"></div>
        <div class="bar">
          <button class="ai-bg-close" id="ai-keys-close">${t("close")}</button>
        </div>
      </div>`;
    document.body.appendChild(panel);

    fab.addEventListener("click", togglePanel);
    document.getElementById("ai-close").addEventListener("click", togglePanel);
    document.getElementById("ai-send").addEventListener("click", sendMessage);
    document.getElementById("ai-abort").addEventListener("click", abortChat);
    document.getElementById("ai-new").addEventListener("click", resetChat);
    document.getElementById("ai-hist").addEventListener("click", toggleSessions);
    document.getElementById("ai-bg").addEventListener("click", openBg);
    document.getElementById("ai-bg-close").addEventListener("click", closeBg);
    document.getElementById("ai-bg-save").addEventListener("click", saveBg);
    document.getElementById("ai-keys").addEventListener("click", openKeys);
    document.getElementById("ai-keys-close").addEventListener("click", closeKeys);
    document.getElementById("ai-keys-list").addEventListener("click", onKeysListClick);
    document.getElementById("ai-model").addEventListener("change", onModelChange);

    const input = document.getElementById("ai-input");
    input.addEventListener("keydown", onInputKeydown);
    // 历史消息里的引用 chip 是 innerHTML 插入的，用事件委托支持点击定位
    document.getElementById("ai-messages").addEventListener("click", (e) => {
      const chip = e.target.closest(".ai-quote-chip");
      if (chip && chip.dataset.uid) focusNode(chip.dataset.uid);
    });
    initDrag(panel, document.getElementById("ai-header"));
  }

  function initDrag(panel, handle) {
    let dragging = false, sx, sy, sl, st;
    handle.style.cursor = "grab";
    handle.addEventListener("mousedown", (e) => {
      if (e.target.closest("button,select")) return; // 不在按钮/下拉上拖拽
      dragging = true;
      const r = panel.getBoundingClientRect();
      sx = e.clientX; sy = e.clientY; sl = r.left; st = r.top;
      e.preventDefault();
    });
    document.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      panel.style.left = (sl + e.clientX - sx) + "px";
      panel.style.top = (st + e.clientY - sy) + "px";
      panel.style.right = "auto"; panel.style.bottom = "auto";
    });
    document.addEventListener("mouseup", () => { dragging = false; });
  }

  function togglePanel() {
    const panel = document.getElementById("ai-panel");
    const fab = document.getElementById("ai-fab");
    const nowHidden = panel.classList.toggle("hidden");
    fab.classList.toggle("active", !nowHidden);
    // 面板开关状态存服务端，不依赖浏览器缓存
    fetch(api("panel"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ open: !nowHidden }),
    }).catch(() => {});
    if (!nowHidden) { loadHistory(); connectSSE(); loadModels(); recoverStreamState(); } else { disconnectSSE(); }
  }
  function openPanelFlash() {
    const panel = document.getElementById("ai-panel");
    if (panel.classList.contains("hidden")) togglePanel();
    const header = document.getElementById("ai-header");
    header.classList.remove("flash");
    void header.offsetWidth;
    header.classList.add("flash");
  }
  /* ── SSE ── */
  function connectSSE() {
    if (_es) return;
    _es = new EventSource(api("events"));
    _es.addEventListener("agent_start", () => setStreaming(true));
    _es.addEventListener("agent_end", () => setStreaming(false));
    // On SSE reconnect (browser auto-reconnects), recover state
    _es.addEventListener("open", () => recoverStreamState());
    _es.addEventListener("message_update", (e) => {
      try {
        const ev = JSON.parse(e.data);
        const d = ev.assistantMessageEvent || {};
        if (d.type === "text_delta") appendDelta(d.delta);
        else if (d.type === "thinking_start") showThinking();
        else if (d.type === "thinking_delta") appendThinking(d.delta);
        else if (d.type === "thinking_end") hideThinking();
      } catch (_) {}
    });
    _es.addEventListener("tool_execution_start", (e) => {
      try {
        const ev = JSON.parse(e.data);
        appendToolCall(ev.toolName, ev.args);
      } catch (_) {}
    });
    _es.addEventListener("mindmap_update", (e) => {
      try {
        const ev = JSON.parse(e.data);
        applyMapUpdate(ev.tree);
      } catch (_) {}
    });
  }
  function disconnectSSE() { if (_es) { _es.close(); _es = null; } }

  /* ── 状态恢复：页面刷新/SSE 重连后，向后端查询是否正在流式输出 ── */
  function recoverStreamState() {
    fetch(api("status")).then(function(r) { return r.json(); }).then(function(d) {
      if (d.streaming && !_streaming) setStreaming(true);
    }).catch(function() {});
  }

  function setStreaming(on) {
    _streaming = on;
    document.getElementById("ai-send").classList.toggle("hidden", on);
    document.getElementById("ai-abort").classList.toggle("hidden", !on);
    document.getElementById("ai-status").textContent = on ? t("thinking") : "";
    if (!on) { _currentBubble = null; hideThinking(); }
  }

  /* ── 思考小窗：thinking_start 出现、thinking_delta 流式更新、thinking_end 隐藏 ── */
  let _thinkEl = null, _thinkText = "";
  function showThinking() {
    hideThinking();
    _thinkText = "";
    const box = document.getElementById("ai-messages");
    const div = document.createElement("div");
    div.className = "ai-thinking";
    div.innerHTML = '<div class="ai-thinking-title">💭 ' + t("thinking") + '</div><div class="ai-thinking-body"></div>';
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
    _thinkEl = div;
  }
  function appendThinking(delta) {
    if (!_thinkEl) showThinking();
    _thinkText += delta || "";
    _thinkEl.querySelector(".ai-thinking-body").textContent = _thinkText;
    const box = document.getElementById("ai-messages");
    box.scrollTop = box.scrollHeight;
  }
  function hideThinking() {
    if (_thinkEl) { _thinkEl.remove(); _thinkEl = null; }
    _thinkText = "";
  }

  /* ── AI 改图：mindmap_update ── */
  function applyMapUpdate(tree) {
    if (!_mindMap || !tree) return;
    // 后端 SSE 推来的 tree 就是纯节点树根 {data, children}，直接 setData。
    // 不要包成 getData()+root 的形式——setData 期望纯树，包 root 会导致
    // 界面渲染旧树（AI 改图不生效），且垃圾 root 属性污染后续 sync。
    _mindMap.setData(tree);
    _mindMap.render();
    addBubble("assistant", t("mapUpdated"));
  }

  /* ── 消息渲染 ── */
  function renderMd(text) {
    let h = esc(text);
    h = h.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    h = h.replace(/`([^`]+)`/g, "<code>$1</code>");
    h = h.replace(/^### (.*)$/gm, "<strong>$1</strong>");
    h = h.replace(/\n/g, "<br>");
    return h;
  }
  function addBubble(role, html) {
    const box = document.getElementById("ai-messages");
    const div = document.createElement("div");
    div.className = "ai-bubble " + role;
    div.innerHTML = html;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
    return div;
  }
  function appendDelta(text) {
    if (!_currentBubble) _currentBubble = addBubble("assistant", "");
    const raw = (_currentBubble.getAttribute("data-raw") || "") + text;
    _currentBubble.setAttribute("data-raw", raw);
    _currentBubble.innerHTML = renderMd(raw);
    const box = document.getElementById("ai-messages");
    box.scrollTop = box.scrollHeight;
  }
  function appendToolCall(name, args) {
    const box = document.getElementById("ai-messages");
    const div = document.createElement("div");
    div.className = "ai-tool-call";
    const s = args ? JSON.stringify(args) : "";
    div.textContent = "🔧 " + name + "(" + (s.length > 70 ? s.slice(0, 70) + "…" : s) + ")";
    div.title = s;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
    _currentBubble = null; // 工具调用后开启新气泡
  }

  /* ── 历史 ── */
  // 把历史用户消息里的 [NODE_ASSIST uid=xxx] 前缀还原成引用 chip，
  // 而不是把协议原文裸显示出来
  function renderUserMsg(text) {
    const m = text.match(/^\[NODE_ASSIST uid=([^\]]*)\] 用户在节点「([\s\S]*?)」上求助\s*\n?/);
    if (!m) return esc(text);
    const uid = m[1], nodeText = m[2];
    let rest = text.slice(m[0].length).trim();
    if (rest === "（引用节点求助）" || rest === "(Assist with node)") rest = "";
    const label = nodeText.length > 20 ? nodeText.slice(0, 20) + "…" : nodeText;
    const chip = '<span class="ai-quote-chip" data-uid="' + esc(uid) + '" data-full-text="' +
      esc(nodeText) + '"><span>📎 ' + esc(label) + "</span></span>";
    return chip + (rest ? " " + esc(rest) : "");
  }
  function loadHistory() {
    fetch(api("history")).then((r) => r.json()).then((msgs) => {
      const box = document.getElementById("ai-messages");
      if (box.children.length > 0 || !msgs || !msgs.length) return;
      msgs.forEach((m) => {
        if (m.role === "user") addBubble("user", renderUserMsg(m.text));
        else if (m.role === "assistant" && m.text) addBubble("assistant", renderMd(m.text));
      });
    }).catch(() => {});
  }

  /* ── 会话列表 ── */
  function toggleSessions() {
    const list = document.getElementById("ai-session-list");
    if (!list.classList.contains("hidden")) { list.classList.add("hidden"); return; }
    fetch(api("sessions")).then((r) => r.json()).then((items) => {
      list.innerHTML = "";
      if (!items.length) {
        list.innerHTML = '<div class="ai-session-item">' + t("noHistory") + '</div>';
      }
      items.forEach((it) => {
        const div = document.createElement("div");
        div.className = "ai-session-item" + (it.active ? " active" : "");
        const d = new Date(it.modified * 1000);
        div.innerHTML = "<span>" + d.toLocaleString(lang().startsWith("zh") ? "zh-CN" : "en-US", { month: "numeric", day: "numeric", hour: "numeric", minute: "numeric" }) +
          "</span><span>" + it.user_messages + t("nMessages") + "</span>";
        div.title = it.name;
        div.addEventListener("click", () => switchSession(it.file));
        list.appendChild(div);
      });
      list.classList.remove("hidden");
    }).catch(() => {});
  }
  function switchSession(file) {
    fetch(api("switch"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_file: file }),
    }).then(() => {
      document.getElementById("ai-messages").innerHTML = "";
      document.getElementById("ai-session-list").classList.add("hidden");
      loadHistory();
    }).catch(() => {});
  }
  /* ── 发送 / 中止 / 新建 ── */
  function syncMap() {
    if (!_mindMap) return Promise.resolve();
    return fetch(api("sync"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(_mindMap.getData()),
    }).catch(() => {});
  }
  function getInputText() {
    const input = document.getElementById("ai-input");
    const clone = input.cloneNode(true);
    clone.querySelectorAll(".ai-quote-chip").forEach((c) => c.remove());
    return clone.textContent || "";
  }
  function collectQuotes() {
    return Array.from(document.querySelectorAll("#ai-input .ai-quote-chip"))
      .map((c) => ({ uid: c.dataset.uid || "", text: c.dataset.fullText || "", note: c.dataset.note || "" }));
  }
  function sendMessage() {
    if (_streaming) return;
    const msg = getInputText().trim();
    const quotes = collectQuotes();
    if (!msg && !quotes.length) return;
    const input = document.getElementById("ai-input");
    input.innerHTML = "";
    let shown = esc(msg);
    quotes.forEach((q) => { shown = '<span style="opacity:.75">📎' + esc(q.text.slice(0, 15)) + "…</span> " + shown; });
    addBubble("user", shown);
    const context = quotes.length ? { quoted_node: quotes[0] } : null;
    syncMap().then(() => {
      fetch(api("prompt"), {
        method: "POST", headers: { "Content-Type": "application/json", "X-Lang": lang() },
        body: JSON.stringify({ message: msg || t("nodeAssistFallback"), context }),
      }).catch(() => {});
    });
  }
  function abortChat() {
    fetch(api("abort"), { method: "POST" }).catch(() => {});
    // Optimistic: show "正在终止…" and poll status
    document.getElementById("ai-status").textContent = t("aborting");
    var _abortPoll = setInterval(function() {
      fetch(api("status")).then(function(r) { return r.json(); }).then(function(d) {
        if (!d.streaming) {
          clearInterval(_abortPoll);
          setStreaming(false);
        }
      }).catch(function() {});
    }, 1000);
    // Safety: stop polling after 10s regardless
    setTimeout(function() { clearInterval(_abortPoll); }, 10000);
  }
  function resetChat() {
    fetch(api("reset"), { method: "POST" }).then(() => {
      document.getElementById("ai-messages").innerHTML = "";
    }).catch(() => {});
  }

  function onInputKeydown(e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  }

  /* ── 节点引用 chip ── */
  function insertQuoteChip(uid, text, note) {
    const input = document.getElementById("ai-input");
    const chip = document.createElement("span");
    chip.className = "ai-quote-chip";
    chip.contentEditable = "false";
    chip.dataset.uid = uid;
    chip.dataset.fullText = text;
    chip.dataset.note = note || "";
    const label = text.length > 20 ? text.slice(0, 20) + "…" : text;
    chip.innerHTML = "<span>📎 " + esc(label) + '</span><span class="x">✕</span>';
    chip.querySelector(".x").addEventListener("click", (ev) => {
      ev.stopPropagation(); chip.remove();
    });
    chip.addEventListener("click", () => focusNode(uid));
    input.appendChild(chip);
    input.appendChild(document.createTextNode(" "));
    input.focus();
  }
  function focusNode(uid) {
    if (!_mindMap) return;
    const target = findNodeByUid(_mindMap.renderer.root, uid);
    if (target) {
      _mindMap.renderer.clearActiveNode();
      _mindMap.renderer.addNodeToActiveList(target, true);
    }
  }
  function findNodeByUid(node, uid) {
    if (!node) return null;
    if (node.nodeData && node.nodeData.data && node.nodeData.data.uid === uid) return node;
    const children = node.children || [];
    for (const c of children) {
      const r = findNodeByUid(c, uid);
      if (r) return r;
    }
    return null;
  }

  /* ── 节点求助（Ctrl+J / 全局按钮）── */
  function activeNode() {
    if (!_mindMap) return null;
    const list = _mindMap.renderer.activeNodeList || [];
    return list.length ? list[0] : null;
  }
  function stripHtml(t) {
    const d = document.createElement("div");
    d.innerHTML = t || "";
    return d.textContent.trim();
  }
  function nodeAssist() {
    const node = activeNode();
    if (!node) { openPanelFlash(); return; }
    const uid = node.nodeData.data.uid || "";
    const text = stripHtml(node.nodeData.data.text);
    const note = stripHtml(node.nodeData.data.note || "");
    openPanelFlash();
    insertQuoteChip(uid, text, note);
    document.getElementById("ai-input").focus();
  }

  /* ── 背景信息 ── */
  function openBg() {
    const drawer = document.getElementById("ai-bg-drawer");
    drawer.classList.remove("hidden");
    fetch("/api/background?name=" + encodeURIComponent(mapKey()))
      .then((r) => r.json())
      .then((d) => { document.getElementById("ai-bg-text").value = d.content || ""; })
      .catch(() => {});
  }
  function closeBg() {
    document.getElementById("ai-bg-drawer").classList.add("hidden");
  }
  function saveBg() {
    const content = document.getElementById("ai-bg-text").value;
    fetch("/api/background?name=" + encodeURIComponent(mapKey()), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }).then(() => closeBg()).catch(() => {});
  }

  /* ── 模型设置（key 管理）── */
  const KEY_PROVIDERS = [
    { id: "deepseek", name: "🔵 DeepSeek", placeholder: t("pasteKey") },
    { id: "moonshotai-cn", name: "🟣 Moonshot (Kimi)", placeholder: t("pasteKey") },
  ];
  function openKeys() {
    const drawer = document.getElementById("ai-keys-drawer");
    drawer.classList.remove("hidden");
    const list = document.getElementById("ai-keys-list");
    list.innerHTML = '<div class="ai-keys-loading">' + t("loading") + '</div>';
    fetch("/api/keys").then((r) => r.json()).then((d) => {
      const cfg = d.configured || {};
      list.innerHTML = "";
      KEY_PROVIDERS.forEach((p) => {
        const ok = !!cfg[p.id];
        const row = document.createElement("div");
        row.className = "ai-key-row";
        row.innerHTML =
          '<div class="ai-key-head"><span>' + p.name + "</span>" +
          '<span class="ai-key-badge' + (ok ? " on" : "") + '">' + (ok ? t("configured") : t("notConfigured")) + "</span></div>" +
          '<div class="ai-key-body">' +
          '<div class="ai-key-line"><input type="password" class="ai-key-input" placeholder="' + p.placeholder +
          '" data-provider="' + p.id + '" autocomplete="off" spellcheck="false">' +
          '<button type="button" class="ai-key-eye" data-provider="' + p.id + '" title="' + t("showHide") + '">👁</button></div>' +
          '<div class="ai-key-actions">' +
          '<button type="button" class="ai-key-btn save" data-provider="' + p.id + '">' + t("save") + '</button>' +
          (ok ? '<button type="button" class="ai-key-btn clear" data-provider="' + p.id + '">' + t("clear") + '</button>' : "") +
          '</div></div>';
        list.appendChild(row);
      });
    }).catch(() => {
      list.innerHTML = '<div class="ai-keys-loading">' + t("loadFailed") + '</div>';
    });
  }
  function closeKeys() {
    document.getElementById("ai-keys-drawer").classList.add("hidden");
  }
  function onKeysListClick(e) {
    const eyeBtn = e.target.closest(".ai-key-eye");
    if (eyeBtn) {
      const pv = eyeBtn.dataset.provider;
      const inp = document.querySelector('.ai-key-input[data-provider="' + pv + '"]');
      const show = inp.type === "password";
      inp.type = show ? "text" : "password";
      eyeBtn.textContent = show ? "🙈" : "👁";
      return;
    }
    const saveBtn = e.target.closest(".ai-key-btn.save");
    const clearBtn = e.target.closest(".ai-key-btn.clear");
    if (!saveBtn && !clearBtn) return;
    const prov = (saveBtn || clearBtn).dataset.provider;
    const input = document.querySelector('.ai-key-input[data-provider="' + prov + '"]');
    const isClear = !!clearBtn;
    const key = isClear ? null : (input ? input.value.trim() : "");
    if (!isClear && !key) { toast(t("keyRequired")); return; }
    const name = KEY_PROVIDERS.find((p) => p.id === prov) || {};
    fetch("/api/keys", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: prov, key: key }),
    }).then((r) => {
      if (!r.ok) return r.json().then((d) => { throw new Error((d && d.detail) || t("saveFailed")); });
      toast(isClear ? t("clearedKey", { name: name.name || prov }) : t("savedKey"));
      openKeys(); // 刷新状态徽章
    }).catch((err) => toast(t("saveFailed") + ": " + (err && err.message ? err.message : "")));
  }

  let _toastTimer = null;
  function toast(msg) {
    let el = document.getElementById("ai-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "ai-toast"; el.className = "ai-toast";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(function () { el.classList.remove("show"); }, 2500);
  }

  /* ── 模型切换 ── */
  function loadModels() {
    const sel = document.getElementById("ai-model");
    fetch(api("models")).then((r) => r.json()).then((d) => {
      const groups = {};
      (d.models || []).forEach((m) => {
        (groups[m.provider] = groups[m.provider] || []).push(m);
      });
      sel.innerHTML = "";
      Object.keys(groups).sort().forEach((prov) => {
        const og = document.createElement("optgroup");
        og.label = prov;
        groups[prov].forEach((m) => {
          const opt = document.createElement("option");
          opt.value = m.provider + "|" + m.id;
          opt.textContent = m.id;
          og.appendChild(opt);
        });
        sel.appendChild(og);
      });
      if (d.current) {
        sel.value = d.current.provider + "|" + d.current.id;
      }
    }).catch(() => {});
  }
  function onModelChange() {
    const sel = document.getElementById("ai-model");
    const parts = sel.value.split("|");
    if (parts.length !== 2) return;
    const status = document.getElementById("ai-status");
    status.textContent = t("switchingModel");
    fetch(api("model"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: parts[0], model_id: parts[1] }),
    }).then((r) => {
      status.textContent = r.ok ? "" : t("switchFailed");
      if (!r.ok) loadModels(); // 还原显示
    }).catch(() => { status.textContent = t("switchFailed"); });
  }

  /* ── 初始化 ── */
  // AI 面板内（焦点或选区）禁止画布快捷键劫持：Ctrl+C/V/X 等走浏览器默认，
  // 否则选中聊天消息文本复制时会被 KeyCommand 当成画布复制拦截
  function installShortcutGuard(mindMap) {
    if (!mindMap || !mindMap.opt || !mindMap.editNodeClassList) return;
    mindMap.opt.customCheckEnableShortcut = function (e) {
      var ae = document.activeElement;
      if (ae && ae.closest && ae.closest("#ai-panel")) return false;
      var sel = window.getSelection();
      if (sel && sel.rangeCount > 0 && !sel.isCollapsed && sel.anchorNode) {
        var el = sel.anchorNode.nodeType === 3 ? sel.anchorNode.parentElement : sel.anchorNode;
        if (el && el.closest && el.closest("#ai-panel")) return false;
      }
      if (e.target === document.body) return true;
      for (var i = 0; i < mindMap.editNodeClassList.length; i++) {
        var cur = mindMap.editNodeClassList[i];
        if (e.target.classList && e.target.classList.contains(cur)) return true;
      }
      return false;
    };
  }

  /* ── 语言切换：收到 lang_change 事件后重建面板 DOM ── */
  function rebuildForLang() {
    _lang = null; // 清缓存，下次 lang() 重新从 localStorage 读
    const wasOpen = !document.getElementById("ai-panel")?.classList.contains("hidden");
    // 断开 SSE、销毁旧 DOM
    disconnectSSE();
    const oldPanel = document.getElementById("ai-panel");
    const oldFab = document.getElementById("ai-fab");
    const oldToast = document.getElementById("ai-toast");
    if (oldPanel) oldPanel.remove();
    if (oldFab) oldFab.remove();
    if (oldToast) oldToast.remove();
    // 重建
    createDOM();
    if (wasOpen) togglePanel();
  }

  function init(mindMap) {
    _mindMap = mindMap;
    installShortcutGuard(mindMap);
    if (document.getElementById("ai-fab")) return;
    createDOM();
    // 暴露给顶栏 AI 按钮（ToolbarNodeBtnList.vue aiAssist）
    window.__aiAssist = nodeAssist;
    // 语言切换时重建面板
    if (window.$bus) window.$bus.$on("lang_change", rebuildForLang);
    // 恢复面板开关状态（服务端持久化；流式状态由后端事件重放恢复）
    fetch(api("panel")).then((r) => r.json()).then((d) => {
      if (d.open) togglePanel();
    }).catch(() => {});
    // Ctrl+J：当前节点求助
    document.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === "j" || e.key === "J")) {
        const tag = (e.target.tagName || "").toLowerCase();
        if (tag === "input" || tag === "textarea") return;
        e.preventDefault();
        nodeAssist();
      }
    });
  }

  if (window.$bus) {
    window.$bus.$on("app_inited", init);
  } else {
    window.addEventListener("load", () => {
      const t = setInterval(() => {
        if (window.$bus) {
          clearInterval(t);
          window.$bus.$on("app_inited", init);
        }
      }, 300);
    });
  }
})();
