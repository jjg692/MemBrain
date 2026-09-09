// main.js — chat 页面入口（ES Module）
// 编排：状态 + WS + 渲染 + 事件绑定。后端接口契约不变。

import { state, cacheKey, setMyName, initMyName } from './state.js';
import { apiPost, saveProfile, fetchContacts, createRoom as apiCreateRoom, joinRoom, fetchRoomConfig, saveRoomConfig as apiSaveRoomConfig } from './api.js';
import { WSClient, makeUrl } from './ws.js';
import {
  myNameInitial, renderMyName, renderContacts, loadRoomsIntoList,
  populateRoomMemberList, appendMessage, appendSystem, showThinking,
  removeThinking, loadHistory, loadRoomHistory, setOpenHooks,
  showRoomThinking, removeRoomThinking, clearAllRoomThinking,
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
  $('roomConfigBtn').style.display = 'none';
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
  $('roomConfigBtn').style.display = 'inline-block';
  closePrivateWs();
  connectRoomWs(roomId);
  $('msgList').innerHTML = '';
  loadRoomHistory(roomId);
}

setOpenHooks(openPrivate, openRoom);

// ===================== 群聊设置面板（C 阶段） =====================

async function openRoomConfig() {
  const roomId = state.activeKey;
  if (state.activeType !== 'room' || !roomId) { toast('请先打开一个群聊'); return; }
  const json = await fetchRoomConfig(roomId);
  if (!json || json.code !== 0) { toast('获取房间配置失败'); return; }
  const cfg = json.data || {};
  const members = cfg.member_config || {};
  const nameOf = (rid) => {
    const c = state.contacts.find(x => x.role_id === rid);
    return (c && c.display_name) || rid;
  };
  $('roomConfigBody').innerHTML = `
    <div style="margin:10px 0"><label>是否启用角色接力互相搭话</label>
      <select id="rc_enable_relay">
        <option value="true" ${cfg.enable_relay ? 'selected' : ''}>启用</option>
        <option value="false" ${cfg.enable_relay ? '' : 'selected'}>停用（只回应用户，不互相对话）</option>
      </select></div>
    <div style="margin:10px 0"><label>接力轮数（0=不接力，留空=默认）</label>
      <input id="rc_relay_rounds" type="number" min="0" placeholder="默认"
        value="${cfg.relay_rounds == null ? '' : cfg.relay_rounds}"></div>
    <div style="margin:10px 0"><label>发言风格</label>
      <select id="rc_relay_style">
        <option value="weighted" ${cfg.relay_style === 'weighted' ? 'selected' : ''}>权重挑选（部分角色，避免话痨）(Recommended)</option>
        <option value="all" ${cfg.relay_style === 'all' ? 'selected' : ''}>全员发言（每轮所有角色都开口）</option>
      </select></div>
    <div style="margin:10px 0"><label>并发上限（同时进行的生成数）</label>
      <input id="rc_max_concurrency" type="number" min="1" value="${cfg.max_concurrency || 2}"></div>
    <div style="margin:10px 0"><label>成员参与度</label>
      <div id="rc_members"></div></div>
  `;
  const mc = $('rc_members');
  mc.innerHTML = state.contacts.filter(c => (cfg.members || []).includes(c.role_id))
    .map(c => `
      <div style="display:flex;align-items:center;gap:10px;padding:4px 0">
        <span style="flex:1">${esc(nameOf(c.role_id))}</span>
        <label style="font-size:12px"><input type="checkbox" data-role="${c.role_id}" class="rc-participate"
          ${!(members[c.role_id] && members[c.role_id].participate === false) ? 'checked' : ''}> 参与</label>
        <input class="rc-weight" data-role="${c.role_id}" type="number" min="0" step="0.1" style="width:60px"
          placeholder="权重" value="${(members[c.role_id] && members[c.role_id].weight) || 1}">
      </div>`).join('');
  $('roomConfigModal').classList.add('show');
}

