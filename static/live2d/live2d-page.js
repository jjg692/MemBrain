/**
 * Live2D 桌面宠物独立页前端
 * ──────────────────────────────────────────────
 * 职责：
 *   1. 本地优先、CDN 回退加载 Cubism2 运行时（live2d-widget.js：L2Dwidget.min.js + 核心分块 0.min.js）
 *   2. 拉取 /api/live2d/models 环境信息，初始化 L2Dwidget 渲染指定模型
 *   3. 通过 /ws/chat 私聊 WebSocket 与角色对话，底部聊天气泡展示
 *
 * 扩展点 / 后门（为后续优化预留）：
 *   - window.Live2D：全局 hub，暴露 renderer / config / bus（on/emit 事件总线）。
 *     未来语音驱动口型、情绪→表情映射、动捕等可直接挂到 bus 事件上，无需改本文件。
 *   - 渲染器解耦：所有对 L2Dwidget 的调用收敛在 renderer 适配层，换渲染器只改这一处（实现同名接口）。
 *   - HOOK_* 生命钩子：渲染完成 / 收到消息 / 输入发送，可被外部脚本扩展。
 */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var el = {};

  // ---------- DOM 引用 ----------
  function initEl() {
    var map = {
      canvas: "live2d-canvas",
      topbar: "l2d-topbar",
      modelName: "l2d-model-name",
      switchBtn: "l2d-switch",
      togglePanel: "l2d-toggle-panel",
      modelPanel: "l2d-model-panel",
      modelList: "l2d-model-list",
      bubble: "l2d-bubble",
      bubbleText: "l2d-bubble-text",
      inputbar: "l2d-inputbar",
      input: "l2d-input",
      send: "l2d-send",
      status: "l2d-status",
      error: "l2d-error",
      retry: "l2d-retry",
    };
    for (var k in map) el[k] = $(map[k]);
    el.errorMsg = document.querySelector(".l2d-error-msg");
  }

  // ---------- 全局扩展 hub（后门） ----------
  // 供外部脚本 / 未来功能挂载。已存在则不覆盖（保护现场注入的扩展）。
  window.Live2D = window.Live2D || {};
  if (!window.Live2D.bus) {
    var bus = {
      _map: {},
      on: function (evt, fn) { (this._map[evt] = this._map[evt] || []).push(fn); return this; },
      off: function (evt, fn) { var a = this._map[evt]; if (a) this._map[evt] = a.filter(function (f) { return f !== fn; }); },
      emit: function (evt, payload) {
        (this._map[evt] || []).slice().forEach(function (fn) {
          try { fn(payload); } catch (e) { console.error("[Live2D] hook error", evt, e); }
        });
      },
    };
    window.Live2D.bus = bus;
  }
  var bus = window.Live2D.bus;

  // ---------- 状态 ----------
  var params = new URLSearchParams(location.search);
  var state = {
    config: null,       // /api/live2d/models 返回
    currentModel: null, // 当前模型 id
    roleLive2d: "",     // 当前角色配置的 live2d_model（用于检测后台变更自动切换）
    ws: null,
    pending: false,     // 等待回复中
    user_id: params.get("user_id") || localStorage.getItem("membrain_userId") || "default_user",
    role_id: params.get("role_id") || "",
    runtimeLoaded: false,
  };

  // ============================================================
  // 渲染器适配层（换渲染器只改这一处）
  // 当前：live2d-widget.js（Cubism2）。抽象 load/destroy/playMotion/setExpression。
  // 未来接入 pixi-live2d-display（Cubism3）或自研渲染器时实现同一接口即可。
  // ============================================================
  var renderer = {
    name: "l2dwidget",
    available: function () {
      return typeof window.L2Dwidget !== "undefined" && window.L2Dwidget && window.L2Dwidget.init;
    },

    /**
     * 初始化并渲染模型。
     * @param {object} m 模型信息（含 model_url）
     * @returns {Promise}
     */
    load: function (m) {
      return new Promise(function (resolve, reject) {
        if (!renderer.available()) { reject(new Error("运行时未就绪")); return; }
        var base = (location.origin || "");
        var modelUrl = (m.model_url || "").indexOf("http") === 0 ? m.model_url : base + (m.model_url || "");

        var userCfg = window.Live2D.config || {};
        // petmode（透明小窗只包模型）时用更大比例让模型铺满小窗；普通大窗按宽度自适应
        var scale = (userCfg.scale != null) ? userCfg.scale : (state.petmode ? floatScale() : defaultScale());

        try {
          window.L2Dwidget.init(Object.assign({
            model: { jsonPath: modelUrl, scale: scale, translateX: 0, translateY: 0 },
            display: {
              superSample: userCfg.superSample || 2,
              width: window.innerWidth || 420,
              height: window.innerHeight || 640,
              position: "fixed",
              hOffset: 0,
              vOffset: 0,
            },
            motion: {
              enable: true,
              idle: { enable: true, interval: 8 },
              tapBody: { enable: true },
            },
            dialog: { enable: false, hitokoto: false },
          }, window.Live2D.l2dConfig || {}));

          // L2Dwidget 异步加载模型，稍后确认渲染
          setTimeout(function () {
            try {
              if (window.L2Dwidget.show) window.L2Dwidget.show();
            } catch (e) {}
            state.rendered = true;
            bus.emit("render", { model: m });
            resolve(true);
          }, 900);
        } catch (e) {
          reject(e);
        }
      });
    },

    destroy: function () {
      try {
        if (window.L2Dwidget) {
          if (window.L2Dwidget.remove) window.L2Dwidget.remove();
          if (window.L2Dwidget.dispose) window.L2Dwidget.dispose();
        }
      } catch (e) {}
    },

    // —— 后门：动作 / 表情（驱动真实运行时模型）——
    // 通过改造后的运行时暴露的 window.__l2dManager 拿到 cManager，再取模型实例。
    // cManager.getModel(0) 返回 cModel（继承 L2DBaseModel），具备：
    //   startMotion(group, index, priority) / setExpression(name) /
    //   setLipSync(bool) / setLipSyncValue(v) / setParamFloat(id,val,weight)
    playMotion: function (group, index, priority) {
      var m = liveModel();
      if (!m || !m.startMotion) return;
      try { m.startMotion(group, index == null ? 0 : index, priority == null ? 3 : priority); } catch (e) {}
      bus.emit("motion", { name: group, index: index, priority: priority || 3, destination: "renderer" });
    },
    setExpression: function (name) {
      var m = liveModel();
      if (!m || !m.setExpression) return;
      try { m.setExpression(name); } catch (e) {}
      bus.emit("expression", { name: name, destination: "renderer" });
    },
    setLipSync: function (on) {
      var m = liveModel();
      if (!m || !m.setLipSync) return;
      try { m.setLipSync(!!on); } catch (e) {}
    },
    setLipSyncValue: function (v) {
      var m = liveModel();
      if (!m || !m.setLipSyncValue) return;
      try { m.setLipSyncValue(v); } catch (e) {}
    },
    // 直接设模型的 mouth 参数（口型开合核心通道）
    setMouth: function (v) {
      var m = liveModel();
      if (!m || !m.live2DModel) return;
      try { m.live2DModel.setParamFloat("PARAM_MOUTH_OPEN_Y", v, 1); } catch (e) {}
    },
    // 视线跟随：直接驱动模型看向鼠标方向（不依赖运行时内部 rAF 链路）
    setDrag: function (dx, dy) {
      var m = liveModel();
      if (!m) return;
      try {
        if (m.setDrag) m.setDrag(dx, dy);
        if (m.live2DModel) {
          m.live2DModel.setParamFloat("PARAM_EYE_BALL_X", dx, 1);
          m.live2DModel.setParamFloat("PARAM_EYE_BALL_Y", dy, 1);
        }
      } catch (e) {}
    },
  };

  // 运行时模型助手（供 renderer 方法调用）。cManager.getModel(0) 返回当前模型实例。
  function liveModel() {
    try {
      if (window.__l2dManager && window.__l2dManager.getModel) {
        return window.__l2dManager.getModel(0);
      }
    } catch (e) {}
    return null;
  }
  function motionGroups() {
    try {
      var m = window.__l2dManager && window.__l2dManager.getModel && window.__l2dManager.getModel(0);
      if (m && m.modelSetting && m.modelSetting.json && m.modelSetting.json.motions) {
        return Object.keys(m.modelSetting.json.motions);
      }
    } catch (e) {}
    return [];
  }

  function defaultScale() {
    // 桌面宠物窗口窄（~420px），模型过大显示不全 → 窄窗用更小 scale
    var winW = window.innerWidth || 420;
    return winW <= 560 ? 1.2 : 1.0;
  }

  function floatScale() {
    // 透明小窗（petmode）：让立绘填充满窗口，减少窗口四周多余透明区（拖拽区）。
    // scale 是 L2Dwidget 相对模型缩放。实测 scale 0.62 时角色约占窗口高 51%；
    // 提至 1.0 时约 82%。（不能无限拉大：角色 bbox 高 > 画布高，过大会裁顶部/脚）
    // 用户反馈 1.0 略大，缩小一个滚轮格(÷1.1) → 约 0.909。
    return 0.909;
  }

  // ============================================================
  // 运行时加载（本地优先 + CDN 回退）
  // ============================================================
  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = src; s.async = false;
      s.onload = function () { resolve(); };
      s.onerror = function () { reject(new Error("脚本加载失败: " + src)); };
      document.head.appendChild(s);
    });
  }

  function probeL2D(url) {
    // 探测本地运行时是否可服务（fetch HEAD）
    return fetch(url, { method: "HEAD", cache: "no-store" }).then(function (r) {
      return r.ok;
    }).catch(function () { return false; });
  }

  /**
   * 确保运行时已加载。策略：
   *   - 若有本地运行时文件（runtime_local 中 present=true），优先本地；HEAD 失败则回退 CDN。
   *   - 本地 index 与本地 chunk 必须同源（chunk 相对路径从 script src 解析），故成对选择。
   */
  async function ensureRuntime() {
    if (state.runtimeLoaded) return;
    var cfg = state.config || {};
    var local = (cfg.runtime_local || []).filter(function (f) { return f.present; });
    var cdn = cfg.runtime_cdn || {};

    var localIndex = local.find(function (f) { return f.name === "L2Dwidget.min.js"; });
    var localChunk = local.find(function (f) { return f.name === "L2Dwidget.0.min.js"; });

    // 探测本地可用性
    var localOk = false;
    if (localIndex && localChunk) {
      var ok = await probeL2D(localIndex.url);
      var ok2 = await probeL2D(localChunk.url);
      localOk = ok && ok2;
    }

    var indexSrc, chunkSrc;
    if (localOk && localIndex && localChunk) {
      indexSrc = localIndex.url;
      chunkSrc = localChunk.url;   // 相对 local index 目录，天然同源
    } else {
      // CDN 回退（index 与 chunk 同一 CDN 目录，也同源）
      indexSrc = cdn.runtime;
      chunkSrc = cdn.chunk;
    }

    await loadScript(indexSrc);
    await loadScript(chunkSrc);
    // 等待脚本执行（L2Dwidget 全局就绪）
    await new Promise(function (r) { setTimeout(r, 50); });
    state.runtimeLoaded = true;
  }

  // ============================================================
  // 聊天（私聊 WS，复用主页面协议）
  // ============================================================
  function connectWS() {
    if (!state.role_id) return; // 无角色则等待
    var proto = location.protocol === "https:" ? "wss://" : "ws://";
    // 双窗口：petmode（模型窗）是 watcher（只接收气泡提醒，不触发回复）；
    // 大窗单窗模式是 sender（发消息+收回复）
    var mode = state.petmode ? "watcher" : "sender";
    var url = proto + location.host + "/ws/chat?user_id=" + encodeURIComponent(state.user_id) +
              "&role_id=" + encodeURIComponent(state.role_id) + "&mode=" + mode;
    try { if (state.ws) state.ws.close(); } catch (e) {}
    state.ws = new WebSocket(url);
    state.ws.onmessage = function (evt) {
      var msg; try { msg = JSON.parse(evt.data); } catch (e) { return; }
      if (msg.type === "connected") { /* 连接建立 */ }
      else if (msg.type === "thinking") { setPending(true); }
      else if (msg.type === "reply") {
        setPending(false);
        showBubble(msg.content || "");
        // 对话联动：优先取窗口A下发的 behavior（表情/口型/动作），
        // 没有则回退到"前端猜"（+0 契约 §3.2：向后兼容，对方未完成时不坏）
        onRoleTalk(msg.content || "", msg.behavior);
        bus.emit("message", { role_id: msg.role_id, content: msg.content, behavior: msg.behavior });
      } else if (msg.type === "proactive" || msg.type === "reminder") {
        showBubble(msg.content || msg.text || "");
        onRoleTalk(msg.content || msg.text || "", msg.behavior);
        bus.emit("push", msg);
      }
    };
    state.ws.onclose = function () {
      // 断线自动重连
      setTimeout(function () { if (!state.pending) try { connectWS(); } catch (e) {} }, 3000);
    };
  }

  function sendText(text) {
    if (!state.ws || state.ws.readyState !== 1) return;
    text = (text || "").trim();
    if (!text) return;
    state.ws.send(JSON.stringify({ content: text }));
    showBubble("你：" + text, "user");
    bus.emit("send", { content: text });
  }

  // ============================================================
  // UI 辅助
  // ============================================================
  function setPending(on) {
    state.pending = on;
    el.bubble.classList.toggle("typing", on);
    if (on) { el.bubbleText.textContent = "……"; el.bubble.classList.remove("hidden"); }
  }

  // 气泡文本清洗（petmode 专用，空间紧张时使用）：
  //   1) 去掉括号内的动作/旁白描写（全角（）与半角()，可嵌套，逐对去最内层）；
  //      角色回复常用（笑）（摸摸头）（内心 OS）这类，应用端回复保留，气泡里不展示。
  //   2) 合并所有换行为一行：去掉 \n 并用单个空格衔接，靠气泡框架默认换行。
  //   3) 收尾：折叠多余空白、去首尾空白。
  function cleanBubbleText(text) {
    var s = (text || "").replace(/\r\n?/g, "\n");

    // 去括号内容（含括号本身）。逐对去掉最内层的成对括号，直到没有成对为止，
    // 可正确覆盖嵌套（如「（笑着（轻轻）说）」）与多组括号。
    var guard = 0;
    var had = true;
    while (had && guard < 20) {
      had = false;
      s = s.replace(/（[^（）()]*）/g, function (m) { had = true; return ""; })
           .replace(/\([^（）()]*\)/g, function (m) { had = true; return ""; });
      guard++;
    }

    // 合并换行为单个空格（气泡框架默认换行，这里不再保留原文换行）
    s = s.replace(/\n+/g, " ");
    // 折叠重复空白/空格，去掉行首行尾（含中英文标点前的多余空格）
    s = s.replace(/[ \t\u3000]{2,}/g, " ")
         .replace(/\s+([，。！？；：、,.!?;:])/g, "$1")
         .replace(/^\s+|\s+$/g, "");
    return s;
  }

  function escapeHtml(s) {
    return (s || "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function renderBubbleText(text) {
    el.bubbleText.innerHTML = escapeHtml(text).replace(/\n/g, "<br>");
  }

  // 长回复字号自适应：头顶上方净空间约 26% 窗口高，放不下时逐步缩小字号，
  // 让全部文字塞进这段空间（不动滚动、不裁切）。仅在 petmode 生效。
  function fitBubbleText() {
    if (!state.petmode) return;
    var budget = Math.max(80, window.innerHeight * 0.26);
    var fs = 9, guard = 0;   // 基准字号随整体缩至 65%（原 14px→9px）
    el.bubbleText.style.fontSize = fs + "px";
    while (el.bubbleText.offsetHeight > budget && fs > 6 && guard < 20) {
      fs -= 0.5;
      el.bubbleText.style.fontSize = fs + "px";
      guard++;
    }
  }

  function showBubble(text, kind) {
    renderBubbleText(cleanBubbleText(text));
    el.bubble.classList.remove("typing", "hidden");
    fitBubbleText();
  }

  function showStatus(t) { if (el.status) { el.status.textContent = t || ""; el.status.classList.remove("hidden"); } }
  function hideStatus() { if (el.status) el.status.classList.add("hidden"); }
  function showError(msg) {
    if (el.errorMsg) el.errorMsg.textContent = msg || "未知错误";
    if (el.error) el.error.classList.remove("hidden");
  }
  function hideError() { if (el.error) el.error.classList.add("hidden"); }

  function renderModelList(models) {
    el.modelList.innerHTML = "";
    (models || []).forEach(function (m) {
      var li = document.createElement("li");
      li.textContent = m.name || m.id;
      li.dataset.id = m.id;
      if (m.id === state.currentModel) li.classList.add("active");
      li.addEventListener("click", function () {
        el.modelPanel.classList.add("hidden");
        loadModel(m.id).catch(function (e) { showError(String(e && e.message || e)); });
      });
      el.modelList.appendChild(li);
    });
  }

  // ============================================================
  // 模型选择 / 加载
  // ============================================================
  function findModel(id) {
    var models = (state.config && state.config.models) || [];
    return models.find(function (m) { return m.id === id; }) || models[0] || null;
  }

  async function loadModel(id) {
    hideError();
    var m = findModel(id);
    if (!m) { showError("未找到模型"); return; }
    showStatus("正在加载模型…");
    renderer.destroy();
    try {
      await renderer.load(m);
      state.currentModel = m.id;
      if (el.modelName) el.modelName.textContent = m.name || m.id;
      hideStatus();
      HOOK_renderDone(m);
    } catch (e) {
      hideStatus();
      showError("模型加载失败：\n" + (e && e.message || e) + "\n（已回退 CDN 仍失败，请检查本地 live2d 文件）");
    }
  }

  // ============================================================
  // 生命钩子（可被外部脚本覆盖扩展）
  // ============================================================
  function HOOK_renderDone(m) { bus.emit("rendered", m); }

  // ============================================================
  // 模型位置 & 缩放控制器（后门：可拖动 + 滚轮缩放，均持久化）
  // 用 CSS transform: translate(x,y) scale(s) 统一作用于 canvas；
  // 大窗模式：拖动画布位移 + 滚轮缩放；
  // 浮动小窗模式（petmode）：窗口整体移动由 pywebview easy_drag 负责，页内滚轮缩放模型。
  // ============================================================
  // L2Dwidget 实际把 canvas 放进它动态创建的 #live2d-widget 容器，
  // 我们的占位 div 是 #live2d-canvas —— 必须选 #live2d-widget canvas 才对。
  function l2dCanvases() {
    var w = document.getElementById("live2d-widget");
    if (w) return Array.prototype.slice.call(w.querySelectorAll("canvas"));
    return [];
  }

  var live2dT = { x: 0, y: 0, s: 1 };

  // petHost：通过 QWebChannel 把窗口尺寸缩放进JS→Qt（方案A）。
  // QWebChannel 注入是异步的（页面加载完成后才 ready），这里缓存并等待。
  var petHost = null;
  function bindPetHost() {
    if (window.Live2DHost) { petHost = window.Live2DHost; return; }
    window.addEventListener("petHostReady", function () { petHost = window.Live2DHost || null; }, { once: true });
  }

  function readSavedT() {
    try {
      var v = JSON.parse(localStorage.getItem("live2d_transform") || "null");
      if (v && typeof v.x === "number") { live2dT.x = v.x; live2dT.y = v.y; if (v.s) live2dT.s = v.s; }
    } catch (e) {}
  }
  function applyT() {
    // petmode：模型跟随窗口拉伸（canvas 100vw/100vh），页内不做 CSS 变换，保持恒等
    if (state.petmode) {
      l2dCanvases().forEach(function (c) {
        c.style.transform = "";
      });
      return;
    }
    l2dCanvases().forEach(function (c) {
      c.style.transform = "translate(" + live2dT.x + "px," + live2dT.y + "px) scale(" + live2dT.s + ")";
    });
    try { localStorage.setItem("live2d_transform", JSON.stringify(live2dT)); } catch (e) {}
  }
  function initCanvasDrag() {
    if (state.dragInit) return;
    state.dragInit = true;
    readSavedT();
    var canvases = l2dCanvases();
    if (!canvases.length) { setTimeout(initCanvasDrag, 400); return; }
    var canvas = canvases[0];

    canvas.style.transition = "none";
    canvas.style.cursor = "move";
    applyT();

    // 注意：petmode 下窗口整窗移动由 pywebview easy_drag 负责，页内不做拖动（避免抢事件），
    // 但保留滚轮缩放；大窗模式则页内拖动+缩放都启用。
    if (!state.petmode) {
      var dragging = false, sx = 0, sy = 0, ox = 0, oy = 0;

      function down(e) {
        dragging = true; sx = e.clientX; sy = e.clientY; ox = live2dT.x; oy = live2dT.y;
        canvas.style.transition = "none";
        if (e.preventDefault) e.preventDefault();
      }
      function move(e) {
        if (!dragging) return;
        live2dT.x = ox + (e.clientX - sx);
        live2dT.y = oy + (e.clientY - sy);
        applyT();
      }
      function up() { dragging = false; }

      canvas.addEventListener("mousedown", down);
      // 鼠标命中的可能是盖住的 #live2d-canvas 占位 div，也允许从这里开始拖
      if (el.canvas && el.canvas !== canvas) {
        el.canvas.addEventListener("mousedown", down);
      }
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);

      // 触摸
      canvas.addEventListener("touchstart", function (e) {
        var t = e.touches[0]; down({ clientX: t.clientX, clientY: t.clientY, preventDefault: function () { e.preventDefault(); } });
      }, { passive: false });
      if (el.canvas && el.canvas !== canvas) {
        el.canvas.addEventListener("touchstart", function (e) {
          var t = e.touches[0]; down({ clientX: t.clientX, clientY: t.clientY, preventDefault: function () { e.preventDefault(); } });
        }, { passive: false });
      }
      window.addEventListener("touchmove", function (e) {
        if (!dragging) return; var t = e.touches[0]; move({ clientX: t.clientX, clientY: t.clientY });
      }, { passive: false });
      window.addEventListener("touchend", up);
    }

    // 滚轮缩放（所有模式都启用：在窗口任意位置滚动调整模型大小）
    // 注意：鼠标命中的顶层元素可能是盖住的 #live2d-canvas 占位 div，而不是 L2Dwidget 的 canvas，
    // 所以 wheel 监听绑到 window 上，任意位置滚轮都能缩放。
    window.addEventListener("wheel", function (e) {
      e.preventDefault();
      var factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
      if (state.petmode) {
        // 方案A：缩放整个窗口（保持长宽比），画布/模型随窗口等比例缩放，
        // 窗口永远贴合模型，无多余透明区。
        var cw = window.innerWidth, ch = window.innerHeight;
        var ratio = cw / ch;           // 记住当前窗口宽高比
        var nh = ch * factor;
        var nw = nh * ratio;           // 保持同比例放大/缩小窗口
        resizeHostWindow(nw, nh);
      } else {
        live2dT.s = Math.min(5, Math.max(0.2, live2dT.s * factor));
        applyT();
        bus.emit("scale", { scale: live2dT.s });
      }
    }, { passive: false });
  }

  // ============================================================
  // 视线跟随（自建，不依赖运行时内部 rAF 时序）
  // L2Dwidget 原视线依赖 内部 mousemove→setPoint→rAF 渲染循环。在透明/不激活
  // 悬浮窗或无 GPU 的 headless 下 rAF 会被 Chromium 暂停，导致眼睛不跟。
  // 这里自己监听 window mousemove，缓存最近鼠标位置，用独立 rAF(或降级 setInterval)
  // 把视线直接写进模型参数——只要模型渲染在跑，眼睛就会跟。
  // ============================================================
  var eye = { x: 0, y: 0, has: false, raf: 0, useRaf: true };
  // 关键：runtime 的 rAF 循环每帧调用 c.setDrag(d.getX(), d.getY())，会用它内部
  // 的 targetPoint（透明/不激活窗里没收到鼠标时恒为 0）覆盖模型 dragX/dragY，
  // 导致我外部 setDrag 的眼睛值每帧被写回 0。
  // 解法：把 c.setDrag 替换成"我的全局视线值"版本——runtime 之后读到的就是我的值。
  function patchManagerDrag() {
    try {
      var c = window.__l2dManager;
      if (!c || c.__dragPatched) return;
      var orig = c.setDrag;
      var self = this;
      c.setDrag = function (dx, dy) {
        // 用我的视线值（若已有），否则退回参数
        var ex = eye.has ? eye.x : dx;
        var ey = eye.has ? eye.y : dy;
        // 同步给所有模型实例
        for (var i = 0; i < c.numModels(); i++) {
          var m = c.getModel(i);
          if (m && typeof m.setDrag === 'function') m.setDrag(ex, ey);
        }
        if (orig) try { return orig.call(c, ex, ey); } catch (e) {}
      };
      c.__dragPatched = true;
    } catch (e) {}
  }
  function eyeApply() {
    if (!eye.has) return;
    renderer.setDrag(eye.x, eye.y);
  }
  function setCursor(dx, dy) {
    eye.x = Math.max(-1, Math.min(1, dx || 0));
    // 上下方向反转：鼠标在角色下方(e.clientY大/dy正)时，眼睛应往下看，
    // 但运行时 PARAM_EYE_BALL_Y 正值会让眼睛上偏（实测），故取反。
    eye.y = Math.max(-1, Math.min(1, -(dy || 0)));
    eye.has = true;
    patchManagerDrag();
    eyeApply();
  }
  function eyeLoop(now) {
    eyeApply();
    eye.raf = requestAnimationFrame(eyeLoop);
  }
  function initEyeTrack() {
    if (state.eyeInit) return;
    state.eyeInit = true;
    window.addEventListener("mousemove", function (e) {
      // 相对窗口中心归一化：X 向右正、Y 向下正，范围 [-1,1]
      var w = window.innerWidth || 400, h = window.innerHeight || 640;
      var cx = (w / 2), cy = (h / 2);
      eye.x = Math.max(-1, Math.min(1, (e.clientX - cx) / (w / 2)));
      eye.y = -Math.max(-1, Math.min(1, (e.clientY - cy) / (h / 2)));  // y 取反（见 setCursor）
      eye.has = true;
      patchManagerDrag();
      eyeApply();
    });
    // 可见性恢复 / 窗口获得事件时也立即刷新一次
    window.addEventListener("focus", function () { eyeApply(); });
    if (window.requestAnimationFrame) {
      if (eye.useRaf) { eye.raf = requestAnimationFrame(eyeLoop); }
    }
    // 兜底：若 rAF 被暂停，用 setInterval 每 40ms 兜底驱动视线
    setInterval(function () { if (!eye.has) return; eyeApply(); }, 40);
  }

  // 方案A：把目标窗口尺寸发给 Qt（petHost），Qt 负责 window.resize()。
  // petHost 可能还没就绪（WebEngine 注入异步）；先缓存待发，就绪后补发并同步。
  var pendingResize = null;
  function resizeHostWindow(w, h) {
    pendingResize = { w: w, h: h };
    if (petHost && petHost.resizeWindow) {
      try { petHost.resizeWindow(Math.round(w), Math.round(h)); } catch (e) {}
    }
  }
  // 就绪后：若期间有未发出的缩放，一次性补发 + 把 CSS 场景清成跟随窗口
  window.addEventListener("petHostReady", function () {
    petHost = window.Live2DHost || null;
    if (pendingResize && petHost && petHost.resizeWindow) {
      try { petHost.resizeWindow(Math.round(pendingResize.w), Math.round(pendingResize.h)); } catch (e) {}
      pendingResize = null;
    }
  });

  // 暴露控制（后门）
  window.Live2D.setPosition = function (x, y) { live2dT.x = x; live2dT.y = y; applyT(); };
  window.Live2D.setScale = function (s) { live2dT.s = Math.max(0.2, Math.min(5, s)); applyT(); bus.emit("scale", { scale: live2dT.s }); };
  window.Live2D.resetPosition = function () {
    live2dT.x = 0; live2dT.y = 0; live2dT.s = 1;
    try { localStorage.removeItem("live2d_transform"); } catch (e) {}
    applyT();
  };

  // ============================================================
  // 对话联动：情感识别 → 动作 / 表情 映射 + 文字转口型
  // ============================================================
  // 情感分类（关键词法）→ 动作组 + 表情名。motion 取 `startMotion(group,0,FORCE)`。
  var EMOTION_RULES = [
    { keys: ["哈哈", "hhhh", "笑死", "太好笑", "233", "嘻嘻", "嘿嘿", "开心", "好高兴", "喜欢", "爱你", "好棒"], motion: ["smile01", "smile02", "laugh"], expr: "smile01" },
    { keys: ["难过", "伤心", "哭", "呜呜", "难受", "委屈", "想哭", "好难过", "唉"], motion: ["cry01", "cry02", "sad01"], expr: "cry01" },
    { keys: ["生气", "气死", "愤怒", "讨厌", "烦", "滚", "走开", "哼"], motion: ["angry01"], expr: "angry01" },
    { keys: ["真的吗", "不会吧", "哇", "诶", "惊讶", "没想到", "吓", "什么?"], motion: ["surprised01", "surprised02"], expr: "surprised01" },
    { keys: ["害羞", "不好意思", "脸红", "难为情", "羞", "讨厌啦"], motion: ["shame01"], expr: "shame01" },
    { keys: ["", "嗯", "对", "好的", "是的", "明白", "知道了", "晚安", "再见", "拜拜"], motion: ["nod01", "nod02", "bye01"], expr: "default" },
  ];

  function detectEmotion(text) {
    text = (text || "").toLowerCase();
    // 依次匹配；命中最优先类别
    for (var k in EMOTION_RULES) {
      var rule = EMOTION_RULES[k];
      for (var i = 0; i < rule.keys.length; i++) {
        if (rule.keys[i] && text.indexOf(rule.keys[i]) >= 0) return k;
      }
    }
    return "default";
  }

  var EMO = {
    // 由用户文本选表情（开心/难过等），无匹配回 default
    fromText: function (text) {
      var idx = detectEmotion(text);
      var rule = EMOTION_RULES[idx];
      if (rule) {
        renderer.setExpression(rule.expr || "default");
        // 情感动作（若 group 存在）
        var g = rule.motion && rule.motion[0];
        if (g && renderer.motionGroups().indexOf(g) >= 0) {
          renderer.playMotion(g, 0, 3);
        }
      }
      return idx;
    },
  };

  // ---- 文字转口型 ----
  // 说话时按字数周期性驱动 mouth：使 PARAM_MOUTH_OPEN_Y 在 0~1 间正弦摆动，
  // 字数越多摆动越快（像在说话），停顿/句读时闭嘴。不依赖音频（规避听歌被打扰）。
  var lipT = {
    active: false,
    raf: 0,
    startTime: 0,
    dur: 0,
    speed: 1,
    baseOpen: 0.55,  // 说话时 mouth 开合基准（behavior 可覆盖）
  };
  function lipStop() {
    lipT.active = false;
    if (lipT.raf) { cancelAnimationFrame(lipT.raf); lipT.raf = 0; }
    renderer.setLipSync(false);
    renderer.setMouth(0);
  }
  function lipTick(now) {
    if (!lipT.active) return;
    var p = (now - lipT.startTime) / lipT.durEff();
    if (p > 1) { lipStop(); return; }
    // 以 baseOpen 为中心叠加正弦开合；behavior 给的 mouth_open 越高，嘴越张开
    var open = lipT.baseOpen + 0.25 * Math.abs(Math.sin(now / 90 * lipT.speed));
    renderer.setMouth(Math.max(0.05, Math.min(1, open)));
    lipT.raf = requestAnimationFrame(lipTick);
  }
  function lipSpeak(text, durMs, baseOpen) {
    var len = (text || "").length;
    if (!len) { lipStop(); return; }
    lipT.active = true;
    lipT.startTime = performance.now();
    // behavior 提供的口型基准优先；否则用默认
    if (typeof baseOpen === "number") lipT.baseOpen = Math.max(0.25, Math.min(0.9, baseOpen));
    else lipT.baseOpen = 0.55;
    lipT.speed = Math.max(1.5, Math.min(3, len / 12));
    lipT.dur = durMs || Math.max(1500, Math.min(15000, len * 220));
    lipT.durEff = function () { return lipT.dur; };
    renderer.setLipSync(true);
    if (!lipT.raf) { lipT.raf = requestAnimationFrame(lipTick); }
  }

  // 角色开口说话：优先窗口A下发的 behavior（表情/口型/动作），否则前端猜（+0 契约 §3.2）
  // behavior: { emotion?, expression?, mouth_open?, actions?[] }
  function applyBehavior(behavior, text) {
    var used = false;
    var b = behavior || {};
    var groups = renderer.motionGroups();

    // 1) 表情：behavior.expression 是 exp.json 名，优先直接用
    if (b.expression) {
      renderer.setExpression(b.expression);
      used = true;
    }

    // 2) 动作：behavior.actions 数组，逐个播放存在的 mtn（存在即播，吸收未知名）
    if (Array.isArray(b.actions) && b.actions.length) {
      b.actions.forEach(function (act) {
        if (act && groups.indexOf(act) >= 0) {
          renderer.playMotion(act, 0, 3);
          used = true;
        }
      });
    }

    // 3) 口型：behavior.mouth_open 作为说话开合基准（0~1）
    var baseOpen = (typeof b.mouth_open === "number") ? b.mouth_open : null;
    if (baseOpen !== null) {
      used = true;
    }
    lipSpeak(text, undefined, baseOpen);

    return used;
  }

  function onRoleTalk(text, behavior) {
    var b = behavior || null;
    // 若有 behavior 且包含可驱动信息 → 优先消费；否则回退"前端猜"
    var hasBehavior = b && (
      b.expression ||
      (Array.isArray(b.actions) && b.actions.length) ||
      typeof b.mouth_open === "number"
    );
    if (hasBehavior) {
      applyBehavior(b, text);
      return;
    }
    // —— 回退：现有"前端猜"（关键词情感 → 表情动作；估口型）——
    EMO.fromText(text);
    lipSpeak(text);
  }

  // ============================================================
  // 事件绑定
  // ============================================================
  function bindEvents() {
    el.switchBtn.addEventListener("click", function () { el.modelPanel.classList.toggle("hidden"); });
    el.togglePanel.addEventListener("click", function () {
      el.inputbar.classList.toggle("hidden");
      if (!el.inputbar.classList.contains("hidden")) el.input.focus();
    });
    el.send.addEventListener("click", function () { sendText(el.input.value); el.input.value = ""; });
    el.input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { sendText(el.input.value); el.input.value = ""; }
    });
    el.bubble.addEventListener("click", function () {
      // 浮动宠物模式下输入条默认隐藏；点气泡临时呼出以便聊天
      if (state.petmode) el.inputbar.style.display = "flex";
      el.inputbar.classList.remove("hidden");
      el.input.focus();
    });
    el.bubbleText.addEventListener("click", function (e) { e.stopPropagation(); });
    el.retry.addEventListener("click", function () { hideError(); loadModel(state.currentModel); });
  }

  // ============================================================
  // 启动
  // ============================================================
  async function boot() {
    initEl();
    bindEvents();
    bindPetHost();
    hideError();
    // 透明浮动模式（?transparent=1，桌面宠物窗口透明置顶）：去掉页面深色背景，让模型"浮在桌面"
    if (params.get("transparent") === "1") {
      document.documentElement.classList.add("transparent");
      document.body.classList.add("transparent");
    }
    // 浮动宠物模式（?petmode=1，透明小窗只包模型）：隐藏顶栏/输入条/面板，只显示模型
    if (params.get("petmode") === "1") {
      document.documentElement.classList.add("transparent");
      document.body.classList.add("petmode");
      state.petmode = true;
    }
    showStatus("正在初始化…");

    try {
      // 1) 拉环境信息
      var resp = await fetch("/api/live2d/models", { cache: "no-store" });
      var j = await resp.json();
      if (j.code !== 0) throw new Error(j.message || "接口异常");
      state.config = j.data;
      state.models = j.data.models;

      // 2) 确定角色（URL role_id > 默认角色），并读取该角色配置的 Live2D 模型路径
      var roleLive2d = "";
      if (!state.role_id) {
        try {
          var cont = await fetch("/api/contacts").then(function (r) { return r.json(); });
          var def = (cont.data || []).find(function (c) { return c.default; }) ||
                    (cont.data || [])[0];
          state.role_id = (def && def.role_id) || "";
          roleLive2d = (def && def.live2d_model) || "";
        } catch (e) {}
      } else {
        // 已指定 role_id：尝试从 contacts 读取其 live2d_model
        try {
          var cont2 = await fetch("/api/contacts").then(function (r) { return r.json(); });
          var hit = (cont2.data || []).find(function (c) { return c.role_id === state.role_id; });
          roleLive2d = (hit && hit.live2d_model) || "";
        } catch (e) {}
      }

      // 3) 加载运行时（本地 + CDN 回退）
      await ensureRuntime();

      // 4) 加载默认模型：优先用该角色配置的 live2d_model（若在模型列表中存在），
      //    否则回退全局配置的默认模型路径
      var defModel = state.config.default_model || (state.models[0] && state.models[0].id);
      if (roleLive2d && state.models.some(function (m) { return m.id === roleLive2d || m.path === roleLive2d; })) {
        defModel = roleLive2d;
      }
      state.roleLive2d = defModel || "";
      if (defModel) {
        await loadModel(defModel);
        // 两种模式都启用 CSS 变换（petmode 下也启用滚轮缩放，但拖动由窗口 easy_drag 负责，
        // 页内拖动仅大窗模式启用——见 initCanvasDrag 内的 petmode 判断）
        initCanvasDrag();
        initEyeTrack();
        // 注意：窗口自动裁剪到角色(cropToChar)已停用——角色显示大小随窗口缩放，
        // 裁剪窗口无法让角色铺满（见 schedulePetCrop 注释）。改为调大模型 scale 填满。
      }
      else { hideStatus(); }

      // 5) 连接 WS
      if (state.role_id) connectWS();

      // 6) 渲染模型列表
      if (state.models.length > 1) { el.switchBtn.style.display = "inline-flex"; }
      renderModelList(state.models);
      // 7) 启动「后台改模型自动切换」轮询
      startAutoReload();
    } catch (e) {
      hideStatus();
      showError("初始化失败：\n" + (e && e.message || e));
    }
  }

  // ============================================================
  // 后台改模型自动切换（代替手动重启宠物窗）
  // 周期性查询 /api/contacts，若当前角色的 live2d_model 与页面当前生效的不一致，
  // 说明后台刚改过模型路径，用**整页刷新**生效——刷新后 WebGL 干净重建，
  // boot 会重新读取角色配置并加载新模型。
  // （不用页内 loadModel：Cubism2 运行时在同一页面二次 init 会污染旧 WebGL
  //   上下文，报 object does not belong to this context，画面崩成三原色。）
  // ============================================================
  var autoReloadTimer = 0;
  var autoReloadBusy = false;
  var autoReloading = false;
  function startAutoReload() {
    if (autoReloadTimer || !state.role_id) return;
    autoReloadTimer = setInterval(function () {
      if (autoReloadBusy || autoReloading) return;
      autoReloadBusy = true;
      fetch("/api/contacts", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (j) {
        var hit = (j.data || []).find(function (c) { return c.role_id === state.role_id; });
        var want = (hit && hit.live2d_model) || "";
        // 后台改成了空 → 回退默认模型
        if (!want) want = (state.config && state.config.default_model) || "";
        // 后台配置 ≠ 当前生效模型 → 整页刷新生效
        if (want && want !== state.roleLive2d) {
          autoReloading = true;
          location.reload();
        }
      }).catch(function () {}).then(function () {
        autoReloadBusy = false;
      });
    }, 3000);
  }

  // ============================================================
  // 窗口裁剪到角色（petmode）：让窗口=角色轮廓，去掉外部多余一圈
  // 用 L2Dwidget.captureFrame 拿到当前渲染帧，扫描不透明像素包围盒，
  // 得到角色占窗口宽/高的比例 (fx, fy)，通过 petHost.cropToChar 让 Qt
  // 把窗口收紧到角色实际尺寸并贴任务栏。角色经 100vw/stretch 后在新窗铺满。
  // ============================================================
  function schedulePetCrop() {
    if (!state.petmode) return;
    // 等模型渲染稳定 + petHost 桥就绪后测量一次
    setTimeout(function () {
      try {
        if (!window.L2Dwidget || !window.L2Dwidget.captureFrame) return;
        if (!window.Live2DHost || !window.Live2DHost.cropToChar) return;
        window.L2Dwidget.captureFrame(function (dataUrl) {
          var img = new Image();
          img.onload = function () {
            try {
              var cv = document.createElement("canvas");
              cv.width = img.width; cv.height = img.height;
              var ctx = cv.getContext("2d");
              ctx.drawImage(img, 0, 0);
              var d = ctx.getImageData(0, 0, img.width, img.height).data;
              var minx = img.width, miny = img.height, maxx = -1, maxy = -1, cnt = 0;
              for (var y = 0; y < img.height; y++) {
                for (var x = 0; x < img.width; x++) {
                  if (d[(y * img.width + x) * 4 + 3] > 24) { // 不透明像素
                    if (x < minx) minx = x;
                    if (x > maxx) maxx = x;
                    if (y < miny) miny = y;
                    if (y > maxy) maxy = y;
                    cnt++;
                  }
                }
              }
              if (!cnt) return;
              var fx = (maxx - minx) / img.width;
              var fy = (maxy - miny) / img.height;
              // 留一点边距，避免完全贴死
              fx = Math.min(1, fx + 0.02);
              fy = Math.min(1, fy + 0.02);
              window.Live2DHost.cropToChar(fx, fy);
            } catch (e) {}
          };
          img.onerror = function () {};
          img.src = dataUrl;
        });
      } catch (e) {}
    }, 2000);
  }

  // 暴露接口到 window.Live2D
  window.Live2D.renderer = renderer;
  window.Live2D.say = function (t) { showBubble(t); };
  window.Live2D.showBubble = showBubble;
  window.Live2D.playMotion = function (g, p) { renderer.playMotion(g, 0, p == null ? 3 : p); };
  window.Live2D.setExpression = function (n) { renderer.setExpression(n); };
  window.Live2D.send = sendText     ;
  window.Live2D.setRole = function (r) { state.role_id = r; connectWS(); };
  // 对话联动后门
  window.Live2D.emotion = function (t) { EMO.fromText(t); };
  window.Live2D.speak = function (t) { lipSpeak(t); };
  window.Live2D.stopSpeak = lipStop;
  window.Live2D.motions = function () { return renderer.motionGroups(); };
  window.Live2D._setCursor = setCursor;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
