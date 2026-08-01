/* map-switcher.js — 脑图快速切换下拉框（注入到编辑页） */
(function () {
  "use strict";

  var currentName = window.currentFileName || "";
  if (!currentName) return;

  /* ── 样式 ── */
  var style = document.createElement("style");
  style.textContent = [
    ".map-switcher {",
    "  position: fixed; top: 8px; left: 12px; z-index: 9999;",
    "  display: flex; align-items: center; gap: 6px;",
    "}",
    ".map-switcher select {",
    "  appearance: none; -webkit-appearance: none;",
    "  background: #222b45; color: #e0e0e0;",
    "  border: 1px solid #3a3f5c; border-radius: 6px;",
    "  padding: 5px 28px 5px 10px; font-size: 13px;",
    "  cursor: pointer; outline: none;",
    "  max-width: 220px; overflow: hidden; text-overflow: ellipsis;",
    "  background-image: url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%23888'/%3E%3C/svg%3E\");",
    "  background-repeat: no-repeat; background-position: right 8px center;",
    "}",
    ".map-switcher select:hover { border-color: #5a6080; }",
    ".map-switcher select:focus { border-color: #7c8aff; }",
    ".map-switcher .home-btn {",
    "  background: none; border: none; color: #888; font-size: 16px;",
    "  cursor: pointer; padding: 4px; line-height: 1;",
    "}",
    ".map-switcher .home-btn:hover { color: #fff; }",
  ].join("\n");
  document.head.appendChild(style);

  /* ── DOM ── */
  var wrap = document.createElement("div");
  wrap.className = "map-switcher";

  var homeBtn = document.createElement("button");
  homeBtn.className = "home-btn";
  homeBtn.title = "回到目录";
  homeBtn.textContent = "🏠";
  homeBtn.addEventListener("click", function () {
    window.location.href = "/";
  });

  var sel = document.createElement("select");
  sel.title = "切换脑图";

  // 加载中占位
  var placeholder = document.createElement("option");
  placeholder.textContent = stripExt(currentName);
  placeholder.value = currentName;
  sel.appendChild(placeholder);

  wrap.appendChild(homeBtn);
  wrap.appendChild(sel);

  /* ── 等 DOM ready 后插入 ── */
  function mount() {
    document.body.appendChild(wrap);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }

  /* ── 拉列表 ── */
  fetch("/api/list")
    .then(function (r) { return r.json(); })
    .then(function (files) {
      sel.innerHTML = "";
      files.forEach(function (f) {
        var opt = document.createElement("option");
        opt.value = f.name;
        opt.textContent = stripExt(f.name);
        if (f.name === currentName) opt.selected = true;
        sel.appendChild(opt);
      });
    })
    .catch(function () {});

  /* ── 切换 ── */
  sel.addEventListener("change", function () {
    var name = sel.value;
    if (name && name !== currentName) {
      window.location.href = "/editor?name=" + encodeURIComponent(name);
    }
  });

  /* ── 工具 ── */
  function stripExt(name) {
    return name.replace(/\.smm\.json$/i, "").replace(/\.xmind$/i, "");
  }
})();
