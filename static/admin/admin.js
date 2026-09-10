const $ = id => document.getElementById(id);
function toast(msg){ const t=$('toast'); t.textContent=msg; t.classList.add('show'); setTimeout(()=>t.classList.remove('show'),2000); }
function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

// ===== Tabs（侧边栏导航） =====
document.querySelectorAll('.snav-item').forEach(tab=>{
  tab.onclick=()=>{
    document.querySelectorAll('.snav-item').forEach(t=>t.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
    tab.classList.add('active');
    $('panel-'+tab.dataset.panel).classList.add('active');
    // 惰性加载：进入面板时刷新数据
    const p=tab.dataset.panel;
    if(p==='render') loadRenderRoles();
    if(p==='model') loadModelRows();
    if(p==='mcp') loadMcp();
    if(p==='tts'){ loadTts(); loadTtsRoles(); }
    if(p==='goals') loadGoals();
  };
});

// ===== 联系人管理 =====
async function loadRoles(){
  const res=await fetch('/admin/roles'); const json=await res.json();
  const tbody=document.querySelector('#rolesTable tbody'); tbody.innerHTML='';
  (json.data||[]).forEach(r=>{
    const tr=document.createElement('tr');
    tr.innerHTML=`
      <td>${esc(r.role_id)}</td>
      <td>${esc(r.display_name)}</td>
      <td>${r.default?'⭐ 默认':'<button class="btn btn-gray" onclick="setDefault(\''+r.role_id+'\')">设为默认</button>'}</td>
      <td>${r.avatar?'<img src="'+r.avatar_url+'" style="width:30px;height:30px;border-radius:4px" onerror="this.style.display=\'none\'">':'无'}</td>
      <td style="min-width:220px">
        <div style="font-size:12px;color:#555;word-break:break-all" id="l2dpath_${r.role_id}">${esc(r.live2d_model||'')||'<span style="color:#bbb">（未设置 · 用全局默认）</span>'}</div>
        <button class="btn btn-gray" style="margin-top:4px" onclick="pickLive2d('${r.role_id}')">浏览…</button>
        <button class="btn btn-gray" style="margin-top:4px" onclick="clearLive2d('${r.role_id}')" title="清空，回退全局默认">清除</button>
      </td>
      <td style="white-space:nowrap">
        <button class="btn btn-blue" onclick="editRolePrompt('${r.role_id}')">编辑 Prompt</button>
        <button class="btn btn-gray" onclick="document.getElementById('avatarFile_${r.role_id}').click()">传头像</button>
        <input type="file" id="avatarFile_${r.role_id}" accept="image/*" style="display:none" onchange="uploadAvatar('${r.role_id}', this)">
        <button class="btn btn-red" onclick="deleteRole('${r.role_id}')">删除</button>
      </td>`;
    tbody.appendChild(tr);
  });
}

async function createRole(){
  const body={
    role_id: $('newRoleId').value.trim(),
    display_name: $('newRoleName').value.trim(),
    description: $('newRoleDesc').value.trim(),
    prompt: $('newRolePrompt').value,
  };
  if(!body.role_id){toast('role_id 不能为空');return;}
  const res=await fetch('/admin/roles/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const json=await res.json();
  toast(json.message); if(json.code===0){ loadRoles(); $('newRoleId').value='';$('newRoleName').value='';$('newRoleDesc').value='';$('newRolePrompt').value=''; }
}

async function setDefault(roleId){
  const res=await fetch(`/admin/roles/${roleId}/default`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({default:true})});
  toast((await res.json()).message); loadRoles();
}

async function setRender(roleId, enabled){
  const res=await fetch(`/admin/roles/${roleId}/render`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({render_enabled:enabled})});
  const json=await res.json();
  toast(json.message+(`（${json.rendered?'渲染中':'已关闭'}）`)); loadRoles(); loadRenderRoles();
}

// ===== 渲染管理（单开关，默认角色恒开启） =====
async function loadRenderRoles(){
  const res=await fetch('/admin/roles'); const json=await res.json();
  const tbody=document.querySelector('#renderTable tbody'); if(!tbody) return;
  tbody.innerHTML='';
  (json.data||[]).forEach(r=>{
    const isDefault=!!r.default;
    const on=isDefault?true:!!r.rendered; // 默认角色恒渲染
    const tr=document.createElement('tr');
    tr.innerHTML=`
      <td style="font-weight:600">${esc(r.display_name)}<div style="font-weight:400;color:#999;font-size:12px">${esc(r.role_id)}</div></td>
      <td>${isDefault?'⭐ 默认':'—'}</td>
      <td>${on?'<span style="color:#07c160;font-weight:600">● 渲染中</span>':'<span style="color:#999">○ 未渲染</span>'}</td>
      <td>
        ${isDefault
          ?'<button class="btn btn-green" disabled style="opacity:.9">恒渲染（默认角色）</button>'
          :`<button class="btn ${on?'btn-red':'btn-green'}" onclick="setRender('${r.role_id}',${on?'false':'true'})">${on?'关闭渲染':'开启渲染'}</button>`}
      </td>`;
    tbody.appendChild(tr);
  });
  if(!(json.data||[]).length) tbody.innerHTML='<tr><td colspan="4" class="hint">暂无角色</td></tr>';
}

