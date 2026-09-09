// main.js — chat 页面入口（ES Module）
// 编排：状态 + WS + 渲染 + 事件绑定。后端接口契约不变。

import { state, cacheKey, setMyName, initMyName } from './state.js';
import { apiPost, saveProfile, fetchContacts, createRoom, joinRoom } from './api.js';
import { WSClient, makeUrl } from './ws.js';
import {
  myNameInitial, renderMyName, renderContacts, loadRoomsIntoList,
  populateRoomMemberList, appendMessage, appendSystem, showThinking,
  removeThinking, loadHistory, loadRoomHistory, setOpenHooks,
} from './render.js';
import { $ } from './ui.js';
import { esc } from './escape.js';
import { toast } from './api.js';

// ===================== 会话切换（供 render 回调，避免循环依赖） =====================

function openPrivate(roleId) {
  state.activeType = 'private';
  state.activeKey = 'default_user';
  state.activeRole = roleId;
  $('chatTitle').textContent = '私聊 · ' + (state.contacts.find(c => c.role_id === roleId)?.display_name || roleId);
  $('roleSelectWrap').style.display = 'flex';
  closeRoomWs();
  connectPrivateWs(roleId);
  $('msgList').innerHTML = '';
  loadHistory(roleId);
  updateActiveUI();
}

function openRoom(roomId) {
  state.activeType = 'room';
  state.activeKey = roomId;
  $('chatTitle').textContent = '群聊 · ' + roomId;
  $('roleSelectWrap').style.display = 'none';
  closePrivateWs();
  connectRoomWs(roomId);
  $('msgList').innerHTML = '';
  loadRoomHistory(roomId);
}

setOpenHooks(openPrivate, openRoom);

function updateActiveUI() {
  document.querySelectorAll('.contact-item').forEach(el => el.classList.remove('active'));
}

// ===================== WS：私聊 =====================

function connectPrivateWs(roleId) {
  closePrivateWs();
  state.privateWs = new WSClient(
    makeUrl(`/ws/chat?user_id=default_user&role_id=${encodeURIComponent(roleId)}`),
    {
      onMessage: (d) => {
        const sender = (d.role_id && state.contacts.find(c => c.role_id === d.role_id)) ? d.role_id : roleId;
        if (d.type === 'thinking') {
          showThinking();
        } else if (d.type === 'reply') {
          removeThinking();
          appendMessage({ role: 'assistant', content: d.content, sender: d.role_id || roleId });
        } else if (d.type === 'proactive') {
          appendMessage({ role: 'assistant', content: d.content, sender: d.role_id || roleId });
        } else if (d.type === 'behavior') {
          // 行为事件：更新"最近一条思考/回复"对应的气泡提示（由最近的 assistant 消息承载）
          // 此处仅缓存供后续渲染；为避免复杂 DOM 查找，把行为挂到最近一条消息。
          const key = cacheKey();
          const arr = state.messagesCache[key];
          if (arr && arr.length) {
            const last = arr[arr.length - 1];
            if (last && last.role === 'assistant') last.behavior = d;
          }
        }
      },
    }
  );
}

function closePrivateWs() {
  if (state.privateWs) { try { state.privateWs.closeByUser(); } catch (e) {} state.privateWs = null; }
}

// ===================== WS：群聊 =====================

function connectRoomWs(roomId) {
  closeRoomWs();
  state.roomWs = new WSClient(
    makeUrl(`/ws/room/${encodeURIComponent(roomId)}?role_id=web_user`),
    {
      onMessage: (d) => {
        if (d.type === 'chat_message') {
          const m = d.data;
          if (m.is_user) appendMessage({ role: 'user', content: m.content, sender: m.sender_role || 'user' });
          else if (m.msg_type === 'system') appendSystem(m.content);
          else appendMessage({ role: 'assistant', content: m.content, sender: m.sender_role });
        }
      },
    }
  );
}

function closeRoomWs() {
  if (state.roomWs) { try { state.roomWs.closeByUser(); } catch (e) {} state.roomWs = null; }
}

// ===================== 发送 =====================

function send() {
  const input = $('msgInput');
  const text = input.value.trim();
  if (!text) return;
  if (state.activeType === 'private') {
    appendMessage({ role: 'user', content: text, sender: state.activeRole });
    if (state.privateWs && state.privateWs.isOpen()) {
      state.privateWs.send({ content: text, role_id: state.activeRole });
    } else {
      toast('连接未就绪，请稍候');
    }
  } else if (state.activeType === 'room') {
    appendMessage({ role: 'user', content: text, sender: '我' });
    if (state.roomWs && state.roomWs.isOpen()) {
      state.roomWs.send({ content: text, role_id: 'web_user', user_id: 'default_user' });
    } else {
      toast('连接未就绪，请稍候');
    }
  }
  input.value = '';
  input.style.height = 'auto';
}

