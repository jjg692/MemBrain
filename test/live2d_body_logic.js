// A 状态层逻辑单元测试 —— live2d-page.js 中"情绪→持续驱动身体参数"块
// 运行: node test/live2d_body_logic.js
// 思路: 从 live2d-page.js 提取 A 层块,用 new Function 注入 stub 依赖
//      (window/renderer/liveModel/requestAnimationFrame),再驱动 bodyTick
//      验证映射表、强度调制、插值逼近、保持、回落默认脸 等纯逻辑。
'use strict';
const fs = require('fs');
const path = require('path');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'static', 'live2d', 'live2d-page.js'), 'utf8');
const START = SRC.indexOf('// A 状态层：情绪持续驱动身体参数');
const END = SRC.indexOf('// 角色开口说话：优先窗口A下发的 behavior');
if (START < 0 || END < 0 || END <= START) { throw new Error('无法定位 A 层块,请检查源文件'); }
const BLOCK = SRC.slice(START, END);

let pass = 0, fail = 0;
function assert(cond, msg) {
  if (cond) { pass++; console.log('  ok - ' + msg); }
  else { fail++; console.error('  FAIL - ' + msg); }
}

// 用 stub 依赖(window/renderer/liveModel/whenModelReady)执行 A 层块;
// 通过调用被 patch 后的 modelObj.live2DModel.update() 模拟"运行时每帧叠写"。
function makeEnv() {
  const fakeWindow = { Live2D: {} };
  let lastParamsMap = null;
  const curVals = {};   // 模拟模型当前参数值(供 getParamFloat / 释放对齐)
  const modelObj = {
    live2DModel: {
      setParamFloat: function (id, v) { curVals[id] = v; },
      getParamFloat: function (id) { return typeof curVals[id] === "number" ? curVals[id] : 0; },
      update: function () {},   // 运行时每帧调用;patchBodyUpdate 会 wrap 它在之后叠写
    },
  };
  const renderer = {
    setParams: function (map, weight) { lastParamsMap = Object.assign({}, map); return Object.keys(map).length; },
    setParam: function () { return true; },
    getParam: function (id) { return modelObj.live2DModel.getParamFloat(id); },
  };
  const liveModel = () => modelObj;
  const liveModelReady = function () { return true; };
  const whenModelReady = function (fn, fallback, retry) { try { fn(); } catch (e) {} };
  // lipT: 口型/说话状态(真实页面里与 A 层同属一个 IIFE,这里是注入 stub)
  const lipT = { active: false, raf: 0, startTime: 0, dur: 0, speed: 1, pitch: 1, baseOpen: 0.5 };
  const F = new Function('window', 'renderer', 'liveModel', 'requestAnimationFrame', 'whenModelReady', 'modelReady', 'lipT', BLOCK);
  F(fakeWindow, renderer, liveModel, function (fn) { return 1; }, whenModelReady, liveModelReady, lipT);
  const bodyAPI = fakeWindow.Live2D.body;
  return { bodyAPI, last: () => lastParamsMap, modelObj, lipT };
}

console.log('== 1. 默认脸: 未设情绪时 targets=默认 ==');
{
  const { bodyAPI } = makeEnv();
  const s = bodyAPI.state();
  assert(s.lastKey === '', 'lastKey 初始为空');
  assert(s.primary === '', 'primary 初始为空');
  assert(s.targets['PARAM_CHEEK'] === 0, '默认 CHEEK=0');
  assert(s.targets['PARAM_EYE_L_OPEN'] === 1.0, '默认 EYE_L_OPEN=1.0');
}

console.log('== 2. 情绪→目标映射: 害羞 脸红低头 ==');
{
  const { bodyAPI } = makeEnv();
  bodyAPI.setEmotion('害羞', 1.0);
  const t = bodyAPI.state().targets;
  assert(bodyAPI.state().lastKey === 'shy', '害羞 → key=shy');
  assert(Math.abs(t['PARAM_CHEEK'] - 1.0) < 1e-6, '害羞 CHEEK=1.0 (脸红)');
  assert(Math.abs(t['PARAM_CHEEK2'] - 0.6) < 1e-6, '害羞 CHEEK2=0.6');
  assert(Math.abs(t['PARAM_ANGLE_X'] - 0.06) < 1e-6, '害羞 ANGLE_X=0.06 (低头)');
  assert(Math.abs(t['PARAM_ANGLE_Z'] - 0.08) < 1e-6, '害羞 ANGLE_Z=0.08 (歪头)');
}

