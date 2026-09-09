// ws.js — WebSocket 封装 + 自动重连
// 后端契约不变：/ws/chat?user_id=&role_id= （私聊） / /ws/room/{id}?role_id=（群聊）
// 增强：断线指数退避自动重连 + 连接状态提示条（onStatus 回调）。

let reconnectDelay = 1000; // 首重连延迟，指数翻倍，封顶 15000

export function resetReconnect() { reconnectDelay = 1000; }

function makeUrl(path) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${location.host}${path}`;
}

// 连接状态提示条（纯前端）
let statusEl = null;
function statusBar(text) {
  let el = document.getElementById('membrain-ws-status');
  if (!el) {
    el = document.createElement('div');
    el.id = 'membrain-ws-status';
    el.style.cssText =
      'position:fixed;top:8px;left:50%;transform:translateX(-50%);z-index:9998;' +
      'background:#f0a020;color:#fff;padding:4px 14px;border-radius:20px;font-size:12px;' +
      'box-shadow:0 2px 10px rgba(0,0,0,.2);display:none;';
    document.body.appendChild(el);
  }
  if (text) {
    el.textContent = text;
    el.style.display = 'block';
  } else {
    el.style.display = 'none';
  }
}

// 连接管理器：统一创建 WS，绑定消息处理，自动重连
export class WSClient {
  /**
   * @param {string} url 完整 WS url（不含 ws:// 前缀之外的 query 拼接在外部）
   * @param {object} handlers { onMessage, onOpen, onClose, onReconnecting }
   */
  constructor(url, handlers = {}) {
    this.url = url;
    this.handlers = handlers;
    this.ws = null;
    this.closedByUser = false;
    this.connect();
  }
  connect() {
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => {
      if (this.handlers.onOpen) this.handlers.onOpen();
      statusBar('');
      resetReconnect();
    };
    ws.onmessage = (ev) => {
      let d;
      try { d = JSON.parse(ev.data); } catch (e) { return; }
      if (this.handlers.onMessage) this.handlers.onMessage(d);
    };
    ws.onclose = () => {
      if (this.closedByUser) return;
      if (this.handlers.onReconnecting) {
        statusBar('连接断开，正在重连…');
        this.handlers.onReconnecting();
      }
      if (!this.closedByUser) {
        setTimeout(() => this.connect(), reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 15000);
      }
    };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
  }
  send(obj) {
    if (this.ws && this.ws.readyState === 1) {
      this.ws.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }
  closeByUser() {
    this.closedByUser = true;
    try { if (this.ws) this.ws.close(); } catch (e) {}
    this.ws = null;
  }
  isOpen() {
    return !!(this.ws && this.ws.readyState === 1);
  }
}

// 便捷：根据当前 state 创建/切换私聊连接（url 由外部传入）
export { makeUrl };
