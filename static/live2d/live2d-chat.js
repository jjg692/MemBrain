/**
 * Live2D 双窗口之：对话窗前端
 * ──────────────────────────────────────────────
 * 连接 /ws/chat 作为 sender（发送输入、接收回复），显示完整对话历史；
 * 模型窗（petmode，watcher）只显示气泡提醒。二者同 user_id 共享会话。
 */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var params = new URLSearchParams(location.search);
  var user_id = params.get("user_id") || localStorage.getItem("membrain_userId") || "default_user";
  var role_id = params.get("role_id") || LocalRole() || "";
  var ws = null, pending = false;

  var body = $("chatBody"), input = $("chatInput"), sendBtn = $("chatSend"),
      statusEl = $("chatStatus"), titleEl = $("chatTitle");

  function LocalRole() {
    var r = new URLSearchParams(location.search).get("role_id");
    return r || "";
  }

  function userLabel(u) {
    try { var s = JSON.parse(localStorage.getItem("membrain_userId") || "null"); } catch (e) {}
    return localStorage.getItem("membrain_myName") || "我";
  }
  function roleLabel() {
    // 用角色显示名（从 contacts 查）
    return localStorage.getItem("membrain_role_" + role_id) || role_id;
  }

  function appendMsg(text, kind, who) {
    var d = document.createElement("div");
    d.className = "msg " + (kind || "role");
    if (who) { var w = document.createElement("span"); w.className = "who"; w.textContent = who; d.appendChild(w); }
    var t = document.createElement("span");
    if (kind === "thinking") t.textContent = text || "…";
    else t.textContent = text || "";
    d.appendChild(t);
    body.appendChild(d);
    body.scrollTop = body.scrollHeight;
    return d;
  }

  // 初始：加载角色名 + 历史
  async function init() {
    if (!role_id) {
      try {
        var c = await fetch("/api/contacts").then(function (r) { return r.json(); });
        var def = (c.data || []).find(function (x) { return x.default; }) || (c.data || [])[0];
        role_id = (def && def.role_id) || "";
      } catch (e) {}
    }
    titleEl.textContent = "💬 " + roleLabel();
    if (role_id) {
      try {
        var h = await fetch("/api/history?user_id=" + encodeURIComponent(user_id) + "&role_id=" + encodeURIComponent(role_id)).then(function (r) { return r.json(); });
        var items = (h.data || []);
        if (!items.length) { appendMsg("（还没有对话，先打个招呼吧）", "thinking"); }
        items.forEach(function (it) {
          if (it.role === "user") appendMsg(it.content, "user", userLabel());
          else appendMsg(it.content, "role", roleLabel());
        });
      } catch (e) {}
    }
    connect();
  }

  function connect() {
    if (!role_id) return;
    var proto = location.protocol === "https:" ? "wss://" : "ws://";
    var url = proto + location.host + "/ws/chat?user_id=" + encodeURIComponent(user_id) +
              "&role_id=" + encodeURIComponent(role_id) + "&mode=sender";
    try { if (ws) ws.close(); } catch (e) {}
    ws = new WebSocket(url);
    statusEl.textContent = "连接中…";
    ws.onopen = function () { statusEl.textContent = "在线"; };
    ws.onmessage = function (evt) {
      var m; try { m = JSON.parse(evt.data); } catch (e) { return; }
      if (m.type === "connected") { statusEl.textContent = "在线"; }
      else if (m.type === "thinking") { pending = true; appendMsg("…", "thinking"); sendBtn.disabled = true; }
      else if (m.type === "reply") {
        pending = false; sendBtn.disabled = false;
        // 去掉上一个 thinking 占位（简化：直接追加回复，thinking 留着无妨）
        appendMsg(m.content || "", "role", roleLabel());
      } else if (m.type === "proactive" || m.type === "reminder") {
        appendMsg(m.content || m.text || "", "role", roleLabel());
      }
    };
    ws.onclose = function () {
      statusEl.textContent = "已断开，重连中…";
      setTimeout(function () { try { connect(); } catch (e) {} }, 3000);
    };
  }

  function send() {
    var text = input.value.trim();
    if (!text || !ws || ws.readyState !== 1 || pending) return;
    appendMsg(text, "user", userLabel());
    ws.send(JSON.stringify({ content: text }));
    input.value = "";
  }

  sendBtn.addEventListener("click", send);
  input.addEventListener("keydown", function (e) { if (e.key === "Enter") send(); });

  init();
})();