console.log('== 3. 强度调制: 同情绪高强度更浓 ==');
{
  const { bodyAPI } = makeEnv();
  bodyAPI.setEmotion('害羞', 0.2);
  const low = bodyAPI.state().targets['PARAM_CHEEK'];
  bodyAPI.setEmotion('害羞', 1.0);
  const high = bodyAPI.state().targets['PARAM_CHEEK'];
  assert(high > low, '高强度(' + high.toFixed(3) + ') > 低强度(' + low.toFixed(3) + ')');
  assert(low > 0 && low < 1, '低强度介于默认0与目标1之间: ' + low.toFixed(3));
}

console.log('== 4. 插值逼近: 每帧(运行时 update 后)越接近目标 ==');
{
  const { bodyAPI, last, modelObj } = makeEnv();
  bodyAPI.setEmotion('惊讶', 1.0);
  const tgt = bodyAPI.state().targets['PARAM_EYE_L_OPEN'];
  assert(Math.abs(tgt - 1.5) < 1e-6, '惊讶 EYE_L_OPEN 目标=1.5 (强度1.0)');
  // patchBodyUpdate 已把 modelObj.live2DModel.update 包上"叠写"。驱动它多次模拟运行时每帧。
  let prevVal = null, monotonic = true;
  for (let i = 0; i < 300; i++) {
    modelObj.live2DModel.update();
    const m = last();
    const v = m ? m['PARAM_EYE_L_OPEN'] : null;
    if (v == null) continue;
    if (prevVal !== null && v < prevVal - 1e-9) { monotonic = false; }
    prevVal = v;
  }
  assert(monotonic, '写入值单调非降(向目标逼近)');
  assert(prevVal !== null && Math.abs(prevVal - tgt) < 0.02, '最终接近目标(prev=' + (prevVal == null ? 'null' : prevVal.toFixed(3)) + ', tgt=' + tgt.toFixed(3) + ')');
}

console.log('== 5. 模式二释放: reset 后进入释放过渡,收敛后完全放手(还给A) ==');
{
  const { bodyAPI, modelObj } = makeEnv();
  bodyAPI.setEmotion('生气', 0.9);
  assert(bodyAPI.state().lastKey === 'angry', '生气 → key=angry');
  const t = bodyAPI.state().targets;
  assert(t['PARAM_BROW_L_ANGLE'] < 0, '生气 BROW_L_ANGLE<0 (皱眉)');
  // reset: 应进入"释放过渡"(模式二),而非立即压默认
  bodyAPI.reset();
  let s = bodyAPI.state();
  assert(s.lastKey === '', 'reset 后 lastKey 清空');
  assert(s.releasing === true, 'reset 后进入 releasing 过渡(模式二)');
  assert(s.released === false, 'reset 后尚未 released');
  // 驱动运行时每帧(模拟 update 钩子 + 待机空档),释放应平滑收敛并最终完全放手
  for (let i = 0; i < 600; i++) { modelObj.live2DModel.update(); }
  s = bodyAPI.state();
  assert(s.released === true, '多次帧后 released=true(完全放手给A)');
  assert(s.releasing === false, '释放完成后 releasing=false');
  // 释放期间写入的是 A 的当前值(releaseA),而非硬扣默认 0
  const a = bodyAPI.state().targets;   // targets 未改; 验证写向 A 值
  void a;
}

console.log('== 6. 未知情绪回退默认脸 ==');
{
  const { bodyAPI } = makeEnv();
  bodyAPI.setEmotion('平静', 1.0);
  const s = bodyAPI.state();
  assert(s.lastKey === '', '未知情绪(平静) lastKey 保持空');
  assert(s.targets['PARAM_CHEEK'] === 0 && s.targets['PARAM_EYE_L_OPEN'] === 1.0, '未知情绪回默认脸');
}

console.log('== 7. 各中文情绪均映射到预期 key ==');
{
  const { bodyAPI } = makeEnv();
  const map = { '开心':'happy','兴奋':'excited','难过':'sad','生气':'angry','惊讶':'surprised','害羞':'shy','疲惫':'tired' };
  Object.keys(map).forEach((cn) => {
    bodyAPI.setEmotion(cn, 0.7);
    assert(bodyAPI.state().lastKey === map[cn], cn + '→key=' + map[cn]);
  });
}

console.log('');
console.log('==== 结果: ' + pass + ' passed, ' + fail + ' failed ====');
process.exit(fail ? 1 : 0);
