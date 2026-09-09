// state.js — 集中状态（原 chat.html 全局变量）
// 每个模块通过 import { state, setMyName, ... } 访问，避免全局命名污染。

export const state = {
  contacts: [],
  activeType: null,   // 'private' | 'room'
  activeKey: null,    // user_id 或 room_id
  activeRole: 'kasumi',
  privateWs: null,
  roomWs: null,
  messagesCache: {},  // key -> [{role, content, sender}]
  typingTimers: {},
  myName: '我',
};

// 会话缓存键：私聊 (user_id, role_id) / 群聊 room_id
export function cacheKey() {
  return state.activeType === 'room'
    ? 'room:' + state.activeKey
    : 'private:' + state.activeKey + ':' + state.activeRole;
}

export function setMyName(name) {
  state.myName = (name && name.trim()) || '我';
  localStorage.setItem('membrain_myName', state.myName);
}

export function initMyName() {
  state.myName = localStorage.getItem('membrain_myName') || '我';
}
