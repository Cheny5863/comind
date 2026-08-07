/* ai-assistant.js — 脑图 AI 助理（pi agent 后端） */
(function () {
  "use strict";

  /* ── i18n：面板独立轻量字典（zh/en，语言跟随主应用/localStorage）── */
  const I18N = {
    zh: {
      assistant: "AI 助理",
      newChat: "新建 Agent（绑定当前选中节点分支；未选中 = 整张脑图）",
      history: "Agent / 历史会话",
      modelSettings: "模型设置",
      modelTitle: "切换模型",
      background: "背景信息",
      close: "关闭",
      agents: "Agents",
      rootAgent: "整张脑图",
      newAgentBranch: "新建分支 Agent",
      noAgents: "暂无其他 Agent",
      switchAgent: "切换 Agent",
      activeAgent: "当前",
      inputPlaceholder: "输入问题…（Ctrl+J 求助当前节点 / Ctrl+Alt+N 新建会话）",
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
      noNodeSelected: "请先选中一个节点，再新建分支 Agent",
      nMessages: " 条",
      switchingModel: "切换模型…",
      switchFailed: "切换失败",
      thinkingLevel: "思考等级",
      thinkingMax: "最大",
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
      agentStarted: "🔧 分支「{name}」开始工作",
      agentDone: "✅ 分支「{name}」已完成回复",
      busyTitle: "后台 agent 正在工作",
      working: "工作中…",
      rollback: "回滚",
      rollbackHint: "回滚到某一轮对话（撤销该轮之后的对话与脑图改动）",
      rollbackTitle: "回滚到指定轮次",
      rollbackEmpty: "暂无历史轮次",
      rollbackConfirm: "确定回滚到「{msg}」之前？\n将撤销该轮之后的所有对话和脑图改动，那句话会放回输入框。",
      rollbackDone: "撤销成功",
      rollbackSkipped: "{n} 个节点未回滚",
      rollbackFail: "回滚失败：{err}",
      rollbackNoMap: "对话已回滚，脑图未自动恢复",
    },
    en: {
      assistant: "AI Assistant",
      newChat: "New Agent (bind to selected node's branch; none = whole map)",
      history: "Agents / History",
      modelSettings: "Model Settings",
      modelTitle: "Switch model",
      background: "Background",
      close: "Close",
      agents: "Agents",
      rootAgent: "Whole map",
      newAgentBranch: "New branch agent",
      noAgents: "No other agents",
      switchAgent: "Switch agent",
      activeAgent: "Active",
      inputPlaceholder: "Ask a question… (Ctrl+J assist current node / Ctrl+Alt+N new session)",
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
      noNodeSelected: "Select a node first to create a branch agent",
      nMessages: " msgs",
      switchingModel: "Switching model…",
      switchFailed: "Switch failed",
      thinkingLevel: "Thinking",
      thinkingMax: "Max",
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
      agentStarted: "🔧 Branch \"{name}\" started working",
      agentDone: "✅ Branch \"{name}\" finished",
      busyTitle: "Background agents working",
      working: "Working…",
      rollback: "Rollback",
      rollbackHint: "Roll back to a previous turn (undo conversation & map changes after it)",
      rollbackTitle: "Roll back to a turn",
      rollbackEmpty: "No turns yet",
      rollbackConfirm: "Roll back to before \"{msg}\"?\nAll conversation and map changes after this turn will be undone, and the message goes back to the input box.",
      rollbackDone: "Rolled back",
      rollbackSkipped: "{n} node(s) not rolled back",
      rollbackFail: "Rollback failed: {err}",
      rollbackNoMap: "Conversation rolled back, map not restored",
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
  let _currentBranch = "";   // "" = root agent（整张脑图）；非空 = 绑定分支 uid
  let _agents = [];          // 该脑图的所有 agent 列表（root + 各分支）
  let _pendingBranchLabel = "";  // 新建分支 agent 时的节点文本（agents 刷新前临时显示）
  let _currentSessionFile = "";  // 当前正在查看的 session 文件（前端维护，历史列表高亮用）
  let _pollTimer = null;         // 后台 agent 状态轮询（面板打开期间每 3s）
  let _agentsSnapshot = {};      // 上次轮询的 branch_uid → streaming，用于检测开始/完成

  /* ── 已读标记：localStorage 记每个 session 最后查看时间，历史列表据此显示未读红点 ── */
  function readTs(file) {
    try {
      const v = localStorage.getItem("comind_read_ts:" + mapKey() + ":" + file);
      return v ? parseFloat(v) : 0;
    } catch (_) { return 0; }
  }
  function markRead(file) {
    if (!file) return;
    try { localStorage.setItem("comind_read_ts:" + mapKey() + ":" + file, String(Date.now() / 1000)); } catch (_) {}
  }
  /* ── 上次查看的 session：进入页面打开面板时恢复，而不是固定回 root ── */
  function lastViewKey() { return "comind_last_view:" + mapKey(); }
  function readLastView() {
    try { return JSON.parse(localStorage.getItem(lastViewKey()) || "null"); } catch (_) { return null; }
  }
  function recordLastView(file, branch) {
    // 允许 file 为空但 branch 非空：新分支还没发消息时没有 session 文件，
    // 也要记住「停在这个分支」，否则关面板重开会丢新分支、恢复旧 session
    if (!file && !branch) return;
    try {
      const cur = readLastView();
      if (cur && cur.file === (file || "") && (cur.branch || "") === (branch || "")) return;
      localStorage.setItem(lastViewKey(), JSON.stringify({ file: file || "", branch: branch || "", ts: Date.now() }));
    } catch (_) {}
  }

  function mapKey() { return window.currentFileName || ""; }
  function api(suffix, branch) {
    const b = branch !== undefined ? branch : _currentBranch;
    let url = "/api/chat/" + encodeURIComponent(mapKey()) + "/" + suffix;
    if (b) url += (url.indexOf("?") >= 0 ? "&" : "?") + "branch=" + encodeURIComponent(b);
    return url;
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
        <span class="ai-title" id="ai-title">🤖 ${t("assistant")}</span>
        <div class="ai-label-group">
          <span class="ai-agent-label" id="ai-agent-label" title="${t("switchAgent")}"></span>
          <span class="ai-busy hidden" id="ai-busy" title="${t("busyTitle")}"></span>
        </div>
        <span class="ai-status" id="ai-status"></span>
        <span class="ai-flex"></span>
        <button class="ai-btn-sm" id="ai-new" title="${t("newChat")}">＋</button>
        <button class="ai-btn-sm" id="ai-rollback" title="${t("rollbackHint")}">↩</button>
        <button class="ai-btn-sm" id="ai-hist" title="${t("history")}">📂</button>
        <button class="ai-btn-sm" id="ai-close" title="${t("close")}">✕</button>
      </div>
      <div class="ai-body">
        <div class="ai-session-list hidden" id="ai-session-list"></div>
        <div class="ai-session-list hidden" id="ai-rollback-list"></div>
        <div class="ai-messages" id="ai-messages" data-empty="${t("noHistory")}"></div>
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
        </div>
      </div>
      <div class="ai-toolbar">
        <select class="ai-model-select" id="ai-model" title="${t("modelTitle")}"></select>
        <select class="ai-thinking-select" id="ai-thinking" title="${t("thinkingLevel")}"></select>
        <button class="ai-tool-btn" id="ai-bg" title="${t("background")}">📝 <span>${t("background")}</span></button>
        <button class="ai-tool-btn" id="ai-keys" title="${t("modelSettings")}">⚙️ <span>${t("modelSettings")}</span></button>
      </div>
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
      <div class="ai-resize" id="ai-resize" title="拖拽调整大小"></div>`;
    document.body.appendChild(panel);

    fab.addEventListener("click", togglePanel);
    document.getElementById("ai-close").addEventListener("click", togglePanel);
    document.getElementById("ai-send").addEventListener("click", sendMessage);
    document.getElementById("ai-abort").addEventListener("click", abortChat);
    document.getElementById("ai-new").addEventListener("click", newAgent);
    document.getElementById("ai-rollback").addEventListener("click", toggleRollbackList);
    document.getElementById("ai-hist").addEventListener("click", toggleSessions);
    document.getElementById("ai-bg").addEventListener("click", openBg);
    document.getElementById("ai-bg-close").addEventListener("click", closeBg);
    document.getElementById("ai-bg-save").addEventListener("click", saveBg);
    document.getElementById("ai-keys").addEventListener("click", openKeys);
    document.getElementById("ai-keys-close").addEventListener("click", closeKeys);
    document.getElementById("ai-keys-list").addEventListener("click", onKeysListClick);
    document.getElementById("ai-model").addEventListener("change", onModelChange);
    document.getElementById("ai-thinking").addEventListener("change", onThinkingChange);
    // 点击左上角分支标签 → 脑图聚焦到该分支根节点
    document.getElementById("ai-agent-label").addEventListener("click", focusBranchNode);

    const input = document.getElementById("ai-input");
    input.addEventListener("keydown", onInputKeydown);
    // 历史消息里的引用 chip 是 innerHTML 插入的，用事件委托支持点击定位
    document.getElementById("ai-messages").addEventListener("click", (e) => {
      const chip = e.target.closest(".ai-quote-chip");
      if (chip && chip.dataset.uid) focusNode(chip.dataset.uid);
    });
    initDrag(panel, document.getElementById("ai-header"));
    initResize(panel);
  }

  /* ── 移动端检测：≤640px 视为触屏设备，走全屏沉浸式布局（无拖拽/无 resize）── */
  function isMobile() {
    return window.matchMedia("(max-width: 640px)").matches;
  }

  function initDrag(panel, handle) {
    if (isMobile()) return; // 移动端全屏，无需拖动
    let dragging = false, sx, sy, sl, st;
    handle.style.cursor = "grab";
    handle.addEventListener("mousedown", (e) => {
      if (e.target.closest("button,select") || e.target.closest("#ai-agent-label")) return; // 不在按钮/下拉/分支标签上拖拽
      dragging = true;
      const r = panel.getBoundingClientRect();
      sx = e.clientX; sy = e.clientY; sl = r.left; st = r.top;
      e.preventDefault();
    });
    document.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      // 边界限制：面板至少留 80px 在视口内，防止拖出屏幕找不回来
      const maxLeft = window.innerWidth - 80;
      const maxTop = window.innerHeight - 80;
      panel.style.left = Math.min(Math.max(0, sl + e.clientX - sx), maxLeft) + "px";
      panel.style.top = Math.min(Math.max(0, st + e.clientY - sy), maxTop) + "px";
      panel.style.right = "auto"; panel.style.bottom = "auto";
    });
    document.addEventListener("mouseup", () => { dragging = false; });
  }

  /* ── 拖拽调整大小：右下角 handle，尺寸记忆 localStorage ── */
  function initResize(panel) {
    if (isMobile()) return; // 移动端全屏，无需调整
    // 恢复上次调整的尺寸
    try {
      const s = JSON.parse(localStorage.getItem("comind_panel_size") || "null");
      if (s && s.w >= 340 && s.h >= 380) {
        panel.style.width = s.w + "px";
        panel.style.height = s.h + "px";
      }
    } catch (_) {}
    const handle = document.getElementById("ai-resize");
    if (!handle) return;
    let resizing = false, sx, sy, sw, sh;
    handle.addEventListener("mousedown", (e) => {
      resizing = true;
      const r = panel.getBoundingClientRect();
      sx = e.clientX; sy = e.clientY; sw = r.width; sh = r.height;
      e.preventDefault(); e.stopPropagation();
    });
    document.addEventListener("mousemove", (e) => {
      if (!resizing) return;
      const w = Math.min(Math.max(sw + e.clientX - sx, 340), window.innerWidth - 16);
      const h = Math.min(Math.max(sh + e.clientY - sy, 380), window.innerHeight - 100);
      panel.style.width = w + "px";
      panel.style.height = h + "px";
    });
    document.addEventListener("mouseup", () => {
      if (!resizing) return;
      resizing = false;
      try {
        localStorage.setItem("comind_panel_size",
          JSON.stringify({ w: panel.offsetWidth, h: panel.offsetHeight }));
      } catch (_) {}
    });
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
    if (!nowHidden) {
      // 恢复上次查看的 session（刷新后不固定回 root）；无记录/记录失效则默认流程
      const rec = readLastView();
      if (rec && rec.file) {
        fetch(api("all_sessions")).then((r) => r.json()).then((items) => {
          if (items.some((it) => it.file === rec.file)) {
            switchSession(rec.file, rec.branch || "");
            loadModels(); startPolling();
          } else { openPanelRoutine(); }
        }).catch(() => openPanelRoutine());
      } else if (rec && rec.branch) {
        // 新分支还没发消息（无 session 文件）——恢复到该分支而不是旧 session
        restoreBranch(rec.branch);
        loadModels(); startPolling();
      } else { openPanelRoutine(); }
    } else { disconnectSSE(); stopPolling(); }
  }
  function openPanelRoutine() {
    loadHistory(); connectSSE(); loadModels(); recoverStreamState(); refreshAgents(); startPolling();
  }
  // 恢复到「已绑定但还没发消息的分支」（无 session 文件）：
  // 清空消息区、挂到该分支的 SSE、等用户发消息时自然创建 session。
  // 区别于 switchSession——那里要求 session 文件已存在。
  function restoreBranch(branch) {
    _currentBranch = branch || "";
    _currentSessionFile = "";
    setStreaming(false);
    disconnectSSE();
    const msgs = document.getElementById("ai-messages");
    if (msgs) msgs.innerHTML = "";
    const slist = document.getElementById("ai-session-list");
    if (slist) slist.classList.add("hidden");
    loadAgentLabel();
    refreshAgents();
    connectSSE();
    recoverStreamState();
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
    _es.addEventListener("agent_end", (e) => {
      try {
        const ev = JSON.parse(e.data || "{}");
        const msgs = ev.messages || [];
        const last = msgs[msgs.length - 1] || {};
        if (last.errorMessage) addBubble("assistant", "[Error] " + esc(last.errorMessage));
      } catch (_) {}
      setStreaming(false);
    });
    _es.addEventListener("runtime_error", (e) => {
      try {
        const ev = JSON.parse(e.data || "{}");
        if (ev.source === "pi" && ev.message) addBubble("assistant", "[Pi] " + esc(ev.message));
      } catch (_) {}
    });
    // On SSE reconnect (browser auto-reconnects), recover state
    _es.addEventListener("open", () => recoverStreamState());
    _es.addEventListener("message_update", (e) => {
      if (!_streaming) return; // abort 后丢弃残留内容事件
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
      if (!_streaming) return; // abort 后丢弃
      try {
        const ev = JSON.parse(e.data);
        appendToolCall(ev.toolName, ev.args);
      } catch (_) {}
    });
    _es.addEventListener("mindmap_update", (e) => {
      try {
        const ev = JSON.parse(e.data);
        applyMapUpdate(ev.tree);
        // 对齐画布写版本：保存时后端据此判断前端是否落后（防旧画布覆盖 AI 改动）
        if (typeof ev.ver === "number") window.__comindMapVer = ev.ver;
      } catch (_) {}
    });
  }
  function disconnectSSE() { if (_es) { _es.close(); _es = null; } }

  /* ── 状态恢复：页面刷新/SSE 重连/切换 session 后，与后端 streaming 状态双向对齐 ── */
  function recoverStreamState() {
    fetch(api("status")).then(function(r) { return r.json(); }).then(function(d) {
      // 双向同步：后端 streaming=false 时也要清掉残留的"思考中/中止"UI
      // （例如旧 session 在思考、切到空闲的新 session——不主动清就会残留）
      setStreaming(!!d.streaming);
    }).catch(function() {});
  }

  function setStreaming(on) {
    _streaming = on;
    document.getElementById("ai-send").classList.toggle("hidden", on);
    document.getElementById("ai-abort").classList.toggle("hidden", !on);
    // header 不显示「思考中」——聊天框内已有思考小窗实时展示
    // （aborting/switchingModel 等瞬时状态由各自的流程直接赋值 status）
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
    const body = _thinkEl.querySelector(".ai-thinking-body");
    body.textContent = _thinkText;
    // 固定小窗内自动滚到底部，始终显示最新思考内容（overflow hidden 无滚动条）
    body.scrollTop = body.scrollHeight;
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

  /* ── 消息渲染（流式安全）──
   * 支持：```代码块（未闭合时按纯文本，避免流式中途闪烁）、###~###### 标题、
   *      引用、无序/有序列表、行内 code / **加粗** / *斜体* / [链接](http)
   * 注意：行级块渲染，空行留白；所有内容先 esc 再加工，XSS 安全 */
  function renderInline(s) {
    s = esc(s);
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>');
    return s;
  }
  function renderMd(text) {
    const lines = String(text || "").split("\n");
    const out = [];
    let inCode = false, codeBuf = [];
    const flushCode = () => {
      if (codeBuf.length) {
        out.push('<div class="ai-code">' + esc(codeBuf.join("\n")) + "</div>");
        codeBuf = [];
      }
      inCode = false;
    };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const fence = line.match(/^```\w*\s*$/);
      if (fence) {
        if (inCode) flushCode(); else inCode = true;
        continue;
      }
      if (inCode) { codeBuf.push(line); continue; }
      const trimmed = line.trim();
      if (!trimmed) { out.push('<div class="ai-gap"></div>'); continue; }
      let block = "";
      if (/^#{1,4}\s/.test(trimmed)) {
        const level = trimmed.match(/^#{1,4}/)[0].length;
        block = '<div class="ai-h' + level + '">' + renderInline(trimmed.replace(/^#{1,4}\s*/, "")) + "</div>";
      } else if (/^&gt;\s?/.test(trimmed) || /^>\s?/.test(trimmed)) {
        block = '<div class="ai-quote">' + renderInline(trimmed.replace(/^&gt;\s?/, "").replace(/^>\s?/, "")) + "</div>";
      } else if (/^[-*+]\s+/.test(trimmed)) {
        block = '<div class="ai-li">• ' + renderInline(trimmed.replace(/^[-*+]\s+/, "")) + "</div>";
      } else if (/^\d+[.、]\s+/.test(trimmed)) {
        const m = trimmed.match(/^(\d+)[.、]\s+(.*)$/);
        block = '<div class="ai-li"><span class="ai-li-num">' + m[1] + ".</span> " + renderInline(m[2]) + "</div>";
      } else {
        block = '<div class="ai-line">' + renderInline(trimmed) + "</div>";
      }
      out.push(block);
    }
    if (inCode) {
      // 代码块未闭合（流式中间态）：剩余行按纯文本渲染，避免整个块消失闪烁
      codeBuf.forEach((l) => { if (l.trim()) out.push('<div class="ai-line">' + renderInline(l) + "</div>"); });
    }
    return out.join("");
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
  // 把历史用户消息里的 [NODE_ASSIST...] 协议原文还原成引用 chip（含多引用），
  // 而不是把协议原文裸显示出来。用户怎么写的就怎么显示。
  function renderUserMsg(text) {
    if (!text || !text.startsWith("[NODE_ASSIST")) return esc(text);
    const lines = text.split("\n");
    const m = lines[0].match(/^\[NODE_ASSIST(?: uid=([^\]]*))?\]\s*用户在节点「([\s\S]*?)」上求助\s*$/);
    if (!m) return esc(text);
    // 收集引用列表（含首行第一个引用）
    const ql = [{ uid: m[1] || "", text: m[2] }];
    const bodyLines = [];
    for (let i = 1; i < lines.length; i++) {
      const ln = lines[i].trim();
      if (!ln) continue;
      if (ln.startsWith("[该节点的备注内容：")) continue;
      const rm = ln.match(/^\[引用(\d+)\] uid=([^\s「」]*) 「([\s\S]*?)」(?:（备注：.*）)?$/);
      if (rm) {
        const idx = parseInt(rm[1], 10);
        ql[idx - 1] = { uid: rm[2], text: rm[3] };
        continue;
      }
      if (ln.startsWith("引用节点")) continue;
      bodyLines.push(ln);
    }
    const body = bodyLines.join("\n").trim();
    // [引用N] 占位符还原成 chip（历史消息保留 chip 样式，节点可点击定位）
    const chipHtml = (q) => {
      const label = (q.text || "").length > 20 ? q.text.slice(0, 20) + "…" : (q.text || "");
      return '<span class="ai-quote-chip" data-uid="' + esc(q.uid || "") + '" data-full-text="' +
        esc(q.text || "") + '"><span>📎 ' + esc(label) + "</span></span>";
    };
    let out = "";
    const refRe = /\[引用(\d+)\]/g;
    let last = 0, rm2;
    while ((rm2 = refRe.exec(body)) !== null) {
      out += esc(body.slice(last, rm2.index));
      const q = ql[parseInt(rm2[1], 10) - 1];
      out += q ? chipHtml(q) : rm2[0];
      last = rm2.index + rm2[0].length;
    }
    out += esc(body.slice(last));
    return out || (ql.length ? ql.map(chipHtml).join(" ") : "");
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

  /* ── Agent 列表（多 agent 并行）── */
  function loadAgentLabel() {
    const el = document.getElementById("ai-agent-label");
    if (!el) return;
    const ag = _agents.find((a) => (a.branch_uid || "") === _currentBranch);
    const display = (ag && (ag.display_label || ag.label)) ? (ag.display_label || ag.label)
      : (_pendingBranchLabel ? _pendingBranchLabel.slice(0, 5) : t("rootAgent"));
    el.textContent = display;
    el.classList.toggle("is-branch", !!_currentBranch);
  }
  function refreshAgents(cb) {
    fetch(api("agents")).then((r) => r.json()).then((list) => {
      _agents = list || [];
      loadAgentLabel();
      // 同步当前查看的 session：当前分支的活跃会话文件
      const cur = _agents.find((a) => (a.branch_uid || "") === _currentBranch);
      if (cur && cur.session_file) {
        _currentSessionFile = cur.session_file;
        markRead(_currentSessionFile);  // 正在查看 = 已读，历史列表不显示红点
        recordLastView(_currentSessionFile, _currentBranch);  // 记住"上次查看"，刷新后恢复
      } else if (cur && _currentBranch) {
        // 分支已绑定但还没发消息（无 session 文件）——记住分支本身，
        // 否则关面板重开会丢新分支、恢复旧 session
        _currentSessionFile = "";
        recordLastView("", _currentBranch);
      }
      if (cb) cb(_agents);
    }).catch(() => {});
  }
  /* ── 后台 agent 状态轮询：检测其他分支开始/完成，header 显示活跃计数 ── */
  function startPolling() {
    if (_pollTimer) return;
    _agentsSnapshot = {};   // 重置快照：首轮只建基线，不误报
    _pollTimer = setInterval(pollTick, 3000);
    pollTick();
  }
  function stopPolling() { if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; } }
  function agentLabelOf(branch, list) {
    const ag = (list || []).find((a) => (a.branch_uid || "") === branch);
    if (ag && (ag.display_label || ag.label)) return (ag.display_label || ag.label);
    return branch ? branch.slice(0, 5) : t("rootAgent");
  }
  function pollTick() {
    fetch(api("agents")).then((r) => r.json()).then((list) => {
      const snap = {};
      (list || []).forEach((a) => { snap[a.branch_uid || ""] = !!a.streaming; });
      // 后台 agent 开始/完成 → toast（当前分支的状态由 SSE 管，不重复通知）
      Object.keys(snap).forEach((b) => {
        if (b === _currentBranch) return;
        const cur = !!snap[b];
        const prev = _agentsSnapshot[b] === undefined ? cur : !!_agentsSnapshot[b];
        if (cur && !prev) toast(t("agentStarted", { name: agentLabelOf(b, list) }));
        else if (!cur && prev) toast(t("agentDone", { name: agentLabelOf(b, list) }));
      });
      _agentsSnapshot = snap;
      // header 活跃计数：其他分支正在工作的数量
      const busy = Object.keys(snap).filter((b) => snap[b] && b !== _currentBranch).length;
      const el = document.getElementById("ai-busy");
      if (el) {
        el.classList.toggle("hidden", busy === 0);
        el.textContent = busy > 0 ? "🔄 " + busy : "";
      }
      // 历史列表打开时自动刷新（未读红点/进行中状态实时更新）
      const listEl = document.getElementById("ai-session-list");
      if (listEl && !listEl.classList.contains("hidden")) refreshSessionList();
    }).catch(() => {});
  }
  function switchAgent(branch) {
    if ((branch || "") === _currentBranch) {
      return;
    }
    _currentBranch = branch || "";
    _currentSessionFile = "";  // 等待 refreshAgents 同步新分支活跃会话
    _pendingBranchLabel = "";  // 已存在的 agent 从后端拿 label
    setStreaming(false);  // 清掉旧 session 的"思考中/中止"UI 残留
    disconnectSSE();
    document.getElementById("ai-messages").innerHTML = "";
    document.getElementById("ai-session-list").classList.add("hidden");
    loadAgentLabel();
    loadHistory();
    connectSSE();
    recoverStreamState();
  }
  function newAgent() {
    // 新建 agent：绑定当前选中节点所在分支；未选中 = 整张脑图（root）
    const node = activeNode();
    _pendingBranchLabel = node ? stripHtml(node.nodeData.data.text || "") : "";
    newAgentFor(node ? (node.nodeData.data.uid || "") : "");
  }
  function newAgentFor(branch) {
    // 「+」语义（上下文敏感）：
    // - 该分支已交流过（has_history）→ 新开一轮（reset 清会话，旧文件保留在历史）
    // - 该分支未交流/无 session（幂等）→ 只切换，不重复创建/reset
    const ag = _agents.find((a) => (a.branch_uid || "") === (branch || ""));
    const hasHistory = ag && ag.has_history;
    const doSwitch = () => {
      _currentBranch = branch || "";
      _currentSessionFile = "";  // 新建/切换后等待 agents 刷新同步活跃会话
      // 立刻记住分支（不等 refreshAgents）：新分支还没发消息没有 session 文件，
      // 用户关面板再开时能恢复到该分支，而不是旧的 lastView session
      if (branch) recordLastView("", branch);
      if (!branch) _pendingBranchLabel = "";  // root 用固定文案
      setStreaming(false);  // 清掉旧 session 的"思考中/中止"UI 残留
      disconnectSSE();
      document.getElementById("ai-messages").innerHTML = "";
      document.getElementById("ai-session-list").classList.add("hidden");
      loadAgentLabel();
      refreshAgents();
      connectSSE();
      recoverStreamState();
    };
    if (hasHistory) {
      // 已交流过：reset 新开一轮（旧会话文件保留在历史列表）
      fetch(api("reset", branch), { method: "POST" }).then(doSwitch).catch(() => {});
    } else {
      // 未交流/新分支：幂等切换，不产生新会话
      doSwitch();
    }
  }

  /* ── 历史会话（全部 session 按时间混排，分支降级为行内标签）── */
  function toggleSessions() {
    const list = document.getElementById("ai-session-list");
    if (!list.classList.contains("hidden")) { list.classList.add("hidden"); return; }
    list.classList.remove("hidden");
    refreshSessionList();
  }
  function refreshSessionList() {
    const list = document.getElementById("ai-session-list");
    if (!list || list.classList.contains("hidden")) return;
    fetch(api("all_sessions"))
      .then((r) => r.json()).then((items) => {
        list.innerHTML = "";
        if (!items.length) {
          list.innerHTML = '<div class="ai-session-item">' + t("noHistory") + '</div>';
          return;
        }
        items.forEach((it) => {
          const div = document.createElement("div");
          // 高亮判定：纯前端逻辑——当前正在查看的 session 文件才标 active
          const isActive = it.file === _currentSessionFile;
          // 未读：最后消息时间 > 用户上次查看时间（+1s 容差），且不是当前查看的
          const isUnread = !isActive && (it.modified || 0) > (readTs(it.file) || 0) + 1;
          div.className = "ai-session-item" + (isActive ? " active" : "") + (isUnread ? " unread" : "");
          const d = new Date((it.modified || 0) * 1000);
          const time = d.toLocaleString(lang().startsWith("zh") ? "zh-CN" : "en-US", { month: "numeric", day: "numeric", hour: "numeric", minute: "numeric" });
          const branchTag = it.branch_uid
            ? '<span class="ai-session-branch">' + esc(it.display_label || it.branch_label || it.branch_uid.slice(0, 5)) + "</span>"
            : '<span class="ai-session-branch root">root</span>';
          const msgCount = '<span class="ai-session-count">' + it.user_messages + t("nMessages") + "</span>";
          const dot = isUnread ? '<span class="ai-dot"></span>' : "";
          const spin = it.streaming ? '<span class="ai-spin" title="' + t("working") + '"></span>' : "";
          div.innerHTML = dot + spin + "<span class='ai-session-time'>" + time + "</span>" + branchTag + msgCount;
          div.title = it.name + (it.branch_label ? " — " + it.branch_label : "") + (it.streaming ? " [" + t("working") + "]" : "");
          div.dataset.file = it.file;
          div.dataset.branch = it.branch_uid || "";
          div.addEventListener("click", () => switchSession(it.file, it.branch_uid || ""));
          list.appendChild(div);
        });
      }).catch(() => {});
  }
  function switchSession(file, branch) {
    // 已在查看目标 session，短路（避免无谓断开重连/重建）
    if (file === _currentSessionFile && (branch || "") === _currentBranch) return;
    fetch(api("switch", branch), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_file: file }),
    }).then(() => {
      _currentBranch = branch || "";  // 切 session 时同步分支上下文
      _currentSessionFile = file;     // 前端维护当前查看的 session
      setStreaming(false);  // 清掉旧 session 的"思考中/中止"UI 残留
      disconnectSSE();
      document.getElementById("ai-messages").innerHTML = "";
      document.getElementById("ai-session-list").classList.add("hidden");
      loadAgentLabel();
      refreshAgents();
      loadHistory();
      connectSSE();
      recoverStreamState();  // 与目标 session 的实际 streaming 状态对齐
    }).catch(() => {});
  }

  /* ── 轮次回滚（按对话轮次线性回滚）── */
  // 占位符判断：兼容中英两种语言（session 语言可能与当前 UI 语言不同）
  function isPlaceholderMsg(s) {
    return !s || s === "（引用节点求助）" || s === "(Assist with node)";
  }
  // 把消息文本里的 [引用N] 占位符还原成可读的 📎 节点文本（quoted_list 提供对应节点）
  function replaceRefPlaceholders(text, quotedList) {
    if (!quotedList || !quotedList.length) return text;
    return (text || "").replace(/\[引用(\d+)\]/g, (m, n) => {
      const q = quotedList[parseInt(n, 10) - 1];
      return q && q.text ? "📎" + q.text : m;
    });
  }
  // 轮次显示文本：纯引用 → 📎 节点文本列表（有区分度）；混合 → 原文（[引用N] 还原成 📎）；普通 → 原文
  function rollbackDisplay(quotedList, userMsg) {
    const ql = quotedList || [];
    const rest = isPlaceholderMsg(userMsg) ? "" : (userMsg || "");
    if (rest) {
      // 混合轮次：显示用户原文（占位符还原成 📎 节点文本），保留编排顺序
      return replaceRefPlaceholders(rest, ql).trim() || (ql.length ? ql.map((q) => "📎" + (q.text || "")).join(" ") : "");
    }
    if (ql.length) {
      // 纯引用轮次：显示引用节点列表
      return ql.map((q) => "📎" + (q.text || "")).join(" ");
    }
    return t("nodeAssistFallback");
  }
  function toggleRollbackList() {
    const list = document.getElementById("ai-rollback-list");
    if (!list.classList.contains("hidden")) { list.classList.add("hidden"); return; }
    document.getElementById("ai-session-list").classList.add("hidden");
    list.classList.remove("hidden");
    refreshRollbackList();
  }
  function refreshRollbackList() {
    const list = document.getElementById("ai-rollback-list");
    if (!list || list.classList.contains("hidden")) return;
    fetch(api("turns"))
      .then((r) => r.json()).then((turns) => {
        list.innerHTML = '<div class="ai-rollback-head">' + t("rollbackTitle") + "</div>";
        if (!turns || !turns.length) {
          list.innerHTML += '<div class="ai-session-item">' + t("rollbackEmpty") + "</div>";
          return;
        }
        turns.forEach((tn) => {
          const div = document.createElement("div");
          div.className = "ai-session-item rollback-item";
          const d = new Date((tn.ts || 0) * 1000);
          const time = d.toLocaleString(lang().startsWith("zh") ? "zh-CN" : "en-US", { month: "numeric", day: "numeric", hour: "numeric", minute: "numeric" });
          // 纯引用轮次显示引用节点文本（有区分度），否则显示用户消息
          const display = rollbackDisplay(tn.quoted_list, tn.user_msg || "");
          const s = tn.diff_summary || {};
          const parts = [];
          if (s.add) parts.push("+" + s.add);
          if (s.update_text) parts.push("~" + s.update_text);
          if (s.delete) parts.push("-" + s.delete);
          if (s.move) parts.push("↔" + s.move);
          const badge = parts.length ? '<span class="ai-rollback-badge">' + parts.join(" ") + "</span>" : "";
          div.innerHTML = "<span class='ai-session-time'>" + time + "</span><span class='ai-rollback-msg'>" + esc(display) + "</span>" + badge;
          div.title = display;
          div.addEventListener("click", () => handleRollback(tn));
          list.appendChild(div);
        });
      }).catch(() => {});
  }
  function handleRollback(turn) {
    const preview = rollbackDisplay(turn.quoted_list, turn.user_msg || "");
    if (!window.confirm(t("rollbackConfirm", { msg: preview.slice(0, 30) }))) return;
    fetch(api("rollback"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_msg_idx: turn.user_msg_idx, branch_uid: _currentBranch }),
    }).then((r) => r.json()).then((res) => {
      if (!res.ok) { toast(t("rollbackFail", { err: res.error || "?" })); return; }
      // 回滚成功：后端 session 已截断 + 脑图已恢复（SSE mindmap_update 已广播）
      disconnectSSE();
      const box = document.getElementById("ai-messages");
      box.innerHTML = "";
      document.getElementById("ai-rollback-list").classList.add("hidden");
      // 把目标轮那句话放回输入框（可直接编辑重发）：
      // 用户发了什么就放回什么——引用轮次放回 chip（引用节点已不存在则不放），
      // 文本按原文放回，引用位置用 chip 还原（保留用户编排顺序）
      const input = document.getElementById("ai-input");
      input.innerHTML = "";
      let placed = false;
      const rqList = res.quoted_list || [];
      const rqExists = res.quoted_list_exists || [];
      // 旧格式纯引用轮次的占位符不是用户输入，不参与回填
      let rqMsg = isPlaceholderMsg(res.user_msg) ? "" : (res.user_msg || "");
      // 按 [引用N] 占位符顺序重建输入框：chip 存在则放 chip，否则丢弃占位符
      const refRe = /\[引用(\d+)\]/g;
      let last = 0, m;
      while ((m = refRe.exec(rqMsg)) !== null) {
        if (m.index > last) {
          input.appendChild(document.createTextNode(rqMsg.slice(last, m.index)));
        }
        const qi = parseInt(m[1], 10) - 1;
        const q = rqList[qi];
        if (q && q.uid && rqExists[qi]) {
          insertQuoteChip(q.uid, q.text || "", q.note || "");
          placed = true;
        }
        last = m.index + m[0].length;
      }
      if (last < rqMsg.length) {
        input.appendChild(document.createTextNode(rqMsg.slice(last)));
        placed = true;
      }
      if (placed) input.focus();
      // 画布刷新：后端响应带回滚后完整树（kill 后 SSE 广播靠 EventSource 自动
      // 重连不可靠——重连晚于广播、旧 queue 已 unsub），直接 setData 最稳
      if (res.tree) applyMapUpdate(res.tree);
      loadHistory();      // 截断后的历史
      refreshAgents();    // 更新 label / 活跃 session
      connectSSE();
      recoverStreamState();
      // 成功提示极简：正常只弹「撤销成功」；异常情况最多追加一条关键信息
      toast(t("rollbackDone"));
      const skipped = res.skipped || [];
      if (skipped.length) toast(t("rollbackSkipped", { n: skipped.length }));
      else if (res.map_restored === false) toast(t("rollbackNoMap"));
    }).catch(() => toast(t("rollbackFail", { err: "network" })));
  }
  /* ── 发送 / 中止 / 新建 ── */
  function syncMap() {
    if (!_mindMap) return Promise.resolve();
    return fetch(api("sync"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(_mindMap.getData()),
    }).catch(() => {});
  }
  // 提取输入框内容：chip 原位替换成 [引用N] 占位符（保留用户编排顺序），
  // 同时返回按出现顺序的 quotes 数组。用户怎么写的就怎么发。
  function collectInputMessage() {
    const input = document.getElementById("ai-input");
    const quotes = [];
    let text = "";
    let refIdx = 0;
    input.childNodes.forEach((node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        text += node.textContent;
      } else if (node.classList && node.classList.contains("ai-quote-chip")) {
        refIdx++;
        quotes.push({
          uid: node.dataset.uid || "",
          text: node.dataset.fullText || "",
          note: node.dataset.note || "",
        });
        text += "[引用" + refIdx + "]";
      } else {
        text += node.textContent || "";
      }
    });
    return { text: text.trim(), quotes };
  }
  function getInputText() {
    return collectInputMessage().text;
  }
  function collectQuotes() {
    return collectInputMessage().quotes;
  }
  function sendMessage() {
    if (_streaming) return;
    const { text: msg, quotes } = collectInputMessage();
    if (!msg && !quotes.length) return;
    const input = document.getElementById("ai-input");
    input.innerHTML = "";
    // 气泡显示也按用户原顺序：把 [引用N] 占位符还原成 chip 样式
    let shown = "";
    let refIdx = 0;
    const parts = msg.split(/\[引用(\d+)\]/g);
    for (let i = 0; i < parts.length; i++) {
      if (i % 2 === 0) {
        shown += esc(parts[i]);
      } else {
        const q = quotes[parseInt(parts[i], 10) - 1];
        if (q) shown += '<span style="opacity:.75">📎' + esc(q.text.slice(0, 15)) + "…</span>";
      }
    }
    addBubble("user", shown || (quotes.length ? '📎<span style="opacity:.75">' + esc(quotes[0].text.slice(0, 15)) + "…</span>" : ""));
    const context = quotes.length ? { quoted_nodes: quotes } : null;
    syncMap().then(() => {
      fetch(api("prompt"), {
        method: "POST", headers: { "Content-Type": "application/json", "X-Lang": lang() },
        body: JSON.stringify({ message: msg || t("nodeAssistFallback"), context, branch_uid: _currentBranch }),
      }).then((r) => {
        if (!r.ok) {
          return r.text().then((text) => {
            addBubble("assistant", "[Error] " + esc(text || r.statusText));
            setStreaming(false);
          });
        }
      }).catch((err) => {
        addBubble("assistant", "[Error] " + esc(err && err.message ? err.message : "Request failed"));
        setStreaming(false);
      });
    });
  }
  function abortChat() {
    // 秒停：立即恢复 UI（清思考小窗、恢复发送按钮），不显示「停止中」
    // 后端 abort 在后台处理（pi 快速停止，超时强制 kill）
    setStreaming(false);
    fetch(api("abort"), { method: "POST" }).catch(() => {});
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
      // 选中后把节点居中到画布中心（保持当前缩放级别）
      try { _mindMap.renderer.moveNodeToCenter(target); } catch (_) {}
    }
  }
  function focusBranchNode() {
    // 点击 header 左上角分支标签：聚焦到当前分支根节点（root agent 无分支，忽略）
    if (!_currentBranch || !_mindMap) return;
    focusNode(_currentBranch);
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
    { id: "anthropic", name: "🟠 Anthropic (Claude)", placeholder: "sk-ant-…" },
    { id: "openai", name: "🟢 OpenAI", placeholder: "sk-…" },
    { id: "google", name: "🔴 Google (Gemini)", placeholder: "AI…" },
    { id: "xai", name: "⚡ xAI (Grok)", placeholder: "xai-…" },
    { id: "openrouter", name: "🌐 OpenRouter", placeholder: "sk-or-…" },
    { id: "mistral", name: "🔷 Mistral", placeholder: t("pasteKey") },
    { id: "groq", name: "🟡 Groq", placeholder: "gsk_…" },
    { id: "fireworks", name: "🔥 Fireworks", placeholder: t("pasteKey") },
    { id: "together", name: "🤝 Together AI", placeholder: t("pasteKey") },
    { id: "kimi-coding", name: "🌙 Kimi for Coding", placeholder: t("pasteKey") },
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
      // 加载思考等级
      loadThinkingLevels(d.thinkingLevel || "max");
    }).catch(() => {});
  }
  function loadThinkingLevels(currentLevel) {
    const sel = document.getElementById("ai-thinking");
    fetch(api("thinking_levels")).then((r) => r.json()).then((d) => {
      const levels = d.levels || [];
      sel.innerHTML = "";
      if (!levels.length) {
        // 模型不支持 thinking，隐藏下拉框
        sel.style.display = "none";
        return;
      }
      sel.style.display = "";
      levels.forEach((lv) => {
        const opt = document.createElement("option");
        opt.value = lv;
        opt.textContent = lv;
        sel.appendChild(opt);
      });
      sel.value = currentLevel || levels[levels.length - 1] || "max";
    }).catch(() => { sel.style.display = "none"; });
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
      else loadThinkingLevels("max"); // 换模型后刷新 thinking levels
    }).catch(() => { status.textContent = t("switchFailed"); });
  }
  function onThinkingChange() {
    const sel = document.getElementById("ai-thinking");
    const level = sel.value;
    if (!level) return;
    fetch(api("thinking_level"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ level: level }),
    }).catch(() => {});
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
    // Ctrl+Alt+N：新建会话（选中节点 → 该节点分支；未选中 → root）
    // 不用 Ctrl+N：Chrome 保留快捷键（新建窗口）页面 JS 无法拦截，必须用非保留组合
    // 不走 togglePanel（其"恢复上次 session"逻辑会异步覆盖新建意图），直接打开面板 + newAgent
    document.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.altKey && (e.key === "n" || e.key === "N")) {
        e.preventDefault();
        const panel = document.getElementById("ai-panel");
        if (panel.classList.contains("hidden")) {
          panel.classList.remove("hidden");
          document.getElementById("ai-fab").classList.add("active");
          fetch(api("panel"), {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ open: true }),
          }).catch(() => {});
          loadModels();
          startPolling();
        }
        newAgent();
      }
      // Ctrl+J：当前节点求助
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