async function saveRoomConfig() {
  const roomId = state.activeKey;
  const cfg = {
    enable_relay: $('rc_enable_relay').value === 'true',
    relay_rounds: $('rc_relay_rounds').value === '' ? null : parseInt($('rc_relay_rounds').value, 10),
    relay_style: $('rc_relay_style').value,
    max_concurrency: parseInt($('rc_max_concurrency').value, 10) || 2,
    member_config: {},
  };
  document.querySelectorAll('.rc-participate').forEach(cb => {
    cfg.member_config[cb.dataset.role] = {
      participate: cb.checked,
      weight: parseFloat(document.querySelector(`.rc-weight[data-role="${cb.dataset.role}"]`).value) || 1,
    };
  });
  const json = await apiSaveRoomConfig(roomId, cfg);
  if (json && json.code === 0) { toast('已保存', 'success'); closeRoomConfigModal(); }
  else toast('保存失败');
}
function closeRoomConfigModal() { $('roomConfigModal').classList.remove('show'); }

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
        if (d.type === 'room_turn') {
          // A 阶段：角色轮流打字指示器
          if (d.status === 'thinking') showRoomThinking(d.role_id);
          else removeRoomThinking(d.role_id);
        } else if (d.type === 'chat_message') {
          const m = d.data;
          // 新真实消息到达时，清理对应角色的"正在思考"占位
          if (m.sender_role) removeRoomThinking(m.sender_role);
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

// 联系人加载：首次失败时自动重试（指数退避 → 封顶），
// 解决桌面/web 端在“后端启动时序”下提前加载导致联系人为空的问题。
// 联系人属关键初始化资源，后端最终会就绪，故重试直到成功为止。
// 重试间隔：1s → 2s → 4s → … 封顶 10s（进程内存存续，跨调用保持）
let _contactsRetryStep = 0;
function _contactsRetryDelay() {
  const delay = Math.min(1000 * Math.pow(2, _contactsRetryStep), 10000);
  _contactsRetryStep = Math.min(_contactsRetryStep + 1, 4);
  return delay;
}

let _contactsRetryTimer = null;
function _clearContactsRetry() { if (_contactsRetryTimer) { clearTimeout(_contactsRetryTimer); _contactsRetryTimer = null; } }

async function loadContacts() {
  try {
    const json = await fetchContacts();
    const list = (json && json.data) || [];
    if (json && Array.isArray(list)) {
      state.contacts = list;
      _clearContactsRetry();
      renderContacts();
      populateRoleSelect();
      populateRoomMemberList();
      return;
    }
    throw new Error('contacts payload 无效');
  } catch (e) {
    console.warn('加载联系人失败，将在稍后自动重试:', e);
    _clearContactsRetry();
    _contactsRetryTimer = setTimeout(() => loadContacts(), _contactsRetryDelay());
  }
}

// ===================== 群聊创建弹窗 =====================

function closeRoomModal() { $('roomModal').classList.remove('show'); }
function showRoomModal() { renderContacts(); populateRoomMemberList(); $('roomModal').classList.add('show'); }

async function createRoom() {
  const roomId = $('roomIdInput').value.trim();
  const topic = $('roomTopicInput').value.trim();
  const members = [...document.querySelectorAll('.room-member-cb:checked')].map(cb => cb.value);
  if (!roomId) { toast('请输入群聊名称'); return; }
  const json = await apiCreateRoom(roomId, topic);
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
  $('roomConfigBtn').onclick = openRoomConfig;
  $('searchInput').addEventListener('input', e => renderContacts(e.target.value.trim()));
  setupUserBar();
  window.closeRoomModal = closeRoomModal;
  window.createRoom = createRoom; // HTML 内 onclick 引用
  window.closeRoomConfigModal = closeRoomConfigModal;
  window.saveRoomConfig = saveRoomConfig;
  window.openRoomConfig = openRoomConfig;

  // 加载
  loadContacts();
  loadMyNameFromServer();
  // 群聊列表轮询（保持与原行为一致；后续可改 WS 驱动）
  setInterval(loadRoomsIntoList, 15000);
}

init();
