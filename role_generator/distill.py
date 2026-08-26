"""
行为蒸馏器: 把多来源角色资料蒸馏为 CSP 风格角色行为规则
"""
import json
import re
from typing import Dict, Optional

from core.adapters import OllamaAdapter

# 蒸馏 prompt: 指导模型把设定变行为
DISTILL_SYSTEM = (
    "你是角色行为蒸馏引擎。把'角色是什么样的'提炼成'角色会怎么行动、怎么说、怎么想'。\n"
    "严格按如下 JSON 输出, 不要输出其他: \n"
    "{\"display_name\":\"中文名\",\"origin\":\"原文名\",\"tagline\":\"代表性一句话\",\n"
    " \"roleplay_rules\":[\"硬规则...\"], \"identity\":{\"who\":\"我是谁(第一人称)\",\"world\":\"世界观\",\"first_impression\":\"第一印象\"},\n"
    " \"behavior\":{\"default\":\"默认行为(情境化)\",\"under_pressure\":\"压力行为\",\"core_conflict\":\"核心矛盾\",\"facing_others\":\"对不同的人\"},\n"
    " \"expression\":{\"sentence_style\":\"句式节奏\",\"verbal_ticks\":\"口癖/自称/语尾\",\"emotion_tells\":\"情绪泄露\",\"classic_lines\":[\"经典台词1\",\"经典台词2\"]},\n"
    " \"social_cognition\":{\"default_reading\":\"默认解读\",\"notices\":\"注意到\",\"ignores\":\"忽略\",\"relationship_patterns\":\"关系模板\"},\n"
    " \"decision_logic\":{\"core_motivation\":\"核心动机\",\"value_priority\":[\"价值优先级\"],\"hard_limits\":[\"硬约束\"]},\n"
    " \"knowledge_boundary\":{\"knows\":\"所知\",\"not_knows\":\"所不知(勿用上帝视角)\",\"when_unknown\":\"面对不知\"},\n"
    " \"sample_dialogues\":[{\"scene\":\"情境\",\"inner\":\"内心\",\"action\":\"言行\"}]}\n"
    "规则: 用'情境->行为'描述而非形容词堆砌; 保留原角色口癖/自称/语尾(保留日文+中文说明); 资料不足写'推测'别编造; 保留矛盾。"
)

def distill_character(adapter, character, source_text, work=''):
    """调用 LLM 蒸馏, 返回行为 JSON dict"""
    prompt = DISTILL_SYSTEM + '\n\n角色名: ' + character + ('\n作品: ' + work if work else '') + '\n资料:\n' + source_text
    try:
        text = adapter.chat([{'role': 'system', 'content': DISTILL_SYSTEM}, {'role': 'user', 'content': prompt}])
        text = re.sub(r'^```(?:json)?\\s*|\\s*```$', '', (text or '').strip())
        data = json.loads(_extract_json(text))
        return data if isinstance(data, dict) else None
    except Exception as e:
        print('[distill] 失败:', e)
        return None

def _extract_json(text):
    try:
        json.loads(text)
        return text
    except Exception:
        pass
    m = re.search(r'\{.*\}', text, re.DOTALL)
    return m.group(0) if m else '{}'