// ===== 模型管理（角色 ↔ 对应的 Live2D 模型绑定） =====
async function loadModelRows(){
  const res=await fetch('/admin/roles'); const json=await res.json();
  const tbody=document.querySelector('#modelTable tbody'); if(!tbody) return;
  tbody.innerHTML='';
  (json.data||[]).forEach(r=>{
    const tr=document.createElement('tr');
    const rendered=r.rendered?'<span style="color:#07c160">●</span>':'';
    tr.innerHTML=`
      <td style="font-weight:600">${esc(r.display_name)} ${rendered}<div style="font-weight:400;color:#999;font-size:12px">${esc(r.role_id)}${r.default?' · 默认':''}</div></td>
      <td>
        <div id="modelpath_${r.role_id}" style="font-size:12px;color:#555;word-break:break-all">${esc(r.live2d_model||'')||'<span style="color:#bbb">（未设置 · 用全局默认）</span>'}</div>
        <button class="btn btn-gray" style="margin-top:4px" onclick="pickModel('${r.role_id}')">选择模型…</button>
        <button class="btn btn-gray" style="margin-top:4px" onclick="clearModel('${r.role_id}')" title="清空，回退全局默认">清除</button>
      </td>
      <td><button class="btn btn-blue" onclick="pickModel('${r.role_id}')">选择</button></td>`;
    tbody.appendChild(tr);
  });
  if(!(json.data||[]).length) tbody.innerHTML='<tr><td colspan="3" class="hint">暂无角色</td></tr>';
}
// 模型选择复用 Live2D 浏览弹框
async function pickModel(roleId){
  l2dPickRole=roleId; l2dPickSel='';
  try{
    const res=await fetch(`/admin/live2d/models?role_id=${encodeURIComponent(roleId)}`); const json=await res.json();
    l2dCandidates=json.data||[]; l2dFilterDir=json.filter_dir||'';
  }catch(e){ l2dCandidates=[]; l2dFilterDir=''; }
  const body=$('l2dModalBody'); body.innerHTML='';
  if(l2dFilterDir){
    const tip=document.createElement('p'); tip.className='hint'; tip.style.marginBottom='10px';
    tip.textContent='当前角色目录：'+l2dFilterDir+'（仅显示该角色的模型）'; body.appendChild(tip);
  }
  if(!l2dCandidates.length){
    body.innerHTML+='<p class="hint">未发现属于该角色的 Live2D 模型。</p>';
  }else{
    l2dCandidates.forEach(m=>{
      const div=document.createElement('div'); div.className='l2d-item';
      div.dataset.path=m.path||m.id;
      div.innerHTML=`<div class="l2d-name">${esc(m.name||m.id)}</div><div class="l2d-path">${esc(m.path||m.id)}</div>`;
      div.onclick=()=>{
        $('l2dModalBody').querySelectorAll('.l2d-item').forEach(x=>x.classList.remove('selected'));
        div.classList.add('selected'); l2dPickSel=div.dataset.path;
      };
      body.appendChild(div);
    });
  }
  $('l2dModal').classList.add('show');
}
async function clearModel(roleId){ await saveRoleLive2d(roleId, ''); loadModelRows(); }