// ===================== 昵称编辑 =====================

function setupUserBar() {
  $('userBar').onclick = () => {
    const bar = $('userBar');
    const name = $('userName');
    const editHint = $('userEditHint');
    const input = document.createElement('input');
    input.value = (state.myName === '我') ? '' : state.myName;
    input.placeholder = '输入昵称，回车保存';
    input.maxLength = 12;
    name.replaceWith(input);
    editHint.textContent = '回车保存 · Esc 取消';
    input.focus(); input.select();
    let done = false;
    const finish = (save) => {
      if (done) return; done = true;
      const val = save ? input.value : (state.myName);
      const newName = document.createElement('div');
      newName.className = 'user-name'; newName.id = 'userName';
      newName.textContent = state.myName;
      input.replaceWith(newName);
      editHint.textContent = '点击修改昵称 ✎';
      if (save) saveMyName(val);
    };
    input.onkeydown = (e) => {
      if (e.key === 'Enter') { e.preventDefault(); finish(true); }
      else if (e.key === 'Escape') { finish(false); }
      else { e.stopPropagation(); }
    };
    input.onblur = () => finish(true);
  };
}

function saveMyName(name) {
  name = (name || '').trim();
  if (!name) name = '我';
  const old = state.myName;
  setMyName(name);
  // 同步后端（不阻塞）
  try {
    saveProfile('default_user', (name === '我') ? '' : name);
  } catch (e) {}
  renderMyName();
  // 重绘当前会话列表消息（保留滚动位置）
  const list = $('msgList');
  const scrollTop = list.scrollTop;
  const saved = state.messagesCache[cacheKey()];
  list.innerHTML = '';
  if (saved) saved.forEach(m => appendMessage(m));
  list.scrollTop = scrollTop;
}

async function loadMyNameFromServer() {
  try {
    const res = await fetch('/api/profile?user_id=default_user');
    const json = await res.json();
    const nick = ((json && json.data && json.data.nickname) || '').trim();
    if (nick) { setMyName(nick); renderMyName(); }
  } catch (e) {}
}

// ===================== 角色下拉 / 群聊成员 =====================

function populateRoleSelect() {
  const sel = $('roleSelect'); sel.innerHTML = '';
  state.contacts.forEach(c => {
    const opt = document.createElement('option');
    opt.value = c.role_id; opt.text = c.display_name;
    const def = c.default || (c.role_id === state.activeRole);
    if (def) { opt.selected = true; if (!state.activeRole) state.activeRole = c.role_id; }
    sel.appendChild(opt);
  });
  sel.onchange = () => {
    state.activeRole = sel.value;
    renderContacts();
    if (state.activeType === 'private') openPrivate(state.activeRole);
  };
}

async function loadContacts() {
  const json = await fetchContacts();
  state.contacts = (json && json.data) || [];
  renderContacts();
  populateRoleSelect();
  populateRoomMemberList();
}

// ===================== 群聊创建弹窗 =====================

function closeRoomModal() { $('roomModal').classList.remove('show'); }
function showRoomModal() { renderContacts(); populateRoomMemberList(); $('roomModal').classList.add('show'); }

async function createRoom() {
  const roomId = $('roomIdInput').value.trim();
  const topic = $('roomTopicInput').value.trim();
  const members = [...document.querySelectorAll('.room-member-cb:checked')].map(cb => cb.value);
  if (!roomId) { toast('请输入群聊名称'); return; }
  const json = await createRoom(roomId, topic);
  if (!json || json.code !== 0) { toast((json && json.message) || '创建失败'); return; }
  for (const roleId of members) {
    await joinRoom(roomId, roleId);
  }
  closeRoomModal();
  $('roomIdInput').value = ''; $('roomTopicInput').value = '';
  openRoom(roomId);
  loadRoomsIntoList();
}

// ===================== 初始化 =====================

function init() {
  initMyName();
  renderMyName();

  // 事件绑定
  $('sendBtn').onclick = send;
  $('msgInput').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });
  $('msgInput').addEventListener('input', function () {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 120) + 'px';
  });
  $('newRoomBtn').onclick = showRoomModal;
  $('searchInput').addEventListener('input', e => renderContacts(e.target.value.trim()));
  setupUserBar();
  window.closeRoomModal = closeRoomModal;
  window.createRoom = createRoom; // HTML 内 onclick 引用

  // 加载
  loadContacts();
  loadMyNameFromServer();
  // 群聊列表轮询（保持与原行为一致；后续可改 WS 驱动）
  setInterval(loadRoomsIntoList, 15000);
}

init();
