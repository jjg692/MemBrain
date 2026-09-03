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
  // 表现层配置 (PET_CFG) —— 待机 / 点击 / 口型全部可配置
  // 合并优先级（低 → 高）：
  //   默认值 < 后端 /api/live2d/config 的 pet 段 < window.Live2D.config.pet
  //   < URL 参数 (pet_<key>, 扁平覆盖)
  // 说明：这是表现层参数，不改契约事件；窗口A下发的 behavior 仍是最高优先级信源，
  //       但 behavior 缺席时（前端猜回退）及待机/点击行为完全由这套配置驱动。
  // ============================================================
  var PET_CFG = {
    lip: {
      amp: 0.25,          // 口型正弦振幅（绕 baseOpen 摆动）
      minOpen: 0.05,      // 口型最小开合
      maxOpen: 1.0,       // 口型最大开合
      baseDefault: 0.5,   // 无情绪/无 behavior 时的口型基准
      minSpeed: 1.5,      // 嘴动频率下限（Hz 系数）
      maxSpeed: 3.2,      // 嘴动频率上限
      durPerChar: 220,    // 每字说话时长(ms)
      minDur: 1500,       // 最短说话时长
      maxDur: 15000,      // 最长说话时长
      pitchRange: 0.9,    // pitch_hint 对嘴速的调制范围（pitch 高→嘴更快，低→更慢）
    },
    tap: {
      enabled: true,      // 点击宠物响应开关
      cooldown: 2500,     // 点击防抖(ms)
      reactions: ["wink", "smile", "smile", "nod", "surprised", "wink"],
    },
    idle: {
      enabled: true,      // 待机表现开关
      loopMs: 9000,       // 待机动作间隔(ms)
      idleMs: 3500,       // 距上次交互多久进入待机(ms)
      actions: ["nod", "wink", "smile", "surprised", "shrug", "nod", "wink", "idle"],
      moodChance: 0.5,    // 每次待机动作附带表情微变的概率
      moodSleep: 0.28,    // 微变中"犯困"占比
      moodSmile: 0.22,    // 微变中"微笑"占比
      exprMs: 1800,       // 临时表情持续(ms)
    },
  };

  // URL 参数扁平覆盖表：pet_<key> -> PET_CFG[section][field]
  // 值类型由 PIC 推导（数值/布尔/字符串），逗号分隔的原数组用 JSON.parse 尝试。
  function applyUrlPetCfg(cfg) {
    var s2v = function (raw, hint) {
      if (raw == null) return hint;
      if (hint === true || hint === false) return raw === "true" || raw === "1";
      if (typeof hint === "number") { var n = Number(raw); return isNaN(n) ? hint : n; }
      return raw;   // 字符串
    };
    for (var i = 0; i < params_keys_pet.length; i++) {
      var key = params_keys_pet[i];
      var raw = params.get(key);                 // 如 pet_lip_amp
      if (raw == null) continue;
      var parts = key.slice(4).split("_");       // 去掉 pet_ 前缀，按 _ 分层
      // 逐层下钻，最后一段是字段名
      var cur = cfg;
      var ok = true;
      for (var j = 0; j < parts.length - 1; j++) { cur = cur[parts[j]]; if (!cur) { ok = false; break; } }
      if (!ok) continue;
      var field = parts[parts.length - 1];
      if (!(field in cur)) continue;
      cur[field] = s2v(raw, cur[field]);
    }
    return cfg;
  }
  // URL 中所有 pet_ 开头的参数名（供 applyUrlPetCfg 使用）
  var params_keys_pet = [];
  params.forEach(function (v, k) { if (k.indexOf("pet_") === 0) params_keys_pet.push(k); });

  // 深层浅合并：把 src 合并进 dst（仅当 dst 有同名段/字段）。返回 dst。
  function mergePetCfg(dst, src) {
    if (!src || typeof src !== "object") return dst;
    for (var k in dst) {
      if (src[k] === undefined) continue;
      if (dst[k] && typeof dst[k] === "object" && !Array.isArray(dst[k]) &&
          src[k] && typeof src[k] === "object" && !Array.isArray(src[k])) {
        mergePetCfg(dst[k], src[k]);
      } else if (Array.isArray(dst[k]) && Array.isArray(src[k])) {
        if (src[k].length) dst[k] = src[k].slice();
      } else {
        dst[k] = src[k];
      }
    }
    return dst;
  }

  // 解析最终表现层配置：合并默认 + 后端 + window后门 + URL 参数
  function resolvePetCfg(backendData) {
    var cfg = JSON.parse(JSON.stringify(PET_CFG));           // 深拷贝默认
    if (backendData && backendData.pet) mergePetCfg(cfg, backendData.pet);
    var w = window.Live2D.config || {};
    if (w.pet) mergePetCfg(cfg, w.pet);
    applyUrlPetCfg(cfg);
    return cfg;
  }

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
      // 保留 null：运行时渲染条件 `null == lipSync` 才用 lipSyncValue 写 mouth。
      // 传 true/false 会关闭该通道（true/false 都不是 null）。
      try { m.setLipSync(on === null ? null : !!on); } catch (e) {}
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
    // 通用参数驱动：写入任意 PARAM_*（A 状态层 / 细粒度控制用）。
    // weight: 0~1 覆盖权重（越大越覆盖模型动画给出的值）。返回是否写入成功。
    setParam: function (id, v, weight) {
      var m = liveModel();
      if (!m || !m.live2DModel) return false;
      try { m.live2DModel.setParamFloat(id, v, weight == null ? 1 : weight); return true; } catch (e) {}
      return false;
    },
    // 批量参数驱动（每帧由状态层调用）：写入目标值，返回成功写入的计数。
    setParams: function (map, weight) {
      var m = liveModel();
      if (!m || !m.live2DModel) return 0;
      var n = 0;
      try {
        var w = weight == null ? 1 : weight;
        for (var id in map) {
          if (Object.prototype.hasOwnProperty.call(map, id)) {
            m.live2DModel.setParamFloat(id, map[id], w);
            n++;
          }
        }
      } catch (e) {}
      return n;
    },
    // 读取当前参数值(供"释放给A前"对齐B与A的当前值,避免抽手时跳变)
    getParam: function (id) {
      var m = liveModel();
      if (!m || !m.live2DModel) return 0;
      try { return m.live2DModel.getParamFloat(id) || 0; } catch (e) {}
      return 0;
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
  // 模型是否就绪（有管理的模型实例，且已拿到 live2DModel）。
  // 点击/口型/表情都依赖它；未就绪时避免静默失效，改用重试或轻反馈。
  function modelReady() {
    try {
      var m = liveModel();
      return !!(m && m.live2DModel);
    } catch (e) { return false; }
  }
  // 模型就绪后再执行 fn（最多 retry 次，间隔 250ms）；始终给一个 fallback 渲染反馈。
  function whenModelReady(fn, fallback, retry) {
    var n = retry == null ? 20 : retry;
    if (modelReady()) { try { fn(); } catch (e) {} return; }
    if (n <= 0) { try { if (fallback) fallback(); } catch (e) {} return; }
    setTimeout(function () { whenModelReady(fn, fallback, n - 1); }, 250);
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
    // 最近一次 reply 的正文（behavior 事件不带 content，需借用它来驱动口型/表情）
    var lastReplyText = "";
    state.ws.onmessage = function (evt) {
      var msg; try { msg = JSON.parse(evt.data); } catch (e) { return; }
      if (msg.type === "connected") { /* 连接建立 */ }
      else if (msg.type === "thinking") { setPending(true); }
      else if (msg.type === "reply") {
        setPending(false);
        showBubble(msg.content || "");
        lastReplyText = msg.content || "";
        // 对话联动：优先取窗口A下发的 behavior（表情/口型/动作），
        // 没有则回退到"前端猜"（+0 契约 §3.2：向后兼容，对方未完成时不坏）
        onRoleTalk(msg.content || "", msg.behavior);
        bus.emit("message", { role_id: msg.role_id, content: msg.content, behavior: msg.behavior });
      } else if (msg.type === "proactive" || msg.type === "reminder") {
        showBubble(msg.content || msg.text || "");
        lastReplyText = msg.content || msg.text || "";
        onRoleTalk(msg.content || msg.text || "", msg.behavior);
        bus.emit("push", msg);
      } else if (msg.type === "behavior") {
        // 后端内核独立广播的精确行为事件（表情/口型/动作）。
        // 随 reply 一并广播；走"后端精确 behavior"而非前端猜，口型/表情/动作才准。
        // behavior 事件本身不带正文，借用最近一次 reply 的文本驱动口型（否则空文本→口型立即停）。
        onRoleTalk(lastReplyText || msg.content || "", msg);
        bus.emit("message", { role_id: msg.role_id, content: msg.content, behavior: msg });
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
    // 用户说话 → 角色轻声"倾听"反应（点头，增强生命感）
    onUserSay();
    bus.emit("send", { content: text });
    bus.emit("activity");   // 记录一次用户交互（重置待机倒计时）
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
    canvas.style.cursor = "grab";       // 可拖动手型；拖动中变 grabbing
    canvas.addEventListener("mousedown", function () { canvas.style.cursor = "grabbing"; });
    ["mouseup", "mouseleave", "mouseout"].forEach(function (ev) {
      canvas.addEventListener(ev, function () { canvas.style.cursor = "grab"; });
    });
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
    } else {
      // ---- petmode：页面手势识别「拖动 vs 点击」----
      // 透明窗里鼠标事件能进页面（点击已验证有效）。位移超阈值判定为拖动，
      // 调用 petHost.beginDrag 触发 Win32 原生窗口拖动（顺滑）；未位移的快速
      // 释放 = 点击（走 document click 委托 → onPetTap）。
      var pDrag = { down: false, sx: 0, sy: 0, moved: false, handed: false };
      function pDown(e) {
        pDrag.down = true; pDrag.sx = e.clientX; pDrag.sy = e.clientY;
        pDrag.moved = false; pDrag.handed = false;
      }
      function pMove(e) {
        if (!pDrag.down || pDrag.handed) return;
        var dx = e.clientX - pDrag.sx, dy = e.clientY - pDrag.sy;
        if (Math.abs(dx) + Math.abs(dy) > 6) {
          pDrag.moved = true;
          // 触发 Win32 原生窗口拖动（顺滑无抖动），之后系统接管，本页不再移动窗口
          if (window.Live2DHost && window.Live2DHost.beginDrag) {
            pDrag.handed = true;
            try { window.Live2DHost.beginDrag(); } catch (e) {}
          }
        }
      }
      function pUp() {
        if (pDrag.moved) { petDragSupressClick = Date.now(); }   // 拖完（原生接管）抑制一次 click
        pDrag.down = false;
      }
      window.addEventListener("mousedown", pDown);
      window.addEventListener("mousemove", pMove);
      window.addEventListener("mouseup", pUp);
      window.addEventListener("touchstart", function (e) {
        var t = e.touches[0]; pDown({ clientX: t.clientX, clientY: t.clientY });
      }, { passive: false });
      window.addEventListener("touchmove", function (e) {
        var t = e.touches[0]; pMove({ clientX: t.clientX, clientY: t.clientY });
      }, { passive: false });
      window.addEventListener("touchend", pUp);
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
        // 记下该角色新尺寸，重启后恢复；滚轮后重贴一次角色（防极端拉伸未完全贴合）
        setTimeout(function () { recordPetSize(); schedulePetCrop(); }, 350);
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
  // 情感分类（关键词法）→ 动作组 + 表情名 + 口型基准。
  // mouth: 该情绪下说话的口型开合基准（0~1，越大嘴张越开）；
  // speed: 该情绪下嘴动频率系数（开心/激动更快更"雀跃"，平静更缓）。
  var EMOTION_RULES = [
    { keys: ["哈哈", "hhhh", "笑死", "太好笑", "233", "嘻嘻", "嘿嘿", "开心", "好高兴", "喜欢", "爱你", "好棒"], motion: ["smile01", "smile02", "laugh"], expr: "smile01", act: "laugh", mouth: 0.68, speed: 1.25 },
    { keys: ["难过", "伤心", "哭", "呜呜", "难受", "委屈", "想哭", "好难过", "唉"], motion: ["cry01", "cry02", "sad01"], expr: "cry01", act: "cry", mouth: 0.35, speed: 0.85 },
    { keys: ["生气", "气死", "愤怒", "讨厌", "烦", "滚", "走开", "哼"], motion: ["angry01"], expr: "angry01", act: "angry", mouth: 0.55, speed: 1.2 },
    { keys: ["真的吗", "不会吧", "哇", "诶", "惊讶", "没想到", "吓", "什么?"], motion: ["surprised01", "surprised02"], expr: "surprised01", act: "surprised", mouth: 0.8, speed: 1.4 },
    { keys: ["害羞", "不好意思", "脸红", "难为情", "羞", "讨厌啦"], motion: ["shame01"], expr: "shame01", act: "shame", mouth: 0.3, speed: 0.75 },
    { keys: ["", "嗯", "对", "好的", "是的", "明白", "知道了", "晚安", "再见", "拜拜"], motion: ["nod01", "nod02", "bye01"], expr: "default", act: "nod", mouth: 0.5, speed: 1.0 },
  ];

  // 情绪主名 → 口型基准/频率（供 behavior.emotion.primary 联动，行为事件无 mouth_open 时用）
  // 注意：这里 key 要与窗口A BehaviorMapper 可能下发的 primary 中文情绪名尽量对齐，
  // 未命中时回退默认（0.5/1.0），不报错。
  var EMOTION_MOUTH_MAP = {
    "开心": { mouth: 0.68, speed: 1.25 },
    "高兴": { mouth: 0.7, speed: 1.3 },
    "兴奋": { mouth: 0.75, speed: 1.4 },
    "惊讶": { mouth: 0.8, speed: 1.4 },
    "难过": { mouth: 0.32, speed: 0.8 },
    "伤心": { mouth: 0.3, speed: 0.8 },
    "生气": { mouth: 0.5, speed: 1.15 },
    "平静": { mouth: 0.5, speed: 1.0 },
    "害羞": { mouth: 0.28, speed: 0.7 },
    "困":   { mouth: 0.25, speed: 0.6 },
  };

  // 情绪主名 → 表情名（供 behavior.emotion.primary 无 expression 时映射表情，跨角色别名兜底）
  // 与窗口A BehaviorMapper 的 _EMOTION_EXPRESSION 语义对齐。
  var EMOTION_RULES_EXPR = {
    "开心": "smile01", "高兴": "smile01", "愉悦": "smile02", "满足": "smile02",
    "兴奋": "f01", "生气": "angry01", "愤怒": "angry01",
    "难过": "sad01", "伤心": "sad01", "沮丧": "serious01",
    "焦虑": "surprised01", "担心": "serious02", "惊讶": "surprised01",
    "害羞": "shame01", "撒娇": "f02", "感动": "smile03",
    "平静": "default", "平和": "default", "疲惫": "default", "困": "default",
  };

  // ============================================================
  // 行为驱动：语义动作名 → 真实 mtn 组（跨模型解析 / 回退）
  // 窗口A behavior.actions（如 wave/clap/bow/nod/shrug）是**语义名**，
  // 与各模型实际 mtn 组名（nod01/bye01/oowarai01…）不同；且不同角色命名
  // 不同（Kasumi: nod/bye；Kokoro 无 nod01）。这里按候选链逐模型解析，
  // 命中该模型实际存在的组才播放；全不命中则静默跳过（不报错）。
  // ============================================================
  // 语义动作 -> 候选 mtn 组（按优先级；各候选需在 motionGroups() 中存在才算命中）
  var ACTION_MOTION_MAP = {
    "nod":    ["nod01", "nod02", "smile01"],      // 点头（Kokoro 无 nod → 微笑）
    "wave":   ["bye01", "wink01", "smile02"],     // 挥手/再见（Kasumi/Kokoro 均无须 fallback）
    "clap":   ["oowarai01", "kime01", "smile03"], // 鼓掌/欢呼（Kokoro 无 oowarai → kime）
    "bow":    ["jaan01", "kime01", "shame01"],    // 鞠躬/致意（有礼动作）
    "shrug":  ["nf01", "nnf01", "eeto01", "serious01"], // 耸肩/无奈（Kokoro 无 nnf/eeto → nf）
    "wink":   ["wink01", "smile02"],
    "sleep":  ["sleep01", "sleep02", "shame01"],
    "cry":    ["cry01", "cry02", "cry03", "sad01"],
    "laugh":  ["oowarai01", "smile02", "smile01"],
    "smile":  ["smile01", "smile02", "smile03"],
    "idle":   ["idle01", "idle02", "smile01"],
  };
  // 解析：给定语义动作名，返回该模型下第一个存在的 mtn 组；无则 null
  function resolveMotion(semantic, groups) {
    var cands = ACTION_MOTION_MAP[semantic];
    if (!cands) return null;
    for (var i = 0; i < cands.length; i++) {
      if (groups.indexOf(cands[i]) >= 0) return cands[i];
    }
    return null;
  }
  // 播放一个语义动作：找到真实 mtn 组并 playMotion
  function playAction(semantic, priority) {
    var g = resolveMotion(semantic, motionGroups());
    if (g) { renderer.playMotion(g, 0, priority == null ? 3 : priority); return true; }
    return false;
  }

  // ---- 表达式解析与跨角色兜底 ----
  // 运行时按当前模型 exp 名称解析；未命中直接名时，用别名链回退到
  // 该模型存在的近似 exp（Kasumi 的 smile01 在 Kokoro 里是 kokoro_smile01 等）。
  // 上述契约 §3.2：behavior.expression 是 exp.json 名，这里确保"已产出的都能命中"。
  function expressionNames() {
    try {
      var m = window.__l2dManager && window.__l2dManager.getModel && window.__l2dManager.getModel(0);
      var ex = m && m.modelSetting && m.modelSetting.json && m.modelSetting.json.expressions;
      if (Array.isArray(ex)) {
        var names = [];
        ex.forEach(function (e) { if (e && e.name) names.push(e.name); });
        return names;
      }
    } catch (e) {}
    return [];
  }
  // 语义/别名 -> 在该模型下优先命中的 exp 名（别名链）
  var EXPR_ALIAS = {
    "smile":      ["smile01", "kokoro_smile01", "smile02", "smile03"],
    "happy":      ["smile01", "kokoro_smile01", "smile02", "f01"],
    "sad":        ["sad01", "kokoro_sad", "sad02", "cry01"],
    "angry":      ["angry01", "kokoro_serious", "serious01"],
    "surprised":  ["surprised01", "kokoro_suprised", "f01"],
    "shame":      ["shame01", "kokoro_smile02", "smile02"],
    "default":    ["default", "kokoro_default", "idle01"],
    "neutral":    ["default", "kokoro_default"],
    "serious":    ["serious01", "kokoro_serious", "serious02"],
    "cry":        ["cry01", "kokoro_sad", "cry03", "sad01"],
    "excited":    ["f01", "kokoro_special", "smile01"],
    "sleep":      ["default", "kokoro_default", "idle01"],
    "smile01": ["smile01", "kokoro_smile01", "smile02"],
    "smile02": ["smile02", "kokoro_smile02", "smile01", "smile03"],
    "smile03": ["smile03", "kokoro_smile02", "smile02"],
    "sad01":   ["sad01", "kokoro_sad", "sad02"],
    "angry01": ["angry01", "kokoro_serious", "serious01"],
    "surprised01": ["surprised01", "kokoro_suprised", "f01"],
    "shame01": ["shame01", "kokoro_smile02", "smile02"],
    "serious01": ["serious01", "kokoro_serious", "serious02"],
    "serious02": ["serious02", "kokoro_serious", "serious01"],
    "cry01":   ["cry01", "kokoro_sad", "cry03", "sad01"],
    "default": ["default", "kokoro_default", "idle01"],
    "f01":     ["f01", "kokoro_special", "smile01"],
    "f02":     ["f02", "kokoro_special", "smile01"],
    "f03":     ["f03", "kokoro_special", "smile01"],
    "f04":     ["f04", "kokoro_special", "smile01"],
    "f05":     ["f05", "kokoro_special", "smile01"],
    "f06":     ["f06", "kokoro_special", "smile01"],
    "f07":     ["f07", "kokoro_special", "smile01"],
    "f08":     ["f08", "kokoro_special", "smile01"],
    "f09":     ["f09", "kokoro_special", "smile01"],
    "f10":     ["f10", "kokoro_special", "smile01"],
    "f11":     ["f11", "kokoro_special", "smile01"],
    "f12":     ["f12", "kokoro_special", "smile01"],
    "f13":     ["f13", "kokoro_special", "smile01"],
    "f14":     ["f14", "kokoro_special", "smile01"],
    "f15":     ["f15", "kokoro_special", "smile01"],
    "f16":     ["f16", "kokoro_special", "smile01"],
    "f17":     ["f17", "kokoro_special", "smile01"],
    "f18":     ["f18", "kokoro_special", "smile01"],
    "kime01":  ["kime01", "kokoro_serious", "kokoro_smile01", "serious01"],
    "idle01":  ["idle01", "kokoro_default", "default"],
  };
  var _modelExprNames = null;   // 当前模型 exp 名缓存（模型切换后失效）
  var lastExpression = "";       // 最近一次成功设置的表情（待机/回退恢复用）
  // 安全设置表达式：直接名命中用直接名；否则别名链回退到模型实际存在者；
  // 记录 lastExpression 供待机/回退恢复。未命中任何候选则不动。
  function setExpressionSafe(name) {
    if (!name) return false;
    var names = expressionNames();
    if (names.indexOf(name) >= 0) {
      renderer.setExpression(name);
      lastExpression = name;
      return true;
    }
    var chain = EXPR_ALIAS[name];
    if (chain) {
      for (var i = 0; i < chain.length; i++) {
        if (names.indexOf(chain[i]) >= 0) {
          renderer.setExpression(chain[i]);
          lastExpression = chain[i];
          return true;
        }
      }
    }
    return false;
  }

  // ---- 表情临时态：设一段时间后恢复进入前表情（待机/微交互用）----
  var _exprRestoreTimer = 0;
  function setExpressionTimed(name, ms) {
    var prev = lastExpression;              // 记录进入前的表情
    var ok = setExpressionSafe(name);
    if (!ok) return false;
    if (_exprRestoreTimer) { clearTimeout(_exprRestoreTimer); _exprRestoreTimer = 0; }
    // ms 后恢复进入前的表情（若期间没有新的 setExpressionSafe 覆盖）
    _exprRestoreTimer = setTimeout(function () {
      _exprRestoreTimer = 0;
      if (prev && lastExpression === name) setExpressionSafe(prev);   // 仅当仍是临时态才回正
    }, ms || 1800);
    return true;
  }

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
    // 由文本选表情 + 情感动作，返回 {idx, mouth, speed}
    fromText: function (text) {
      var idx = detectEmotion(text);
      var rule = EMOTION_RULES[idx];
      if (rule) {
        // 表情用安全解析（跨角色/别名兜底）
        setExpressionSafe(rule.expr || "default");
        // 情感动作（语义 → 真实 mtn 组），若命中才播
        var sem = rule.act;   // 对应该情绪语义动作（在 EMOTION_RULES 里定义）
        if (sem) playAction(sem, 3);
        else {
          var g = rule.motion && rule.motion[0];
          if (g && motionGroups().indexOf(g) >= 0) renderer.playMotion(g, 0, 3);
        }
        return { idx: idx, mouth: rule.mouth != null ? rule.mouth : 0.55,
                 speed: rule.speed != null ? rule.speed : 1.0 };
      }
      return { idx: "default", mouth: 0.55, speed: 1.0 };
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
    pitch: 1,       // pitch_hint（>1 快高、<1 慢低），调制 mouth 韵律
    baseOpen: 0.5,  // 说话时 mouth 开合基准（behavior 可覆盖）
  };
  function lipStop() {
    lipT.active = false;
    if (lipT.raf) { cancelAnimationFrame(lipT.raf); lipT.raf = 0; }
    // 关掉 lipSyncValue 通道并复位嘴
    renderer.setLipSync(false);
    renderer.setLipSyncValue(0);
    // 说话结束 → 表情恢复默认待机脸（对话时由 applyBehavior/EMO 设置的情绪表情
    // 用 setExpressionSafe 直接置上、没有 timed 恢复，会一直挂着，直到下次点击才回正。
    // 这里在口型结束时统一回默认，符合"对话后回到待机表情"的预期。）
    setExpressionSafe("default");
  }
  function lipTick(now) {
    if (!lipT.active) return;
    var p = (now - lipT.startTime) / lipT.durEff();
    if (p > 1) { lipStop(); return; }
    var cfg = PET_CFG.lip;
    // 以 baseOpen 为中心叠加正弦开合；behavior 给的 mouth_open 越高，嘴越张开。
    // pitch 调制：pitch 高(欢快/激动) → 嘴动更快更"雀跃"，低(低落/平淡) → 更缓。
    var effSpeed = lipT.speed * lipT.pitch;
    var open = lipT.baseOpen + cfg.amp * Math.abs(Math.sin(now / 90 * effSpeed));
    // 用 lipSyncValue 驱动（渲染循环每帧写入 PARAM_MOUTH_OPEN_Y），
    // 而非直接 setParamFloat —— 后者会被渲染循环每帧覆盖，导致嘴不动。
    renderer.setLipSyncValue(Math.max(cfg.minOpen, Math.min(cfg.maxOpen, open)));
    lipT.raf = requestAnimationFrame(lipTick);
  }
  // 文字转口型：baseOpen=情绪对口型开合基准，speedMul=情绪对口型频率系数，
  // pitchHint=窗口A behavior.pitch_hint（>1 快高、<1 慢低）→ 真口型韵律联动。
  function lipSpeak(text, durMs, baseOpen, speedMul, pitchHint) {
    var cfg = PET_CFG.lip;
    var len = (text || "").length;
    if (!len) { lipStop(); return; }
    // 模型就绪后才开始口型（此前 getModel 未就绪时口型静默不动）。
    whenModelReady(function () {
      lipT.active = true;
      lipT.startTime = performance.now();
      if (typeof baseOpen === "number") lipT.baseOpen = Math.max(cfg.minOpen, Math.min(cfg.maxOpen, baseOpen));
      else lipT.baseOpen = cfg.baseDefault;
      var mul = (typeof speedMul === "number") ? Math.max(0.5, Math.min(2, speedMul)) : 1.0;
      lipT.speed = Math.max(cfg.minSpeed, Math.min(cfg.maxSpeed, (len / 10) * mul));
      // pitch 归一化到 [1-pitchRange, 1+pitchRange]，避免过于夸张
      var ph = (typeof pitchHint === "number" && isFinite(pitchHint)) ? pitchHint : 1;
      lipT.pitch = Math.max(1 - cfg.pitchRange, Math.min(1 + cfg.pitchRange, ph));
      lipT.dur = durMs || Math.max(cfg.minDur, Math.min(cfg.maxDur, len * cfg.durPerChar));
      lipT.durEff = function () { return lipT.dur; };
      // 关键：setLipSync(null) 让运行时渲染条件 `null == lipSync` 成立，
      // 每帧用 lipSyncValue 写 PARAM_MOUTH_OPEN_Y，嘴才会动。
      // 传 true/false 都会关闭该通道（true/false != null）。
      renderer.setLipSync(null);
      renderer.setLipSyncValue(lipT.baseOpen);
      if (!lipT.raf) { lipT.raf = requestAnimationFrame(lipTick); }
    });
  }


  // ============================================================
  // A 状态层：情绪持续驱动身体参数（“情绪挂在脸上”，不是一次性 .exp 快照）
  // ------------------------------------------------------------
  // 设计：
  //  - 情绪(primary/valence/intensity) → 一组 PARAM 目标值（脸颊/眼眶/眉毛/嘴形/头部姿态）。
  //  - 独立 rAF 循环 bodyLoop 每帧把“当前值”向“目标值”**平滑插值(ease)**，
  //    再低权重(setParams weight≈0.35)写入，与模型动画/口型/视线叠加而非打架。
  //  - 情绪在两次回复之间**持续保持**（除非被新 behavior 或超时重置），
  //    这正是“状态层”区别于“一次性动作”的关键：会话中情绪一直挂在脸上。
  //  - 只驱动 shape/orientation 参数，刻意避开 PARAM_MOUTH_OPEN_Y(口型通道)、
  //    PARAM_EYE_BALL_*(视线通道)、PARAM_BREATH(呼吸)，避免互相覆盖。
  // ============================================================
  var BODY_EASE = 0.08;      // 插值系数：越大过渡越快（每帧朝向目标靠近的比例）
  var BODY_HOLD_MS = 25000;  // 无新行为时情绪保持多久后平滑回落默认（25s）
  // 情绪主名 → 身体参数目标值。未列出的情绪回退默认脸。
  // PARAM 值域参考：EYE_L/R_OPEN 0~1(+)，BROW_FORM 0~1，BROW_ANGLE -1..1，
  // CHEEK/TEAR 0~1，MOUTH_FORM_01/02 0~1，ANGLE_X/Y/Z -1..1。
  var BODY_TARGETS = {
    happy: {
      "PARAM_EYE_L_OPEN": 1.15, "PARAM_EYE_R_OPEN": 1.15,
      "PARAM_BROW_L_FORM": 0.20, "PARAM_BROW_R_FORM": 0.22,
      "PARAM_CHEEK": 0.25, "PARAM_MOUTH_FORM_01": 0.25,
      "PARAM_ANGLE_Z": 0.04,
    },
    excited: {
      "PARAM_EYE_L_OPEN": 1.35, "PARAM_EYE_R_OPEN": 1.35,
      "PARAM_EYE_SCALE": 1.15, "PARAM_BROW_L_FORM": 0.35, "PARAM_BROW_R_FORM": 0.38,
      "PARAM_CHEEK": 0.35, "PARAM_MOUTH_FORM_01": 0.4, "PARAM_MOUTH_FORM_02": 0.1,
      "PARAM_ANGLE_Z": 0.06,
    },
    sad: {
      "PARAM_EYE_L_OPEN": 0.70, "PARAM_EYE_R_OPEN": 0.70,
      "PARAM_BROW_L_FORM": -0.2, "PARAM_BROW_R_FORM": -0.2,
      "PARAM_BROW_L_ANGLE": 0.5, "PARAM_BROW_R_ANGLE": 0.5,
      "PARAM_TEAR": 0.6, "PARAM_MOUTH_FORM_01": 0.12,
      "PARAM_ANGLE_X": 0.05, "PARAM_ANGLE_Y": -0.05,
    },
    angry: {
      "PARAM_EYE_L_OPEN": 0.85, "PARAM_EYE_R_OPEN": 0.80,
      "PARAM_BROW_L_ANGLE": -0.6, "PARAM_BROW_R_ANGLE": -0.6,
      "PARAM_BROW_L_FORM": -0.15, "PARAM_BROW_R_FORM": -0.15,
      "PARAM_MOUTH_FORM_01": 0.45, "PARAM_ANGLE_Z": 0.05,
    },
    surprised: {
      "PARAM_EYE_L_OPEN": 1.5, "PARAM_EYE_R_OPEN": 1.5,
      "PARAM_EYE_SCALE": 1.2, "PARAM_BROW_L_FORM": 0.6, "PARAM_BROW_R_FORM": 0.6,
      "PARAM_MOUTH_FORM_01": 0.55, "PARAM_MOUTH_FORM_02": -0.2,
      "PARAM_ANGLE_Y": 0.06,
    },
    shy: {
      "PARAM_EYE_L_OPEN": 0.85, "PARAM_EYE_R_OPEN": 0.85,
      "PARAM_BROW_L_FORM": 0.12, "PARAM_BROW_R_FORM": 0.12,
      "PARAM_CHEEK": 1.0, "PARAM_CHEEK2": 0.6,
      "PARAM_MOUTH_FORM_01": 0.2, "PARAM_ANGLE_Z": 0.08, "PARAM_ANGLE_X": 0.06,
    },
    tired: {
      "PARAM_EYE_L_OPEN": 0.45, "PARAM_EYE_R_OPEN": 0.45,
      "PARAM_BROW_L_FORM": -0.1, "PARAM_BROW_R_FORM": -0.1,
      "PARAM_MOUTH_FORM_01": 0.1, "PARAM_ANGLE_Z": 0.08, "PARAM_ANGLE_X": 0.08,
    },
  };
  var BODY_DEFAULT = {
    "PARAM_EYE_L_OPEN": 1.0, "PARAM_EYE_R_OPEN": 1.0,
    "PARAM_EYE_SCALE": 1.0, "PARAM_BROW_L_FORM": 0, "PARAM_BROW_R_FORM": 0,
    "PARAM_BROW_L_ANGLE": 0, "PARAM_BROW_R_ANGLE": 0,
    "PARAM_CHEEK": 0, "PARAM_CHEEK2": 0, "PARAM_TEAR": 0,
    "PARAM_MOUTH_FORM_01": 0, "PARAM_MOUTH_FORM_02": 0,
    "PARAM_ANGLE_X": 0, "PARAM_ANGLE_Y": 0, "PARAM_ANGLE_Z": 0,
  };
  // 情绪名归一化（中文 primary → 内部 key；未命中回退 null）
  var BODY_KEY = {
    "开心": "happy", "高兴": "happy", "愉悦": "happy", "兴奋": "excited",
    "难过": "sad", "伤心": "sad", "沮丧": "sad",
    "生气": "angry", "愤怒": "angry",
    "惊讶": "surprised", "焦虑": "surprised",
    "害羞": "shy", "撒娇": "shy",
    "疲惫": "tired", "困": "tired",
  };
  var body = {
    primary: "",       // 当前情绪主名
    intensity: 0.5,    // 情绪强度 0~1（调制次向导数的权重）
    ts: 0,             // 上次收到行为/情绪的时间戳
    cur: {},           // 当前各参数插值后的值（初始为默认脸）
    targets: {},       // 目标值（初始为默认脸）
    lastKey: "",
    raf: 0,
    active: false,
    // ---- 模式二:释放给 A ----
    releasing: false,  // 是否处于"向A释放"的过渡期(B 每帧压 A 但仍平滑收手)
    released: false,   // 是否已完全放手(B 跳过写入,每帧完全还给 A)
    releaseStart: 0,   // 开始"释放过渡"的时间戳(用于卡 A 待机间隔)
    releaseA: {},      // 释放期间对齐的 A 当前值(避免抽手跳变)
  };
  function bodyDefaultTargets() {
    var t = {};
    for (var k in BODY_DEFAULT) t[k] = BODY_DEFAULT[k];
    return t;
  }
  function bodyInit() {
    body.cur = bodyDefaultTargets();
    body.targets = bodyDefaultTargets();
  }
  // 把情绪(中文主名+强度) → 目标参数表
  function bodyTargetsForKey(key, intensity) {
    var t = bodyDefaultTargets();
    var tgt = BODY_TARGETS[key];
    if (!tgt) return t;
    var it = (typeof intensity === "number" && isFinite(intensity)) ? Math.max(0, Math.min(1, intensity)) : 0.5;
    // 强度调制：把目标值往中间值(默认)拉——强度低时情绪表现更淡
    for (var id in BODY_TARGETS) { /* noop guard */ }
    for (var p in tgt) {
      var def = BODY_DEFAULT[p] || 0;
      if (Object.prototype.hasOwnProperty.call(tgt, p)) {
        t[p] = def + (tgt[p] - def) * (0.4 + 0.6 * it);
      }
    }
    return t;
  }
  // 设置情绪状态(供 applyBehavior/onRoleTalk 调用)。intensity 默认 0.5。
  function bodySetEmotion(primary, intensity) {
    var key = BODY_KEY[primary] || "";
    body.primary = primary || "";
    body.intensity = (typeof intensity === "number" && isFinite(intensity)) ? intensity : 0.5;
    body.ts = Date.now();
    // 首次设置:先初始化 cur/targets 为默认,再设目标(顺序必须:init 在前,否则被覆盖)
    if (!body.active) {
      bodyInit();
      startBodyLoop();
    }
    // 若正处于"释放给A"过渡,收到新情绪 → 立刻取消释放,恢复压写(从A当前值平滑去新情绪)
    if (body.released || body.releasing) {
      body.releasing = false;
      body.released = false;
      // 从 A 当前值开始插值,避免从"已还原的A值"瞬间跳到新情绪
      body.cur = bodyCaptureA();
    }
    if (key !== body.lastKey) {
      body.lastKey = key;
      body.targets = key ? bodyTargetsForKey(key, body.intensity) : bodyDefaultTargets();
    } else {
      // 同一情绪、强度变化 → 直接重算目标（不打断已进行的插值平滑）
      if (key) body.targets = bodyTargetsForKey(key, body.intensity);
    }
  }
  // 重置回默认脸（新会话/超时等）
  // 读取 A(原生)当前各参数值,用于"释放给A"时对齐,避免抽手瞬间跳变。
  function bodyCaptureA() {
    var a = {};
    for (var p in BODY_DEFAULT) {
      if (Object.prototype.hasOwnProperty.call(BODY_DEFAULT, p)) a[p] = renderer.getParam(p);
    }
    return a;
  }
  // 开始"释放给A"过渡:记录 A 当前值;等 A 进入待机空档后再平滑收手
  function bodyReset() {
    body.lastKey = "";
    body.primary = "";
    if (!body.releasing && !body.released) {
      body.releaseA = bodyCaptureA();
      body.releasing = true;
      body.releaseStart = Date.now();
    }
  }
  // 每帧:由运行时 update 钩子在"参数算完之后"叠写情绪参数(weight=1,不被覆盖)
  // 模式二:情绪有效时压写;情绪超时后进入"释放过渡",平滑对齐 A 当前值,
  // 收敛后完全放手(跳过写入,每帧还给 A)。释放时机卡 A 的待机空档(不开口时)。
  function bodyApplyFrame() {
    var m = liveModel();
    if (!m || !m.live2DModel) return;

    // 情况1:已完全放手 → 不写,每帧完全还给 A
    if (body.released) return;

    // 情况2:情绪存在且未超时 → 正常压写
    var timedOut = body.lastKey && (Date.now() - body.ts > BODY_HOLD_MS);
    if (!timedOut && !body.releasing) {
      var out = {};
      for (var p in body.targets) {
        if (!Object.prototype.hasOwnProperty.call(body.targets, p)) continue;
        var cur = (typeof body.cur[p] === "number") ? body.cur[p] : (BODY_DEFAULT[p] || 0);
        var tgt = body.targets[p];
        var nv = cur + (tgt - cur) * BODY_EASE;
        if (Math.abs(nv - tgt) < 0.003) nv = tgt;
        body.cur[p] = nv;
        out[p] = nv;
      }
      renderer.setParams(out, 1);
      return;
    }

    // 情况3:需要释放(超时)但还没开始 → 启动释放过渡(等 A 待机空档)
    if (timedOut && !body.releasing && !body.released) {
      bodyReset();
    }

    // 情况4:释放过渡中。
    //   卡 A 待机空档:仅当 A 不开口(lipT.active=false,无大幅动作)才真正收手,
    //   避免在说话/大动作中途抽手造成跳变。
    if (body.releasing) {
      var nearIdle = !lipT.active;
      if (nearIdle) {
        // 把 B 的当前值平滑逼近 A 的当前值(releaseA);收敛后完全放手
        var out2 = {};
        var done = true;
        for (var p2 in BODY_DEFAULT) {
          if (!Object.prototype.hasOwnProperty.call(BODY_DEFAULT, p2)) continue;
          var av = (typeof body.releaseA[p2] === "number") ? body.releaseA[p2] : (BODY_DEFAULT[p2] || 0);
          var c2 = (typeof body.cur[p2] === "number") ? body.cur[p2] : (BODY_DEFAULT[p2] || 0);
          var n2 = c2 + (av - c2) * BODY_EASE;
          if (Math.abs(n2 - av) < 0.003) n2 = av; else done = false;
          body.cur[p2] = n2;
          out2[p2] = n2;
        }
        renderer.setParams(out2, 1);
        if (done) { body.released = true; body.releasing = false; }
      } else {
        // A 还在开口/大动作中 → 本帧继续维持 B 当前值(不抽手),等下次空档
        renderer.setParams(body.cur, 1);
      }
    }
  }
  // 把叠写钩子挂到运行时每帧 update 之末。
  // 运行时主循环每帧 e.update() 内部调用 this.live2DModel.update() 重算参数,
  // 我在其之后补写 → 情绪参数每帧都被压上去,不会被动作/物理/呼吸覆盖。
  // 与视线跟随的 patchManagerDrag 同思路:改运行时收尾,而不是独立 rAF 抢参数。
  function patchBodyUpdate() {
    try {
      var m = liveModel();
      if (!m || !m.live2DModel || m.live2DModel.__bodyPatched) return;
      var target = m.live2DModel;
      // 需要每个模型实例都 patch;这里针对当前 getModel(0)
      var origUpdate = target.update;
      if (typeof origUpdate !== "function") return;
      target.update = function () {
        try { if (origUpdate) return origUpdate.apply(this, arguments); }
        catch (e) {}
        finally { try { bodyApplyFrame(); } catch (e2) {} }
      };
      target.__bodyPatched = true;
    } catch (e) {}
  }
  function startBodyLoop() {
    if (body.active) return;
    body.active = true;
    // 模型就绪后立即 patch(未就绪则等就绪后再试)
    whenModelReady(function () { patchBodyUpdate(); bodyApplyFrame(); }, function () {}, 40);
  }
  // 暴露后门
  window.Live2D.body = {
    setEmotion: bodySetEmotion,
    reset: function () { bodyReset(); },
    state: function () {
      // 未初始化(从未设情绪)时, targets 为空对象 → 返回默认脸,避免调用方拿到空 {}
      var t = (body.targets && Object.keys(body.targets).length) ? body.targets : bodyDefaultTargets();
      return {
        primary: body.primary, intensity: body.intensity, lastKey: body.lastKey, targets: t,
        releasing: body.releasing, released: body.released,   // 模式二:释放状态(可观察)
      };
    },
  };
  // 角色开口说话：优先窗口A下发的 behavior（表情/口型/动作），否则前端猜（+0 契约 §3.2）
  // behavior: { emotion?, expression?, mouth_open?, actions?[] }
  function applyBehavior(behavior, text) {
    var used = false;
    var b = behavior || {};
    var groups = motionGroups();
    var bEmotion = (b.emotion && b.emotion.primary) || "";

    // 0) A 状态层：把情绪写入 body 状态，持续驱动脸/体态参数（并保持到下一次行为）
    if (bEmotion) {
      var bIntensity = (b.emotion && typeof b.emotion.intensity === "number") ? b.emotion.intensity : 0.5;
      bodySetEmotion(bEmotion, bIntensity);
    }

    // 1) 表情：behavior.expression 是 exp.json 名（或语义名），用安全解析（跨模型/别名兜底）
    if (b.expression) {
      if (setExpressionSafe(b.expression)) used = true;
    } else if (bEmotion) {
      // 无显式 expression，但有情绪名 → 按情绪主名映射到表情（friendly 降级），再安全设置
      var emoExpr = EMOTION_RULES_EXPR[bEmotion];
      if (emoExpr) { if (setExpressionSafe(emoExpr)) used = true; }
      else {
        // 从 EMOTION_RULES 的 keys 反查 expr
        for (var k in EMOTION_RULES) {
          if (EMOTION_RULES[k].keys.indexOf(bEmotion) >= 0) {
            if (setExpressionSafe(EMOTION_RULES[k].expr || "default")) used = true;
            break;
          }
        }
      }
    }

    // 2) 动作：behavior.actions 是**语义名**（wave/clap/bow/nod/shrug）→ 解析到真实 mtn 组
    if (Array.isArray(b.actions) && b.actions.length) {
      b.actions.forEach(function (act) {
        if (act && playAction(act, 3)) used = true;
      });
    }

    // 3) 口型：mouth_open 优先；否则用 emotion.primary 推导；否则默认 —— 表情与口型联动。
    //    pitch_hint（窗口A下发的语音情感基调）融入口型韵律 → 真口型联动。
    var baseOpen = (typeof b.mouth_open === "number") ? b.mouth_open : null;
    var speedMul = null;
    if (baseOpen === null && bEmotion) {
      var em = EMOTION_MOUTH_MAP[bEmotion];
      if (em) { baseOpen = em.mouth; speedMul = em.speed; }
    }
    if (baseOpen !== null) used = true;
    // TTS 介入前：对话口型时长固定 5s（不按字数字长计算，
    // 避免"对话没口型/口型极短"；等接 TTS 后再按其语音时长驱动）。
    lipSpeak(text, 5000, baseOpen, speedMul, b.pitch_hint);

    return used;
  }

  function onRoleTalk(text, behavior) {
    var b = behavior || null;
    bus.emit("activity");   // 收到角色开口 → 重置待机倒计时（说话时不动）
    // 若有 behavior 且包含可驱动信息 → 优先消费；否则回退"前端猜"
    var hasBehavior = b && (
      b.expression ||
      (Array.isArray(b.actions) && b.actions.length) ||
      typeof b.mouth_open === "number" ||
      (b.emotion && b.emotion.primary)
    );
    if (hasBehavior) {
      applyBehavior(b, text);
      return;
    }
    // —— 回退：现有"前端猜"，情绪 → 表情 + 动作 + 同款口型（表情与口型联动）——
    var emo = EMO.fromText(text);
    // TTS 介入前：对话口型时长固定 5s（与 behavior 路径一致）。
    lipSpeak(text, 5000, emo.mouth, emo.speed);
  }

  // 用户输入时角色"倾听/感兴趣"反应（增强生命感，不改对话行为）
  // 用 playAction(语义"nod")以跨模型命中；Kokoro 无 nod01 时自动回退微笑。
  function onUserSay() {
    playAction("nod", 2);
  }

  // ============================================================
  // 点击 / 交互反馈（增强生命感）
  // 点宠物身体 → 随机友好反应（眨眼/微笑/惊讶/张望），带防抖避免连点刷屏。
  // petmode 下窗口整窗移动由 pywebview easy_drag 负责；点击事件仍可正常到达
  // （easy_drag 仅在用户拖动时拦截），故此处不加"拖动则忽略"逻辑，只做点击防抖。
  // ============================================================
  var tapT = { last: 0 };
  // petmode 拖动后抑制一次 click 的时间戳（拖动结束后的 mouseup 会触发 click，需跳过）
  var petDragSupressClick = 0;
  function onPetTap() {
    if (!PET_CFG.tap.enabled) return;
    var now = Date.now();
    if (now - tapT.last < PET_CFG.tap.cooldown) return;   // 防抖
    tapT.last = now;
    // 随机挑一个轻反应（语义动作），并偶尔换一下表情
    var reactions = PET_CFG.tap.reactions || ["wink"];
    var pick = reactions[Math.floor(Math.random() * reactions.length)];
    // 模型就绪后再播放动作/表情；未就绪则延迟重试（最多约 5s），
    // 避免"点它没反应"（此前 getModel 未就绪时 playAction 静默失败）。
    whenModelReady(function () {
      playAction(pick, 4);                 // 高优先级（点它就是想让它有反应）
      if (pick === "surprised") setExpressionTimed("surprised", 1200);
      else if (pick === "wink") setExpressionTimed("smile", 900);
    }, function () {
      // 兜底：模型始终未就绪时，至少给一个可见的"被摸到"反馈（闪烁一下）
    }, 20);
    bus.emit("tap", { reaction: pick });
    bus.emit("activity");   // 点击也算交互，重置待机倒计时
  }

  // ============================================================
  // 待机表现（长时间无交互/无对话 → 宠物自然"活着"）
  // - 每 cfg.idle.loopMs 随机播放一个轻待机动作（低优先级，不打断主动行为）
  // - 附带随机表情微变（如困→眨眼→回归），由 setExpressionTimed 回正 lastExpression
  // - 一旦用户交互/收到消息，立即重置"无交互计时"（避免说话时乱动）
  // - 通过 bus.on("activity") 由 sendText/onRoleTalk/onPetTap 触发 reset
  // ============================================================
  var idleT = {
    timer: 0,
    lastActivity: 0,
    started: false,
    idx: 0,
  };
  function idleReset() {
    idleT.lastActivity = Date.now();
    idleT.started = false;         // 重新进入待机倒计时
  }
  function startIdle() {
    if (!PET_CFG.idle.enabled) return;
    if (idleT.timer || !renderer.available()) return;
    // 观察所有"活动"信号并重置待机倒计时
    bus.on("activity", idleReset);
    idleReset();
    var icfg = PET_CFG.idle;
    idleT.timer = setInterval(function () {
      // 待机被再次禁用 → 停掉
      if (!PET_CFG.idle.enabled) { clearInterval(idleT.timer); idleT.timer = 0; return; }
      // 距上次交互不足 idleMs → 保持安静（进入期）
      if (Date.now() - idleT.lastActivity < icfg.idleMs) { idleT.started = true; return; }
      // 说话中不动（lipT.active 表示正在开口）
      if (lipT.active) return;
      // 随机待机动作（低优先级 2，不打断 3/4 的主动行为）
      var actions = icfg.actions || ["nod"];
      var act = actions[idleT.idx % actions.length];
      idleT.idx++;
      // 偶发表情微变 + 恢复（借助 setExpressionTimed 的表情微调）
      setIdleMood();
      playAction(act, 2);
      bus.emit("idle", { action: act });
    }, icfg.loopMs);
  }
  // 待机时的表情微变（偶尔困/眨眼，之后回正）
  function setIdleMood() {
    var icfg = PET_CFG.idle;
    if (Math.random() >= icfg.moodChance) return null;   // 本次不带表情微变
    var r = Math.random();
    if (r < icfg.moodSleep) return setExpressionTimed("sleep", icfg.exprMs + 700);  // 偶尔犯困
    if (r < icfg.moodSleep + icfg.moodSmile) return setExpressionTimed("smile", icfg.exprMs); // 偶尔微笑
    return null;                                               // 保持当前
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

    // 点击宠物身体 → 交互反馈（委托到 document，canvas 是 L2Dwidget 动态创建的）
    // 排除点击到 UI 控件（输入条/气泡/面板/按钮）的情况，避免误触
    document.addEventListener("click", function (e) {
      var t = e.target;
      var isCanvas = t && (t.tagName === "CANVAS" ||
                           (t.id === "live2d-widget") ||
                           (t.id === "live2d-canvas"));
      if (!isCanvas) return;
      var onUI = t.closest && t.closest("#l2d-inputbar, #l2d-bubble, #l2d-model-panel, .l2d-topbar, #l2d-toggle-panel");
      if (onUI) return;   // 点到 UI 不算摸宠物
      // 拖动结束后的 click 跳过（避免拖完误触发点击反应）
      if (Date.now() - (petDragSupressClick || 0) < 300) return;
      onPetTap();
    });
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

      // 1.5) 解析表现层配置（默认 + 后端 /api/live2d/config.pet + window后门 + URL 参数）
      // 后端 config 拉取失败时静默降级到默认 + URL + window 后门配置。
      var backendPet = null;
      try {
        var cr = await fetch("/api/live2d/config", { cache: "no-store" });
        var cj = await cr.json();
        if (cj.code === 0 && cj.data && cj.data.pet) backendPet = cj.data.pet;
      } catch (e) {}
      PET_CFG = resolvePetCfg(backendPet ? { pet: backendPet } : null);

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
        // 待机表现：长时间无交互时宠物自然"活着"（动作 + 表情微变）
        startIdle();
        // 窗口贴合角色（需求1）：先恢复上次记住的窗口尺寸（避免闪烁），
        // 再做收敛式裁剪——每次量角色 bbox 后把窗口收紧到角色大小，重复直到贴合。
        whenPetHostReady(restoreSavedPetSize);
        schedulePetCrop();
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
  // 窗口贴合角色（需求1）：让窗口=角色轮廓，去掉外部多余一圈
  // 用 L2Dwidget.captureFrame 拿到当前渲染帧，扫描不透明像素包围盒，
  // 得到角色占窗口宽/高的比例 (fx, fy)，通过 petHost.cropToChar 让 Qt
  // 把窗口收紧到角色实际尺寸并贴任务栏。角色经 100vw/stretch 后铺满窗口。
  //
  // 关键：收敛式裁剪。裁剪窗口后模型若随窗口拉伸，角色比例可能再次变化，
  // 所以只做一次收不干净。这里重复测量→裁剪，直到 fx、fy 都接近 1（角色
  // 铺满窗口）或已达重试上限，保证窗口最终贴合角色、无左右约 1/4 空边。
  // ============================================================
  var petCropTries = 0;
  function measurePetBbox(cb) {
    // 用 captureFrame 拿当前渲染帧做像素扫描，得到角色占窗口的 (fx, fy)。
    // 若运行时未就绪则回调 null，由上层收尾（不破坏已有窗口尺寸）。
    try {
      if (window.L2Dwidget && window.L2Dwidget.captureFrame) {
        window.L2Dwidget.captureFrame(function (dataUrl) { scanFrame(dataUrl, cb); });
        return;
      }
    } catch (e) {}
    cb(null, null);
  }
  function scanFrame(dataUrl, cb) {
    if (!dataUrl) { cb(null, null); return; }
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
            if (d[(y * img.width + x) * 4 + 3] > 24) {
              if (x < minx) minx = x;
              if (x > maxx) maxx = x;
              if (y < miny) miny = y;
              if (y > maxy) maxy = y;
              cnt++;
            }
          }
        }
        if (!cnt) { cb(null, null); return; }
        var fx = Math.min(1, (maxx - minx) / img.width + 0.02);
        var fy = Math.min(1, (maxy - miny) / img.height + 0.02);
        cb(fx, fy);
      } catch (e) { cb(null, null); }
    };
    img.onerror = function () { cb(null, null); };
    img.src = dataUrl;
  }
  // 记住当前窗口尺寸（每角色独立存，滚轮/fit 后都写）
  function recordPetSize() {
    try {
      var key = "live2d_pet_size_" + (state.role_id || "default");
      localStorage.setItem(key, JSON.stringify({ w: window.innerWidth, h: window.innerHeight }));
    } catch (e) {}
  }
  // 恢复该角色上次记住的窗口尺寸（若 petHost 可用）
  function restoreSavedPetSize() {
    if (!state.petmode) return;
    try {
      var key = "live2d_pet_size_" + (state.role_id || "default");
      var v = JSON.parse(localStorage.getItem(key) || "null");
      if (v && v.w && v.h && petHost && petHost.resizeWindow) {
        petHost.resizeWindow(Math.round(v.w), Math.round(v.h));
      }
    } catch (e) {}
  }
  // petHost（QWebChannel 桥）是异步注入的，可能晚于模型渲染就绪。
  // 等到桥就绪再执行 fn；有次数上限兜底，避免挂死。
  function whenPetHostReady(fn, tries) {
    var n = tries == null ? 20 : tries;
    if (window.Live2DHost && window.Live2DHost.cropToChar) {
      try { fn(); } catch (e) {}
      return;
    }
    if (n <= 0) return;
    setTimeout(function () { whenPetHostReady(fn, n - 1); }, 250);
  }
  function schedulePetCrop() {
    if (!state.petmode) return;
    // 等模型渲染稳定 + petHost 桥就绪后测量裁剪
    setTimeout(function () {
      whenPetHostReady(function () {
        petCropTries = 0;
        doPetCropStep();
      });
    }, 400);
  }
  function doPetCropStep() {
    if (petCropTries >= 6) { recordPetSize(); return; }
    petCropTries++;
    if (!window.Live2DHost || !window.Live2DHost.cropToChar) return;
    measurePetBbox(function (fx, fy) {
      try {
        if (fx == null) { recordPetSize(); return; }
        // 已足够贴合则收尾
        if (fx >= 0.96 && fy >= 0.96) { recordPetSize(); return; }
        window.Live2DHost.cropToChar(fx, fy);
        // 等 Qt 完成 resize + 页面重排后再量下一次（收敛）
        setTimeout(doPetCropStep, 500);
      } catch (e) { recordPetSize(); }
    });
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
  window.Live2D.speak = function (t, dur, base, speed, pitch) { lipSpeak(t, dur, base, speed, pitch); };
  window.Live2D.stopSpeak = lipStop;
  window.Live2D.motions = function () { return motionGroups(); };
  window.Live2D._setCursor = setCursor;
  // M2/M3 表现层后门：动作/表情/点击/待机（供外部脚本或测试驱动）
  window.Live2D.playAction = playAction;
  window.Live2D.resolveMotion = resolveMotion;
  window.Live2D.setExpressionSafe = setExpressionSafe;
  window.Live2D.setExpressionTimed = setExpressionTimed;
  window.Live2D.onPetTap = onPetTap;
  window.Live2D.expressionNames = expressionNames;
  window.Live2D.idle = { start: startIdle, reset: idleReset };
  window.Live2D.lastExpression = function () { return lastExpression; };
  window.Live2D.applyBehavior = applyBehavior;
  window.Live2D.onRoleTalk = onRoleTalk;
  window.Live2D.emoFromText = function (t) { return EMO.fromText(t); };
  // 表现层配置后门：读取 / 运行时重解析 / 应用（供外部脚本或调试面板）
  window.Live2D.cfg = function () { return PET_CFG; };
  window.Live2D.resolvePetCfg = function (b) { return resolvePetCfg(b); };
  window.Live2D.setCfg = function (c) { if (c && typeof c === "object") PET_CFG = c; return PET_CFG; };
  window.Live2D.lip = function () { return lipT; };   // 只读口型状态（含 baseOpen/speed/pitch）

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
