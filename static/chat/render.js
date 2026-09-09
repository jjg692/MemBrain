// render.js — 渲染模块（会话列表 / 消息 / 群聊 / 行为事件）
// 尽量保持与原 chat.html 行为等价；消息渲染增加 behavior 行为事件提示与 thinking 状态。

import { state, cacheKey } from './state.js';
import { esc } from './escape.js';
import { fetchRooms, fetchRoomMessages, fetchHistory } from './api.js';

const $ = id => document.getElementById(id);

// ===================== 我的昵称 =====================
export function myNameInitial() { return (state.myName || '我').trim().charAt(0) || '我'; }
export function renderMyName() {
  $('userName').textContent = state.myName || '我';
  $('userAvatar').textContent = myNameInitial();
}

// ===================== 会话列表（局部更新） =====================
function groupEl(text) {
  const g = document.createElement('div');
  g.className = 'contact-group';
  g.textContent = text;
  return g;
}

function avatarHTML(c) {
  if (c.avatar_url) {
    return `<img src="${c.avatar_url}" onerror="this.style.display='none'"><span class="badge">${esc((c.display_name || '')[0])}</span>`;
  }
  return `<div class="avatar">${esc((c.display_name || 'R')[0])}</div>`;
}

export function renderContacts(filter = '') {
  const list = $('contactList');
  list.innerHTML = '';
  const filtered = state.contacts.filter(c =>
    !filter || (c.display_name || '').includes(filter) || (c.role_id || '').includes(filter));

  if (filtered.some(c => c.role_id === state.activeRole)) {
    list.appendChild(groupEl('私聊'));
  }
  filtered.forEach(c => {
    const div = document.createElement('div');
    div.className = 'contact-item' +
      (state.activeType === 'private' && state.activeKey === 'default_user' && c.role_id === state.activeRole ? ' active' : '');
    div.innerHTML = avatarHTML(c) +
      `<div class="info"><div class="name">${esc(c.display_name)}</div><div class="desc">${esc(c.description || 'AI 助手')}</div></div>`;
    div.onclick = () => openPrivate(c.role_id);
    list.appendChild(div);
  });

  if (filtered.length) list.appendChild(groupEl('群聊'));
  const ph = document.createElement('div');
  ph.className = 'contact-item';
  ph.id = 'roomListPlaceholder';
  ph.style.color = '#9aa0a6';
  ph.textContent = '加载群聊中…';
  list.appendChild(ph);
  loadRoomsIntoList();
}

export async function loadRoomsIntoList() {
  try {
    const json = await fetchRooms();
    const rooms = (json && json.data) || [];
    const list = $('contactList');
    const ph = $('roomListPlaceholder'); if (ph) ph.remove();
    rooms.forEach(r => {
      const div = document.createElement('div');
      div.className = 'contact-item' + (state.activeType === 'room' && state.activeKey === r.room_id ? ' active' : '');
      div.innerHTML = `<div class="avatar" style="background:#5b8ff9">👥</div><div class="info"><div class="name">${esc(r.room_id)}</div><div class="desc">${esc(r.topic || '群聊')} · ${(r.members || []).length} 成员</div></div>`;
      div.onclick = () => openRoom(r.room_id);
      list.appendChild(div);
    });
  } catch (e) { console.error(e); }
}

export function populateRoomMemberList() {
  const box = $('roomMembers'); box.innerHTML = '';
  state.contacts.forEach(c => {
    const label = document.createElement('label');
    label.className = 'member-item';
    label.innerHTML = `<input type="checkbox" value="${c.role_id}" class="room-member-cb"> ${avatarHTML(c)} ${esc(c.display_name)}`;
    box.appendChild(label);
  });
}

// ===================== 消息渲染 =====================
export function scrollBottom() {
  const l = $('msgList');
  l.scrollTop = l.scrollHeight;
}

// 把行为事件转成气泡下方的小提示（纯前端展示，无副作用）
function behaviorHint(behavior) {
  if (!behavior) return '';
  const parts = [];
  if (behavior.emotion) parts.push('🎭' + esc(behavior.emotion));
  if (behavior.expression) parts.push('😊' + esc(behavior.expression));
  if (behavior.action) parts.push('🎬' + esc(behavior.action));
  if (!parts.length) return '';
  return `<div class="behavior-hint">${parts.join(' ')}</div>`;
}