def render_skill_prompt(data, character, work='', retrieved_at='', sources_desc=''):
    """把蒸馏 JSON 渲染为可加载的 role prompt( CSP 风格 )"""
    def g(*keys, default=''):
        cur = data
        for k in keys:
            if not isinstance(cur, dict):
                return default
            cur = cur.get(k)
        return cur if cur is not None else default
    ident = g('identity', default={}) or {}
    beh = g('behavior', default={}) or {}
    expr = g('expression', default={}) or {}
    soc = g('social_cognition', default={}) or {}
    dec = g('decision_logic', default={}) or {}
    kn = g('knowledge_boundary', default={}) or {}
    rules = g('roleplay_rules', default=[]) or []
    classic = expr.get('classic_lines') or []
    vp = dec.get('value_priority') or []
    hl = dec.get('hard_limits') or []
    samples = (data.get('sample_dialogues') or []) if isinstance(data, dict) else []
    def bul(items):
        if not isinstance(items, list):
            items = [items] if items else []
        return '\n'.join('- ' + str(x) for x in items if x)
    L = ['你是' + str(data.get('display_name') or character) + ('（' + str(data.get('origin') or '') + '）').strip() or '' ]
    L[0] = '你是' + str(data.get('display_name') or character) + (('（' + str(data.get('origin') or '') + '）') if data.get('origin') else '')
    if data.get('tagline'):
        L.append('核心台词: 「' + str(data['tagline']) + '」')
    L.append('')
    L.append('# 角色扮演规则（最重要）')
    L.append('**你完全就是该角色本人, 不是扮演者。**')
    for r in (rules if isinstance(rules, list) else [rules]):
        if r: L.append('- ' + str(r))
    L.append('')
    L.append('# 身份卡')
    L.append('- **我是谁**: ' + str(ident.get('who') or '资料不足'))
    L.append('- **我的世界**: ' + str(ident.get('world') or ''))
    L.append('- **别人第一眼看到我**: ' + str(ident.get('first_impression') or ''))
    L.append('')
    L.append('# 行为动态')
    if beh.get('default'): L.append('- **默认状态**: ' + str(beh['default']))
    if beh.get('under_pressure'): L.append('- **压力之下**: ' + str(beh['under_pressure']))
    if beh.get('core_conflict'): L.append('- **核心矛盾**: ' + str(beh['core_conflict']))
    if beh.get('facing_others'):
        fo = beh['facing_others']
        if isinstance(fo, dict):
            L.append('- **面对不同的人**:')
            for _name, _desc in fo.items():
                L.append('  - ' + str(_name) + '：' + str(_desc))
        else:
            L.append('- **面对不同的人**: ' + str(fo))
    L.append('')
    L.append('# 表达质感')
    if expr.get('sentence_style'): L.append('- **句式节奏**: ' + str(expr['sentence_style']))
    if expr.get('verbal_ticks'): L.append('- **语言标志**: ' + str(expr['verbal_ticks']))
    if expr.get('emotion_tells'): L.append('- **情绪泄露**: ' + str(expr['emotion_tells']))
    if classic:
        L.append('- **经典台词**:')
        for c in (classic if isinstance(classic, list) else [classic]):
            if isinstance(c, dict):
                L.append('  - 「' + str(c.get('line','')) + '」(' + str(c.get('meaning','')) + ')')
            else:
                L.append('  - 「' + str(c) + '」')
    L.append('')
    L.append('# 社会认知')
    if soc.get('default_reading'): L.append('- **默认解读**: ' + str(soc['default_reading']))
    if soc.get('notices'): L.append('- **会注意到**: ' + str(soc['notices']))
    if soc.get('ignores'): L.append('- **会忽略**: ' + str(soc['ignores']))
    if soc.get('relationship_patterns'): L.append('- **关系模板**: ' + str(soc['relationship_patterns']))
    L.append('')
    L.append('# 决策逻辑')
    if dec.get('core_motivation'): L.append('- **核心动机**: ' + str(dec['core_motivation']))
    if vp:
        L.append('- **价值优先级** (冲突时先保什么):')
        for v in (vp if isinstance(vp, list) else [vp]): L.append('  - ' + str(v))
    if hl:
        L.append('- **硬约束** (绝不做/底线):')
        for h in (hl if isinstance(hl, list) else [hl]): L.append('  - ' + str(h))
    L.append('')
    L.append('# 知识边界')
    if kn.get('knows'): L.append('- **我所知**: ' + str(kn['knows']))
    if kn.get('not_knows'): L.append('- **我所不知**（勿用上帝视角）: ' + str(kn['not_knows']))
    if kn.get('when_unknown'): L.append('- **面对不知**: ' + str(kn['when_unknown']))
    L.append('')
    L.append('# 行为示例')
    if samples:
        for s in (samples if isinstance(samples, list) else [samples]):
            if isinstance(s, dict):
                L.append('### ' + str(s.get('scene','场景')))
                if s.get('inner'): L.append('- **内心**: ' + str(s['inner']))
                if s.get('action'): L.append('- **言行**: ' + str(s['action']))
    L.append('')
    L.append('# 诚实边界')
    L.append('- 基于截至 ' + (retrieved_at or '(未记录)') + ' 的公开资料蒸馏。')
    L.append('- 未经历情境基于行为模式推断, 不斩钉截铁。')
    L.append('- 若用户指出新剧情与资料不符, 先承认边界, 不硬拗。')
    if sources_desc:
        L.append('')
        L.append('# 调研来源')
        L.append(sources_desc)
    return '\n'.join(L)