// 气泡自动消失策略 —— 纯逻辑单元测试
// 运行: node test/live2d_bubble_test.js
// 覆盖: live2d-page.js 中 PET_CFG.bubble 的展示时长计算（scheduleBubbleHide 公式）、
//       与"新消息重置"“短句保底/长句封顶/用户消息略短/自定义配置”等边界。
// 思路: 气泡时长公式是纯函数（长度×每字时长 → clamp 到 [min,max]），此处独立复刻以便
//       不依赖 DOM/L2Dwidget 即可回归保护；若源文件公式改动，需同步本测试保持一致。
'use strict';

// —— 与 live2d-page.js `scheduleBubbleHide` 完全一致的时长公式 ——
function bubbleDuration(len, kind, cfg) {
  cfg = cfg || {};
  var perChar = (cfg.perCharMs != null ? cfg.perCharMs : 120);
  var minMs = (cfg.minMs != null ? cfg.minMs : 3000);
  var maxMs = (cfg.maxMs != null ? cfg.maxMs : 15000);
  // 用户消息（"你：…"）每字略短
  var base = kind === 'user' ? (len * 80) : (len * perChar);
  return Math.max(minMs, Math.min(maxMs, base));
}

let pass = 0, fail = 0;
function assert(cond, msg) {
  if (cond) { pass++; console.log('  ok - ' + msg); }
  else { fail++; console.error('  FAIL - ' + msg); }
}

// —— 默认配置下的边界 ——
assert(bubbleDuration(1, 'role', {}) === 3000, '极短句(1字) 保底 3000ms');
assert(bubbleDuration(5, 'role', {}) === 3000, '短句(5字) 保底 3000ms (5*120=600→3000)');
assert(bubbleDuration(20, 'role', {}) === 3000, '20字 3000ms (20*120=2400→3000)');
assert(bubbleDuration(25, 'role', {}) === 3000, '25字 3000ms (25*120=3000)');
assert(bubbleDuration(50, 'role', {}) === 6000, '50字 6000ms (50*120=6000)');
assert(bubbleDuration(100, 'role', {}) === 12000, '100字 12000ms');
assert(bubbleDuration(125, 'role', {}) === 15000, '125字 15000ms (125*120=15000)');
assert(bubbleDuration(200, 'role', {}) === 15000, '200字 截断到 maxMs=15000');
assert(bubbleDuration(10000, 'role', {}) === 15000, '超长输入 截断到 15000');

// —— 用户消息略短 ——
assert(bubbleDuration(10, 'user', {}) === 3000, '用户消息(10字) 保底 3000ms (10*80=800)');
assert(bubbleDuration(50, 'user', {}) === 4000, '用户消息(50字) 4000ms (50*80=4000)');

// —— 自定义配置 ——
assert(bubbleDuration(10, 'role', { minMs: 500, maxMs: 8000, perCharMs: 500 }) === 5000,
  '自定义 perChar=500, 10字 = 5000ms');
assert(bubbleDuration(1, 'role', { minMs: 1000, maxMs: 8000 }) === 1000, '自定义 minMs=1000 生效');
assert(bubbleDuration(999, 'role', { minMs: 1000, maxMs: 3000 }) === 3000, '自定义 maxMs=3000 封顶');

console.log('==== 结果: ' + pass + ' passed, ' + fail + ' failed ====');
process.exit(fail ? 1 : 0);