export function appendMessage({ role, content, sender, behavior }) {
  const div = document.createElement('div');
  const isSelf = role === 'user';
  const senderC = state.contacts.find(c => c.role_id === sender);
  const senderName = (senderC && senderC.display_name) || sender || '?';
  const selfName = state.myName || '我';
  div.className = 'msg ' + (isSelf ? 'self' : 'other');

  let av;
  if (isSelf) {
    av = `<div class="avatar" style="background:#5b8ff9">${esc(selfName.trim().charAt(0) || '我')}</div>`;
  } else if (senderC && senderC.avatar_url) {
    av = `<div class="avatar" style="background:#07c160"><img src="${senderC.avatar_url}"></div>`;
  } else {
    av = `<div class="avatar" style="background:#07c160">${esc((senderName || '?')[0])}</div>`;
  }
  div.innerHTML = av +
    `<div class="msg-body"><div class="msg-meta">${isSelf ? esc(selfName) : esc(senderName)}</div>` +
    `<div class="msg-bubble">${esc(content)}</div>` +
    behaviorHint(behavior) +
    `</div>`;
  $('msgList').appendChild(div);
  scrollBottom();

  const key = cacheKey();
  if (!state.messagesCache[key]) state.messagesCache[key] = [];
  state.messagesCache[key].push({ role, content, sender });
}

export function appendSystem(text) {
  const div = document.createElement('div');
  div.className = 'msg system';
  div.innerHTML = `<div class="msg-body"><div class="msg-bubble">${esc(text)}</div></div>`;
  $('msgList').appendChild(div);
  scrollBottom();
}

export function showThinking() {
  removeThinking();
  const div = document.createElement('div');
  div.className = 'msg other thinking'; div.id = 'thinkingMsg';
  const name = state.activeRole + ' 正在思考';
  div.innerHTML = `<div class="avatar" style="background:#07c160">✦</div><div class="msg-body"><div class="msg-bubble">${esc(name)}</div></div>`;
  $('msgList').appendChild(div);
  scrollBottom();
}
export function removeThinking() { const t = $('thinkingMsg'); if (t) t.remove(); }

// ===================== 群聊：角色轮流打字指示器（A 阶段） =====================
// 用房间内 id 化的 thinking 气泡，支持多个角色同时"正在思考"。
let roomTurnSeq = 0;
export function showRoomThinking(roleId) {
  // 若该角色已在思考中，不重复添加
  if ($(`roomThink_${roleId}`)) { const el = $(`roomThink_${roleId}`); el.classList.add('thinking'); return; }
  const seq = ++roomTurnSeq;
  const div = document.createElement('div');
  div.className = 'msg other thinking';
  div.id = `roomThink_${roleId}`;
  div.dataset.seq = seq;
  const senderC = state.contacts.find(c => c.role_id === roleId);
  const name = (senderC && senderC.display_name) || roleId;
  div.innerHTML = `<div class="avatar" style="background:#07c160">✦</div><div class="msg-body"><div class="msg-bubble">${esc(name)} 正在思考…</div></div>`;
  $('msgList').appendChild(div);
  scrollBottom();
}
export function removeRoomThinking(roleId) {
  const el = $(`roomThink_${roleId}`);
  if (el) { el.remove(); }
}
export function clearAllRoomThinking() {
  document.querySelectorAll('[id^="roomThink_"]').forEach(el => el.remove());
}

// ===================== 历史/缓存加载 =====================
export async function loadHistory(roleId) {
  const key = 'private:default_user:' + roleId;
  if (state.messagesCache[key]) {
    state.messagesCache[key].forEach(m => appendMessage(m));
    scrollBottom();
    return;
  }
  try {
    const json = await fetchHistory('default_user', roleId, 30);
    (json.data || []).forEach(m => appendMessage({ role: m.role, content: m.content, sender: roleId }));
    state.messagesCache[key] = json.data || [];
    scrollBottom();
  } catch (e) { console.error(e); }
}

export async function loadRoomHistory(roomId) {
  clearAllRoomThinking();
  const key = 'room:' + roomId;
  if (state.messagesCache[key]) {
    state.messagesCache[key].forEach(m => appendMessage(m));
    scrollBottom();
    return;
  }
  try {
    const json = await fetchRoomMessages(roomId, 30);
    (json.data || []).forEach(m => {
      if (m.msg_type === 'system') appendSystem(m.content);
      else if (m.is_user) appendMessage({ role: 'user', content: m.content, sender: m.sender_role });
      else appendMessage({ role: 'assistant', content: m.content, sender: m.sender_role });
    });
    state.messagesCache[key] = json.data || [];
    scrollBottom();
  } catch (e) { console.error(e); }
}

// ===================== 会话切换（由 main.js 回调注入以避免循环依赖） =====================
export let hooks = { openPrivate: () => {}, openRoom: () => {} };
export function setOpenHooks(openPrivate, openRoom) {
  hooks.openPrivate = openPrivate;
  hooks.openRoom = openRoom;
}
function openPrivate(roleId) { hooks.openPrivate(roleId); }
function openRoom(roomId) { hooks.openRoom(roomId); }