async function deleteRole(roleId){
  if(!confirm('确定删除角色 '+roleId+' ？（会删除 Prompt 文件和头像）'))return;
  const res=await fetch('/admin/roles/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({role_id:roleId})});
  toast((await res.json()).message); loadRoles();
}

async function editRolePrompt(roleId){
  // 从 prompt 文件读取当前内容，弹窗修改后保存
  const res=await fetch(`/admin/roles/${roleId}/prompt`);
  const json=await res.json();
  const newText=prompt('请输入新的 Prompt 内容', json.data?.prompt || '');
  if(newText!==null){
    savePrompt(roleId, newText);
  }
}
async function savePrompt(roleId, prompt){
  const res=await fetch('/admin/roles/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({role_id:roleId, prompt})});
  toast((await res.json()).message);
}

async function uploadAvatar(roleId, input){
  if(!input.files.length)return;
  const fd=new FormData(); fd.append('file', input.files[0]);
  const res=await fetch(`/admin/roles/${roleId}/avatar`,{method:'POST',body:fd});
  toast((await res.json()).message); loadRoles();
}

// ===== Live2D 模型路径选择 =====
let l2dPickRole=''; let l2dPickSel=''; let l2dCandidates=[]; let l2dFilterDir='';
async function pickLive2d(roleId){
  l2dPickRole=roleId; l2dPickSel='';
  try{
    const res=await fetch(`/admin/live2d/models?role_id=${encodeURIComponent(roleId)}`); const json=await res.json();
    l2dCandidates=json.data||[]; l2dFilterDir=json.filter_dir||'';
  }catch(e){ l2dCandidates=[]; l2dFilterDir=''; }
  const body=$('l2dModalBody'); body.innerHTML='';
  if(l2dFilterDir){
    const tip=document.createElement('p');
    tip.className='hint';
    tip.style.marginBottom='10px';
    tip.textContent='当前角色目录：'+l2dFilterDir+'（仅显示该角色的模型）';
    body.appendChild(tip);
  }
  if(!l2dCandidates.length){
    body.innerHTML+='<p class="hint">未发现属于该角色的 Live2D 模型。</p>';
  }else{
    l2dCandidates.forEach(m=>{
      const div=document.createElement('div'); div.className='l2d-item';
      div.dataset.path=m.path||m.id;
      div.innerHTML=`<div class="l2d-name">${esc(m.name||m.id)}</div><div class="l2d-path">${esc(m.path||m.id)}</div>`;
      div.onclick=()=>{
        $('l2dModalBody').querySelectorAll('.l2d-item').forEach(x=>x.classList.remove('selected'));
        div.classList.add('selected'); l2dPickSel=div.dataset.path;
      };
      body.appendChild(div);
    });
  }
  $('l2dModal').classList.add('show');
}
function closeL2dModal(){ $('l2dModal').classList.remove('show'); }
async function confirmL2dPick(){
  if(!l2dPickRole){ closeL2dModal(); return; }
  if(!l2dPickSel){ toast('请先选择一个模型目录'); return; }
  await saveRoleLive2d(l2dPickRole, l2dPickSel);
  closeL2dModal();
}
async function clearLive2d(roleId){ await saveRoleLive2d(roleId, ''); }
async function saveRoleLive2d(roleId, path){
  const res=await fetch(`/admin/roles/${roleId}/live2d`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({live2d_model:path})});
  toast((await res.json()).message); loadRoles(); loadModelRows();
}

// ===== 记忆查看 =====
async function loadMemory(){
  const p=new URLSearchParams({user_id:$('memUserId').value, role_id:$('memRoleId').value, level:$('memLevel').value});
  const res=await fetch('/admin/memory?'+p); const json=await res.json();
  const box=$('memoryResult'); box.innerHTML='';
  const data=json.data||{};
  const levelLabels={l1:'L1 内存上下文',l2:'L2 短期记忆',l3:'L3 信息池',l4:'L4 重要事实',l5:'L5 角色事实'};
  for(const lv of ['l1','l2','l3','l4','l5']){
    if(!data[lv] || !data[lv].length) continue;
    box.innerHTML += `<div style="margin:14px 0 6px;font-weight:600">${levelLabels[lv]}（${data[lv].length}条）</div>`;
    data[lv].forEach(item=>{
      const content = typeof item==='string' ? item : (item.content||item.document||'');
      const div=document.createElement('div'); div.className='mem-row';
      div.innerHTML=`<span class="tag">${lv}</span>${esc(content)}`;
      box.appendChild(div);
    });
  }
  if(!box.innerHTML) box.innerHTML='<p class="hint">没有查询到记忆。</p>';
}

// ===== 情感 =====
async function loadEmotion(){
  const p=new URLSearchParams({user_id:$('emoUserId').value, role_id:$('emoRoleId').value});
  const res=await fetch('/admin/memory/emotion?'+p); const json=await res.json();
  const d=json.data||{}; const box=$('emotionResult'); box.innerHTML='';
  const emo=d.emotion||{};
  const aff=d.affection||{};
  box.innerHTML=`
    <div style="margin-bottom:14px">
      <h4 style="margin-bottom:8px">情感状态</h4>
      <p>主要情绪：<b>${esc(emo.primary||'平静')}</b></p>
      <p>强度：${emo.intensity!=null?emo.intensity:'-'} · 效价：${emo.valence!=null?emo.valence:'-'}</p>
      <p class="hint">${esc(emo.description||'')}</p>
    </div>
    <div>
      <h4 style="margin-bottom:8px">6 维好感度</h4>
      ${['liking','trust','familiarity','respect','interest','attachment'].map(k=>{
        const labels={liking:'喜欢',trust:'信任',familiarity:'熟悉',respect:'尊重',interest:'兴趣',attachment:'依恋'};
        const v=aff[k]!=null?aff[k]:0.5;
        return `<div style="margin-bottom:8px">${labels[k]}：<b>${Number(v).toFixed(2)}</b>
          <div style="background:#eee;border-radius:4px;height:8px;margin-top:4px"><div style="background:#07c160;height:8px;border-radius:4px;width:${(v*100).toFixed(0)}%"></div></div></div>`;
      }).join('')}
    </div>`;
}

// ===== 关系记忆 =====
async function loadRelation(){
  const p=new URLSearchParams({user_id:$('relUserId').value, role_id:$('relRoleId').value});
  const res=await fetch('/admin/memory/relation?'+p); const json=await res.json();
  const box=$('relationResult');
  if(json.code!==0){ box.innerHTML=`<p class="err">${esc(json.message||'读取失败')}</p>`; return; }
  const d=json.data||{}; box.innerHTML='';
  const h=t=>`<div style="margin-bottom:14px">${t}</div>`;

  // 自我模型
  const sm=d.self_model||{};
  let html=h(`<h4 style="margin-bottom:8px">🧠 自我模型</h4>
    <p><b>关系认知：</b>${esc(sm.relationship||'—')}</p>
    <p><b>当前心境：</b>${esc(sm.current_mood_text||'—')}</p>
    <p><b>自我认知：</b>${esc(sm.summary||'—')}</p>
    ${(sm.cares_about&&sm.cares_about.length)?`<p><b>在意：</b>${sm.cares_about.map(esc).join('、')}</p>`:''}
    <p class="hint">更新于 ${esc(sm.updated_at||'—')}</p>`);
  box.innerHTML+=html;

  // 用户模型
  const um=d.user_model||{};
  box.innerHTML+=h(`<h4 style="margin-bottom:8px">👤 对用户的认知</h4>
    <p><b>概括：</b>${esc(um.about_user||'—')}</p>
    ${(um.traits&&um.traits.length)?`<p><b>特质：</b>${um.traits.map(esc).join('、')}</p>`:''}
    ${(um.needs&&um.needs.length)?`<p><b>需求：</b>${um.needs.map(esc).join('、')}</p>`:''}`);

  // 情绪衰减 / 好感度底层
  const dec=d.decay||{};
  const aff=dec.affection||{};
  box.innerHTML+=h(`<h4 style="margin-bottom:8px">📉 情绪衰减通道</h4>
    <p><b>当前情绪：</b>${esc(dec.emotion&&dec.emotion.primary||'—')} · 强度 ${dec.emotion_intensity_decayed!=null?dec.emotion_intensity_decayed:'—'}</p>
    <p><b>好感度均值：</b>${dec.affection_avg!=null?dec.affection_avg:'—'} · 心理年龄玩法无关</p>
    <p class="hint">6 维：喜欢 ${aff.liking!=null?aff.liking.toFixed?aff.liking.toFixed(2):aff.liking:'—'} · 信任 ${aff.trust!=null?(aff.trust.toFixed?aff.trust.toFixed(2):aff.trust):'—'} · 熟悉 ${aff.familiarity!=null?(aff.familiarity.toFixed?aff.familiarity.toFixed(2):aff.familiarity):'—'} · 尊重 ${aff.respect!=null?(aff.respect.toFixed?aff.respect.toFixed(2):aff.respect):'—'} · 兴趣 ${aff.interest!=null?(aff.interest.toFixed?aff.interest.toFixed(2):aff.interest):'—'} · 依恋 ${aff.attachment!=null?(aff.attachment.toFixed?aff.attachment.toFixed(2):aff.attachment):'—'}</p>`);

  // 共同经历
  const eps=d.episodes||[];
  box.innerHTML+=h(`<h4 style="margin-bottom:8px">📖 共同经历（${eps.length}）</h4>`);
  if(!eps.length) box.innerHTML+=`<p class="hint">暂无经历沉淀（共振阈值内的重要对话会自动记录）。</p>`;
  eps.slice(0,10).forEach(ep=>{
    box.innerHTML+=`<div style="margin-bottom:10px;padding:8px;background:#f7f7f7;border-radius:6px">
      <p class="hint">${esc((ep.ts||'').slice(0,19).replace('T',' '))} · 共振 ${ep.resonance!=null?ep.resonance.toFixed?ep.resonance.toFixed(2):ep.resonance:'—'}</p>
      <p><b>你：</b>${esc(ep.user_msg||'')}</p>
      <p style="color:#666"><b>Kasumi：</b>${esc((ep.reply||'').slice(0,120))}${(ep.reply||'').length>120?'…':''}</p>
    </div>`;
  });

  // 反思
  const refs=d.reflections||[];
  box.innerHTML+=h(`<h4 style="margin-bottom:8px">🪞 周期反思（${refs.length}）</h4>`);
  if(!refs.length) box.innerHTML+=`<p class="hint">尚未触达反思周期。</p>`;
  refs.slice(0,3).forEach(r=>{
    const rf=r.reflection||{};
    box.innerHTML+=`<div style="margin-bottom:10px;padding:8px;background:#f0f7ff;border-radius:6px">
      <p class="hint">${esc((r.ts||'').slice(0,19).replace('T',' '))}</p>
      <p>${esc(rf.self_summary||'')}</p>
      <p style="color:#666">对用户：${esc(rf.about_user||'')}</p>
    </div>`;
  });

  // 承诺
  const pro=d.promises||[];
  box.innerHTML+=h(`<h4 style="margin-bottom:8px">🤝 承诺（${pro.length}）</h4>`);
  if(!pro.length) box.innerHTML+=`<p class="hint">暂无记录。承诺在对话中自然沉淀（未兑现/已兑现都会记录）。</p>`;
  pro.forEach(p=>{
    const st=p.status||'pending';
    const stTxt={pending:'待兑现',kept:'已兑现',dropped:'已放下'}[st]||st;
    const color=st==='kept'?'#07c160':(st==='dropped'?'#999':'#e6a23c');
    box.innerHTML+=`<div style="margin-bottom:6px"><span style="display:inline-block;padding:1px 8px;border-radius:10px;background:${color}22;color:${color};font-size:12px">${stTxt}</span> ${esc(p.text||'')}</div>`;
  });

  // 情绪走向
  const mt=d.mood_trend||{};
  if(mt.samples){
    box.innerHTML+=h(`<h4 style="margin-bottom:8px">📈 情绪走向</h4>
      <p>样本 ${mt.samples} · 平均效价 ${mt.valence_avg!=null?mt.valence_avg:'—'} · 趋势 ${esc(mt.trend||'平稳')}</p>`);
  }
}

// ===== 长期目标管理（增删改查） =====
const GOAL_STATUS_TXT={active:'进行中',ongoing:'推进中',paused:'搁置',done:'已达成',abandoned:'已放弃'};
const GOAL_STATUS_COLOR={active:'#07c160',ongoing:'#409eff',paused:'#e6a23c',done:'#909399',abandoned:'#999'};
// 用 id 定位，避免标题含引号/特殊字符破坏 onclick
let _goalKey='';

async function loadGoals(){
  const p=new URLSearchParams({user_id:$('goalUserId').value, role_id:$('goalRoleId').value});
  const res=await fetch('/admin/goals?'+p); const json=await res.json();
  const box=$('goalList');
  if(json.code!==0){ box.innerHTML=`<p class="err">${esc(json.message||'读取失败')}</p>`; return; }
  const goals=json.data||[]; box.innerHTML='';
  if(!goals.length){ box.innerHTML='<p class="hint">暂无长期目标。可在对话中自然沉淀，或在上方手动添加。</p>'; return; }
  goals.forEach(g=>{
    const st=g.status||'active', id=g.id||'';
    const stTxt=GOAL_STATUS_TXT[st]||st, color=GOAL_STATUS_COLOR[st]||'#07c160';
    const vit=g.vitality!=null?(typeof g.vitality==='number'?g.vitality.toFixed(2):g.vitality):'—';
    const k=encodeURIComponent(id);
    box.innerHTML+=`<div style="margin-bottom:12px;padding:10px;border:1px solid #eee;border-radius:8px;background:#fafafa">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px">
        <span style="font-weight:600">${esc(g.title||'')}</span>
        <span>
          <span style="display:inline-block;padding:1px 8px;border-radius:10px;background:${color}22;color:${color};font-size:12px">${stTxt}</span>
          <span class="hint" style="font-size:12px;margin-left:6px">鲜活度 ${vit}</span>
        </span>
      </div>
      ${g.progress?`<p style="margin:6px 0 0;font-size:13px"><b>进度：</b>${esc(g.progress)}</p>`:''}
      ${g.note?`<p style="margin:2px 0 0;font-size:13px;color:#666"><b>备注：</b>${esc(g.note)}</p>`:''}
      <p class="hint" style="margin:4px 0 0;font-size:12px">创建 ${esc((g.created||'').slice(0,19).replace('T',' '))} · 上次提及 ${esc((g.last_engaged||'').slice(0,19).replace('T',' '))}</p>
      <div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">
        <button class="btn btn-gray" onclick="promptGoalEdit('${k}')">编辑</button>
        <button class="btn btn-green" onclick="setGoalStatus('${k}','done')">标记达成</button>
        <button class="btn btn-gray" onclick="setGoalStatus('${k}','paused')">搁置</button>
        <button class="btn btn-green" onclick="setGoalStatus('${k}','active')">恢复</button>
        <button class="btn btn-red" onclick="if(confirm('确定删除该目标？'))deleteGoal('${k}')">删除</button>
      </div>
    </div>`;
  });
}

async function addGoal(){
  const title=$('goalNewTitle').value.trim();
  if(!title){ toast('目标标题不能为空'); return; }
  const body={user_id:$('goalUserId').value, role_id:$('goalRoleId').value, title,
    progress:$('goalNewProgress').value, note:$('goalNewNote').value, status:$('goalNewStatus').value};
  const res=await fetch('/admin/goals/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await res.json();
  toast((j.code===0?'✅ ':'❌ ')+j.message);
  if(j.code===0){ $('goalNewTitle').value=''; $('goalNewProgress').value=''; $('goalNewNote').value=''; loadGoals(); }
}

function goalByKey(key){
  // 从当前列表 DOM 无法取原对象，直接走后端更新接口（用 key 标识）
  return decodeURIComponent(key);
}

function promptGoalEdit(key){
  // 从后端取当前值以便编辑（简化：重新查询该条）
  const kw=goalByKey(key).trim();
  const np=prompt('目标标题', kw)||''; if(np==null) return;
  const pp=prompt('进度（文本，可空）','')||''; if(pp==null) return;
  const nn=prompt('备注（可空）','')||''; if(nn==null) return;
  const ns=prompt('状态（active/ongoing/paused/done）','active');
  if(ns==null) return;
  updateGoal(key,{title:np,progress:pp,note:nn,status:ns});
}

async function updateGoal(key, fields){
  const body={user_id:$('goalUserId').value, role_id:$('goalRoleId').value, id:goalByKey(key), ...fields};
  const res=await fetch('/admin/goals/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await res.json();
  toast((j.code===0?'✅ ':'❌ ')+j.message);
  if(j.code===0) loadGoals();
}

async function setGoalStatus(key, status){
  await updateGoal(key,{status});
}

async function deleteGoal(key){
  const body={user_id:$('goalUserId').value, role_id:$('goalRoleId').value, id:goalByKey(key)};
  const res=await fetch('/admin/goals/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await res.json();
  toast((j.code===0?'✅ ':'❌ ')+j.message);
  if(j.code===0) loadGoals();
}

// ===== 统计 =====
async function loadStats(){
  const res=await fetch('/admin/stats'); const json=await res.json();
  const d=json.data||{}; const grid=$('statGrid'); grid.innerHTML='';
  const items=[
    ['total','总记忆数'],['l2','L2 短期记忆'],['l4','L4 重要事实'],['l5','L5 角色事实'],
    ['l3','L3 信息池'],['emotion','情感记录'],['affection','好感度记录'],['roles','角色数'],
  ];
  items.forEach(([key,label])=>{
    const div=document.createElement('div'); div.className='stat-box';
    div.innerHTML=`<div class="num">${d[key]!=null?d[key]:0}</div><div class="label">${label}</div>`;
    grid.appendChild(div);
  });
  const on=d.online||{}; grid.innerHTML+=`<div class="stat-box"><div class="num">${(on.private||0)+(on.room||0)}</div><div class="label">在线用户</div></div>`;
}

// ===== 配置 =====
async function loadConfig(){
  const res=await fetch('/admin/config'); const json=await res.json();
  const tb=document.querySelector('#configTable tbody'); tb.innerHTML='';
  (json.data||[]).forEach(c=>{
    const tr=document.createElement('tr');
    tr.innerHTML=`
      <td><code>${esc(c.key)}</code></td>
      <td>${esc(c.desc)}</td>
      <td><input data-key="${esc(c.key)}" data-type="${c.type}" value="${esc(c.value!=null?c.value:'')}" style="max-width:160px"></td>
      <td><button class="btn btn-green" onclick="saveConfig(this)">保存</button></td>`;
    tb.appendChild(tr);
  });
}
async function saveConfig(btn){
  const tr=btn.closest('tr');
  const key=tr.querySelector('input').dataset.key;
  const val=tr.querySelector('input').value;
  const res=await fetch('/admin/config/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key,value:val})});
  toast((await res.json()).message);
}

// ===== LLM 管理 =====
async function loadLLM(){
  const res=await fetch('/admin/llm/config'); const json=await res.json();
  const c=json.data||{};
  $('llmProvider').value = c.provider==='openai' ? 'openai' : 'ollama';
  $('llmOllamaHost').value = c.ollama_host||'';
  $('llmModel').value = c.llm_model||'';
  $('llmToolModel').value = c.tool_llm_model||'';
  $('llmBaseUrl').value = c.api_base_url||'';
  $('llmApiKey').value = ''; // 不回显 key
  $('llmApiKey').placeholder = c.api_key_masked||'未设置';
  $('llmRemoteModel').value = c.remote_model||'';
  $('llmRemoteToolModel').value = c.remote_tool_model||'';
  $('llmTemperature').value = c.temperature!=null ? c.temperature : 0.85;
  $('llmStatus').textContent = '';
}
function llmPayload(){
  return {
    provider: $('llmProvider').value,
    ollama_host: $('llmOllamaHost').value.trim(),
    llm_model: $('llmModel').value.trim(),
    tool_llm_model: $('llmToolModel').value.trim(),
    api_base_url: $('llmBaseUrl').value.trim(),
    api_key: $('llmApiKey').value.trim(),
    remote_model: $('llmRemoteModel').value.trim(),
    remote_tool_model: $('llmRemoteToolModel').value.trim(),
    temperature: $('llmTemperature').value,
  };
}
async function testLLM(){
  $('llmStatus').textContent = '测试中…';
  const res=await fetch('/admin/llm/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(llmPayload())});
  const json=await res.json();
  const d=json.data||{};
  if(d.ok){
    $('llmStatus').textContent = '✅ '+(d.provider==='openai'?'远程':'本地')+' · '+d.model+' · '+(d.latency_s||'?')+'s';
    $('llmStatus').style.color='#07c160';
  } else {
    $('llmStatus').textContent = '❌ '+(d.error||'连接失败');
    $('llmStatus').style.color='#ff4d4f';
  }
}
async function saveLLM(){
  const payload = llmPayload();
  // 空 key 不发，避免清掉已配置的 key
  if(!payload.api_key) delete payload.api_key;
  const res=await fetch('/admin/llm/switch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const json=await res.json();
  toast(json.message || '已保存');
  loadLLM();
}

// ===== TTS 语音合成管理 =====
function ttsSetFields(d){
  const c=d.config||{};
  $('ttsEnabledToggle').checked=!!d.enabled;
  $('ttsEnabledLabel').textContent='TTS：'+(d.enabled?'开':'关');
  $('ttsHost').value=c.TTS_HOST||'127.0.0.1';
  $('ttsPort').value=c.TTS_PORT||'9880';
  $('ttsTextLang').value=c.TTS_TEXT_LANG||'zh';
  $('ttsPromptLang').value=c.TTS_PROMPT_LANG||'zh';
  $('ttsMediaType').value=c.TTS_MEDIA_TYPE||'wav';
  $('ttsSpeed').value=c.TTS_SPEED_FACTOR!=null?c.TTS_SPEED_FACTOR:1.0;
  $('ttsRefAudio').value=c.TTS_REF_AUDIO_PATH||'';
  $('ttsPromptText').value=c.TTS_PROMPT_TEXT||'';
  $('ttsStatus').textContent='';
}
async function loadTts(){
  try{
    const res=await fetch('/admin/tts/status'); const json=await res.json();
    if((json.code||0)!==0){ $('ttsStatus').textContent='❌ '+(json.message||'读取失败'); $('ttsStatus').style.color='#ff4d4f'; return; }
    ttsSetFields(json.data||{});
  }catch(e){ $('ttsStatus').textContent='❌ 读取失败: '+e; $('ttsStatus').style.color='#ff4d4f'; }
}
function ttsEnabledValue(){ return $('ttsEnabledToggle').checked; }
async function ttsToggle(enabled){
  const res=await fetch('/admin/tts/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({TTS_ENABLED:enabled})});
  const j=await res.json();
  toast((j.code===0?'✅ ':'❌ ')+(j.message||''));
  loadTts();
}
function ttsPayload(){
  return {
    TTS_ENABLED: ttsEnabledValue(),
    TTS_HOST: $('ttsHost').value.trim(),
    TTS_PORT: $('ttsPort').value.trim(),
    TTS_TEXT_LANG: $('ttsTextLang').value.trim(),
    TTS_PROMPT_LANG: $('ttsPromptLang').value.trim(),
    TTS_MEDIA_TYPE: $('ttsMediaType').value,
    TTS_SPEED_FACTOR: $('ttsSpeed').value,
    TTS_REF_AUDIO_PATH: $('ttsRefAudio').value.trim(),
    TTS_PROMPT_TEXT: $('ttsPromptText').value.trim(),
  };
}
async function saveTts(){
  const res=await fetch('/admin/tts/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(ttsPayload())});
  const j=await res.json();
  toast((j.code===0?'✅ ':'❌ ')+(j.message||''));
  loadTts();
}
async function ttsTest(){
  const st=$('ttsStatus'); st.textContent='测试中…'; st.style.color='#666';
  await saveTts();
  const res=await fetch('/admin/tts/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:'你好呀，我是你的语音助手。'})});
  const j=await res.json();
  st.textContent=(j.code===0?'✅ ':'❌ ')+(j.message||'');
  st.style.color=j.code===0?'#07c160':'#ff4d4f';
}

// ===== 每角色 TTS 独立服务管理 =====
let ttsCustomEdits = {};   // role_id -> 前端已编辑但未保存的中间值
let ttsSelected = null;    // 当前展开编辑的角色
async function loadTtsRoles(){
  const st=$('ttsRolesStatus'); st.textContent='加载中…'; st.style.color='#666';
  // 角色主列表（用于列出可配置的角色）
  let roles=[]
  try{ const r=await (await fetch('/admin/roles')).json(); roles=(r.data||[]); }catch(e){}
  // 已配置 TTS 的角色状态
  let cfgList=[]
  try{ const r=await (await fetch('/admin/tts/roles')).json(); cfgList=(r.data||[]); }catch(e){}
  const box=$('ttsRolesList'); box.innerHTML='';
  const cfgMap={}; cfgList.forEach(c=>cfgMap[c.role_id]=c);
  const allIds={}; roles.forEach(r=>allIds[r.role_id]=r.display_name);
  cfgList.forEach(c=>{ if(!allIds[c.role_id]) allIds[c.role_id]=c.role_id; });

  Object.keys(allIds).forEach(rid=>{
    const info=cfgMap[rid]||{};
    const card=document.createElement('div');
    card.className='card'; card.style.padding='12px'; card.style.margin='0';
    card.id='ttsRoleCard_'+rid;
    card.innerHTML=`
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <strong style="min-width:90px">${esc(allIds[rid])} <span style="color:#999;font-weight:400">(${esc(rid)})</span></strong>
        <span id="ttsRoleState_${rid}" style="font-size:13px">${info.running?'<span style="color:#07c160">● 运行中</span>':'<span style="color:#999">○ 未运行</span>'}</span>
        <span style="font-size:13px;color:#888">端口 ${info.port!==undefined?info.port:'—'} · 语言 ${info.lang||'—'}</span>
        <span style="flex:1"></span>
        <button class="btn ${info.running?'btn-red':'btn-green'}" onclick="ttsRoleStartStop('${rid}', ${!info.running})">${info.running?'⏹ 停止':'▶ 启动'}</button>
        <button class="btn btn-blue" onclick="toggleTtsRoleEdit('${rid}')">⚙️ 配置</button>
        ${info.configured?`<button class="btn btn-gray" onclick="deleteTtsRole('${rid}')">🗑 删除</button>`:''}
      </div>
      <div id="ttsRoleEdit_${rid}" style="display:none;margin-top:10px;border-top:1px solid #eee;padding-top:10px"></div>`;
    box.appendChild(card);
    // 若之前选中展开，自动加载编辑区
    if(ttsSelected===rid) toggleTtsRoleEdit(rid, true);
  });
  st.textContent=Object.keys(allIds).length?`共 ${Object.keys(allIds).length} 个角色`:'';
  st.style.color='#999';
}
function toggleTtsRoleEdit(rid, forceOpen){
  const willOpen=forceOpen!==undefined?forceOpen:(ttsSelected!==rid);
  ttsSelected=willOpen?rid:null;
  const box=$('ttsRoleEdit_'+rid); if(!box) return;
  if(!willOpen){ box.style.display='none'; return; }
  loadTtsRoleEdit(rid, box);
}
async function loadTtsRoleEdit(rid, box){
  box.innerHTML='<span class="hint">加载中…</span>'; box.style.display='block';
  let cfg={};
  try{ const r=await (await fetch('/admin/tts/roles/'+encodeURIComponent(rid))).json(); cfg=r.data||{}; }catch(e){cfg={};}
  ttsCustomEdits[rid]=cfg;
  renderTtsRoleEdit(rid, box);
}
function renderTtsRoleEdit(rid, box){
  const c=ttsCustomEdits[rid]||{};
  const w=c.weights||{};
  box.innerHTML=`
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px 18px">
      <div><label>tts 模型执行文件（python 解释器）</label>
        <div style="display:flex;gap:6px"><input id="tr_exec_${rid}" value="${esc(c.exec_file||'')}" placeholder="…\\runtime\\python.exe"><button class="btn btn-gray" onclick="ttsBrowse('${rid}','exec_file')">浏览…</button></div></div>
      <div><label>api_v2.py 位置</label>
        <div style="display:flex;gap:6px"><input id="tr_api_${rid}" value="${esc(c.api_file||'')}" placeholder="…\\api_v2.py"><button class="btn btn-gray" onclick="ttsBrowse('${rid}','api_file')">浏览…</button></div></div>
      <div><label>GPT-SoVITS 根目录</label>
        <div style="display:flex;gap:6px"><input id="tr_root_${rid}" value="${esc(c.sovits_root||'')}" placeholder="D:\\Program\\GPT-SoVITS…"><button class="btn btn-gray" onclick="ttsBrowse('${rid}','sovits_root')">浏览…</button></div></div>
      <div><label>服务端口</label><input id="tr_port_${rid}" type="number" value="${c.port!=null?c.port:''}" placeholder="9880"></div>
      <div><label>启动语言</label><select id="tr_lang_${rid}"><option value="zh" ${c.lang==='zh'?'selected':''}>中文 (zh)</option><option value="ja" ${c.lang==='ja'?'selected':''}>日文 (ja)</option><option value="en" ${c.lang==='en'?'selected':''}>英文 (en)</option></select></div>
      <div><label>启用该角色 TTS</label><label style="display:flex;align-items:center;gap:6px;margin-top:4px"><input type="checkbox" id="tr_enabled_${rid}" ${c.enabled?'checked':''} style="width:auto"> 启用</label></div>
      <div><label>权重文件 vits (.pth)</label>
        <div style="display:flex;gap:6px"><input id="tr_pth_${rid}" value="${esc(w.pth||'')}" placeholder="…\\xxx.pth"><button class="btn btn-gray" onclick="ttsBrowse('${rid}','weights.pth')">浏览…</button></div></div>
      <div><label>权重文件 t2s (.ckpt)</label>
        <div style="display:flex;gap:6px"><input id="tr_ckpt_${rid}" value="${esc(w.ckpt||'')}" placeholder="…\\xxx.ckpt"><button class="btn btn-gray" onclick="ttsBrowse('${rid}','weights.ckpt')">浏览…</button></div></div>
      <div style="grid-column:1/-1"><label>参照音频（该角色音色参考）</label>
        <div style="display:flex;gap:6px"><input id="tr_ref_${rid}" value="${esc(c.ref_audio_path||'')}" placeholder="…\\角色目录\\xxx.mp3"><button class="btn btn-gray" onclick="ttsBrowse('${rid}','ref_audio_path')">浏览…</button></div></div>
      <div style="grid-column:1/-1"><label>参照音频转写文本 (prompt_text)</label><input id="tr_prompt_${rid}" value="${esc(c.prompt_text||'')}" placeholder="参照音频里说的那句话"></div>
    </div>
    <div style="display:flex;gap:10px;margin-top:12px">
      <button class="btn btn-green" onclick="saveTtsRole('${rid}')">💾 保存角色配置</button>
      <button class="btn btn-blue" onclick="testTtsRole('${rid}')">🧪 测试合成</button>
      <span class="hint" id="tr_msg_${rid}" style="align-self:center"></span>
    </div>`;
  // 收回编辑区状态
  setTimeout(()=>{ ttsSelected=rid; },0);
}
function ttsRoleField(rid, field){
  const id='tr_'+field.replace(/[.]/g,'_')+'_'+rid;
  return $(id) ? $(id).value : '';
}
function collectTtsRole(rid){
  const w={pth:ttsRoleField(rid,'pth'), ckpt:ttsRoleField(rid,'ckpt')};
  const c=ttsCustomEdits[rid]||{};
  return {
    exec_file:ttsRoleField(rid,'exec_file'),
    api_file:ttsRoleField(rid,'api_file'),
    sovits_root:ttsRoleField(rid,'root'),
    port: ttsRoleField(rid,'port')?Number(ttsRoleField(rid,'port')):c.port,
    lang: ttsRoleField(rid,'lang')||'zh',
    enabled: !!($('tr_enabled_'+rid)&&$('tr_enabled_'+rid).checked),
    ref_audio_path: ttsRoleField(rid,'ref'),
    prompt_text: ttsRoleField(rid,'prompt'),
    weights: (w.pth||w.ckpt)?w:undefined,
  };
}
async function saveTtsRole(rid){
  const msg=$('tr_msg_'+rid); if(!msg) return;
  msg.textContent='保存中…'; msg.style.color='#666';
  const payload=collectTtsRole(rid);
  const res=await fetch('/admin/tts/roles/'+encodeURIComponent(rid)+'/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const j=await res.json();
  msg.textContent=(j.code===0?'✅ ':'❌ ')+(j.message||''); msg.style.color=j.code===0?'#07c160':'#ff4d4f';
  loadTtsRoles();
}
async function ttsRoleStartStop(rid, start){
  const res=await fetch('/admin/tts/roles/'+encodeURIComponent(rid)+'/'+(start?'start':'stop'),{method:'POST'});
  const j=await res.json();
  toast((j.code===0?'✅ ':'❌ ')+(j.message||''));
  loadTtsRoles();
}
async function deleteTtsRole(rid){
  if(!confirm('确定删除角色「'+rid+'」的 TTS 配置？')) return;
  const res=await fetch('/admin/tts/roles/'+encodeURIComponent(rid)+'/delete',{method:'POST'});
  const j=await res.json();
  toast((j.code===0?'✅ ':'❌ ')+(j.message||'')); loadTtsRoles();
}
async function testTtsRole(rid){
  const msg=$('tr_msg_'+rid); if(!msg) return;
  await saveTtsRole(rid);
  const res=await fetch('/admin/tts/roles/'+encodeURIComponent(rid)+'/start',{method:'POST'});
  const j=await res.json();
  msg.textContent=(j.code===0?'✅ 已尝试启动':'❌ '+(j.message||'')); msg.style.color=j.code===0?'#07c160':'#ff4d4f';
  toast((j.code===0?'✅ 角色 TTS 服务已启动（若未就绪请稍候刷新）':'❌ '+(j.message||'')));
  setTimeout(loadTtsRoles, 1500);
}

// ===== 通用文件浏览器 Modal（TTS 选文件） =====
let fbTarget=''; let fbField=''; let fbPath=''; let fbFilterFile=false;
async function ttsBrowse(rid, field){
  fbTarget=rid; fbField=field; fbFilterFile=field.indexOf('sovits_root')>=0?false:true;
  await fileBrowseOpen('');
}
async function fileBrowseOpen(path){
  const body=$('fileBrowseBody'); body.innerHTML='<span class="hint">加载中…</span>';
  $('fileBrowseTitle').textContent='选择文件';
  $('fileBrowseModal').classList.add('show');
  const url='/admin/tts/browse'+(path?'?path='+encodeURIComponent(path):'');
  try{
    const r=await (await fetch(url)).json();
    if((r.code||0)!==0){ body.innerHTML='<p class="hint">'+esc(r.message||'读取失败')+'</p>'; return; }
    const d=r.data||{};
    body.innerHTML='';
    if(d.cwd && d.drives){
      // 盘符视图（含默认 GPT-SoVITS 根快捷入口）
      body.innerHTML+='<button class="btn btn-gray" style="margin:2px" onclick="fileBrowseQuick(\''+escAttr(d.cwd)+'\')">📁 GPT-SoVITS 根：'+esc(d.cwd)+'</button><br><br>';
      d.drives.forEach(drv=>{
        const b=document.createElement('button'); b.className='btn btn-gray'; b.style.margin='2px';
        b.textContent='💾 '+drv; b.onclick=()=>fileBrowseOpen(drv);
        body.appendChild(b);
      });
      return;
    }
    // 目录内视图
    body.innerHTML+=`<div style="margin-bottom:8px;word-break:break-all">📍 ${esc(d.path||'')}</div>`;
    if(d.parent){ body.innerHTML+=`<div><button class="btn btn-gray" style="margin:2px" onclick="fileBrowseOpen('${escAttr(d.parent)}')">⬆ 上一级</button></div>`; }
    (d.dirs||[]).forEach(dir=>{
      const b=document.createElement('button'); b.className='btn btn-gray'; b.style.margin='2px'; b.style.display='block';
      b.textContent='📁 '+dir; b.style.textAlign='left'; b.style.width='100%';
      b.onclick=()=>{ fbPath=d.path+sepStr(d.sep)+dir; fileBrowseOpen(fbPath); };
      body.appendChild(b);
    });
    if(fbFilterFile){
      body.innerHTML+='<div style="margin:8px 0;border-top:1px solid #eee;padding-top:6px;font-weight:600">文件</div>';
      (d.files||[]).forEach(file=>{
        const row=document.createElement('div');
        row.style.cssText='display:flex;justify-content:space-between;align-items:center;padding:3px 4px;cursor:pointer;border-radius:4px';
        row.onmouseover=()=>{row.style.background='#f5f5f5';}; row.onmouseout=()=>{row.style.background='';};
        row.onclick=()=>{ fbPath=d.path+sepStr(d.sep)+file; pickFbClass(); };
        row.innerHTML=`<span style="word-break:break-all">📄 ${esc(file)}</span><button class="btn btn-blue" style="padding:2px 10px">选用</button>`;
        body.appendChild(row);
      });
    }
  }catch(e){ body.innerHTML='<p class="hint">载入失败: '+esc(String(e))+'</p>'; }
}
function sepStr(sep){ return sep==='/'?'/':'\\'; }
function escAttr(s){ return String(s||'').replace(/\\/g,'\\\\').replace(/'/g,"\\'"); }
function pickFbClass(){ fileBrowsePick(fbPath); }
function fileBrowseQuick(p){ fbPath=''; fileBrowseOpen(p); }
function closeFileBrowseModal(){ $('fileBrowseModal').classList.remove('show'); }
function confirmFileBrowsePick(){
  if(!fbTarget){ closeFileBrowseModal(); return; }
  if(!fbPath){ toast('请先选择一个文件'); return; }
  fileBrowsePick(fbPath);
}
function fileBrowsePick(fullPath){
  const rid=fbTarget, field=fbField;
  // 更新 ttsCustomEdits 中间值
  const c=ttsCustomEdits[rid]=ttsCustomEdits[rid]||{};
  if(field==='weights.pth'){ c.weights=c.weights||{}; c.weights.pth=fullPath; }
  else if(field==='weights.ckpt'){ c.weights=c.weights||{}; c.weights.ckpt=fullPath; }
  else c[field]=fullPath;
  const editBox=$('ttsRoleEdit_'+rid); if(editBox) renderTtsRoleEdit(rid, editBox);
  closeFileBrowseModal();
  toast('已选择路径');
}

// ===== MCP 管理 =====
async function loadMcp(){
  const tbody=document.querySelector('#mcpTable tbody'); tbody.innerHTML='<tr><td colspan="7" class="hint">加载中…</td></tr>';
  try{
    const res=await fetch('/admin/mcp/status'); const json=await res.json();
    const items=(json.data||[])||(json.data&&json.data.items)||[];
    $('mcpDetail').innerHTML='';
    if((json.code||0)!==0){ tbody.innerHTML=`<tr><td colspan="7" class="hint">${esc(json.message||'读取失败')}</td></tr>`; return; }
    if(!items.length){ tbody.innerHTML='<tr><td colspan="7" class="hint">未配置任何 MCP 服务（config/mcp.json 的 servers 为空）。</td></tr>'; return; }
    tbody.innerHTML='';
    items.forEach(s=>{
      const running=!!s.running;
      const enabled=!!s.enabled;
      const tools=s.tools_count!=null?s.tools_count:0;
      const err=s.last_error||'';
      const tr=document.createElement('tr');
      const name=esc(s.name||'?');
      tr.innerHTML=`
        <td style="font-weight:600">${name}${s.registered?' <span title="工具已注册到对话" style="color:#07c160">✓</span>':''}</td>
        <td style="max-width:240px">${esc(s.command||'')}</td>
        <td>
          <label style="cursor:pointer">
            <input type="checkbox" ${enabled?'checked':''} onchange="mcpSetEnabled('${name}', this.checked)" style="width:auto">
            <span style="font-size:12px;color:${enabled?'#07c160':'#999'}">${enabled?'启用':'停用'}</span>
          </label>
        </td>
        <td>${running?'<span style="color:#07c160;font-weight:600">● 运行中</span>':'<span style="color:#999">○ 已停止</span>'}</td>
        <td>${tools}</td>
        <td style="max-width:260px">${err?`<span style="color:#e6a23c;font-size:12px">${esc(err).slice(0,80)}</span>`:'<span class="hint">正常</span>'}</td>
        <td style="white-space:nowrap">
          ${running
            ? `<button class="btn btn-red" onclick="mcpStop('${name}')">停止</button>`
            : `<button class="btn btn-green" onclick="mcpStart('${name}')">启动</button>`}
          <button class="btn btn-gray" onclick="mcpRestart('${name}')" title="重启">重启</button>
          <button class="btn btn-blue" onclick="mcpTest('${name}')" title="测试链路">测试</button>
        </td>`;
      tbody.appendChild(tr);
    });
    $('mcpDetail').textContent='共 '+items.length+' 个 MCP 服务。点击「🔄 刷新」查看最新状态。';
  }catch(e){
    tbody.innerHTML=`<tr><td colspan="7" class="hint">读取失败: ${esc(e)}（后端可能未包含最新 MCP 管理接口，请重启后端）</td></tr>`;
  }
}
async function mcpStart(name){
  const res=await fetch(`/admin/mcp/${encodeURIComponent(name)}/start`,{method:'POST'});
  const j=await res.json();
  toast((j.code===0?'✅ ':'❌ ')+(j.message||''));
  loadMcp();
}
async function mcpStop(name){
  const res=await fetch(`/admin/mcp/${encodeURIComponent(name)}/stop`,{method:'POST'});
  const j=await res.json();
  toast((j.code===0?'✅ ':'❌ ')+(j.message||''));
  loadMcp();
}
async function mcpRestart(name){
  const res=await fetch(`/admin/mcp/${encodeURIComponent(name)}/restart`,{method:'POST'});
  const j=await res.json();
  toast((j.code===0?'✅ ':'❌ ')+(j.message||''));
  loadMcp();
}
async function mcpTest(name){
  const res=await fetch(`/admin/mcp/${encodeURIComponent(name)}/test`,{method:'POST'});
  const j=await res.json();
  const d=j.data||{};
  let msg=j.message||'';
  if(j.code===0&&d.tools_count!=null) msg=`测试成功 · 工具 ${d.tools_count} 个`+(d.tool_names&&d.tool_names.length?('：'+d.tool_names.slice(0,8).join('、')+(d.tool_names.length>8?'…':'')):'');
  toast((j.code===0?'✅ ':'❌ ')+msg);
}
async function mcpSetEnabled(name, enabled){
  const res=await fetch(`/admin/mcp/${encodeURIComponent(name)}/enabled`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled})});
  const j=await res.json();
  toast((j.code===0?'✅ 已保存开关':'❌ '+(j.message||'保存失败'))+(enabled?'（启用，可在上方点击「启动」）':''));
  loadMcp();
}

// ===== 初始化 =====
loadRoles(); loadRenderRoles(); loadStats(); loadConfig(); loadLLM();
setInterval(loadStats, 5000);
// 轮询：渲染角色变化（多主机对齐）时自动刷新渲染面板
setInterval(function(){ const p=document.querySelector('.snav-item.active'); if(p&&p.dataset.panel==='render') loadRenderRoles(); }, 3000);
