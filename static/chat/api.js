// api.js — fetch 统一封装
// 统一：JSON 解析、错误处理、toast、防竞态（AbortController 可选）
// 后端接口契约保持不变，仅做前端封装。

// 简单 toast（注入 body，纯前端，不动后端）
let toastTimer = null;
export function toast(msg, type = 'error') {
  const body = document.body;
  let el = document.getElementById('membrain-toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'membrain-toast';
    el.style.cssText =
      'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:9999;' +
      'background:#333;color:#fff;padding:8px 16px;border-radius:8px;font-size:13px;' +
      'box-shadow:0 2px 12px rgba(0,0,0,.3);opacity:0;transition:opacity .25s;' +
      'max-width:80vw;word-break:break-word;';
    body.appendChild(el);
  }
  el.textContent = (type === 'success' ? '✅ ' : '⚠️ ') + msg;
  el.style.background = type === 'success' ? '#07c160' : '#333';
  requestAnimationFrame(() => { el.style.opacity = '1'; });
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.style.opacity = '0'; }, 2600);
}

async function request(url, options = {}) {
  const { data, signal, silent = false } = options;
  const init = { method: options.method || 'GET', signal };
  if (data !== undefined) {
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(data);
  }
  try {
    const res = await fetch(url, init);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (e) {
    if (e.name === 'AbortError') throw e; // 竞态取消，交给调用方
    if (!silent) console.error('API 请求失败', url, e);
    return null;
  }
}

// 业务约定：{ code:0, data } 成功
export async function apiGet(url, opts = {}) {
  const json = await request(url, { ...opts, method: 'GET' });
  return json;
}

export async function apiPost(url, data, opts = {}) {
  return request(url, { ...opts, method: 'POST', data });
}

export async function apiDelete(url, opts = {}) {
  return request(url, { ...opts, method: 'DELETE' });
}

// 封装常见后端接口（契约完全复用）
export function fetchContacts() {
  return apiGet('/api/contacts');
}
export function fetchHistory(userId, roleId, n = 30) {
  return apiGet(`/api/history?user_id=${encodeURIComponent(userId)}&role_id=${encodeURIComponent(roleId)}&n=${n}`);
}
export function fetchRooms() {
  return apiGet('/api/rooms');
}
export function fetchRoomMessages(roomId, n = 30) {
  return apiGet(`/api/rooms/${encodeURIComponent(roomId)}/messages?n=${n}`);
}
export function saveProfile(userId, nickname) {
  return apiPost('/api/profile', { user_id: userId, nickname });
}
export function createRoom(roomId, topic) {
  return apiPost('/api/rooms/create', { room_id: roomId, topic });
}
export function joinRoom(roomId, roleId) {
  return apiPost(`/api/rooms/${encodeURIComponent(roomId)}/join`, { role_id: roleId });
}
export function fetchRoomConfig(roomId) {
  return apiGet(`/api/rooms/${encodeURIComponent(roomId)}/config`);
}
export function saveRoomConfig(roomId, cfg) {
  return apiPost(`/api/rooms/${encodeURIComponent(roomId)}/config`, cfg);
}
