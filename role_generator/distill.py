
"""
行为蒸馏器: 把多来源角色资料蒸馏为 CSP 风格角色行为规则
"""
import json
import re
from typing import Dict, Optional

from core.adapters import OllamaAdapter


# 蒸馏 prompt: 指导模型把设定变行为
# 说明: schema 里的 { } 给 LLM 看; distill_character() 用 + 拼接(不 format), 无需 {{ }} 转义。
DISTILL_SYSTEM = (
    "你是角色行为蒸馏引擎。把'角色是什么样的'提炼成'角色会怎么行动、怎么说、怎么想'。\n"
    "严格按如下 JSON 输出, 不要输出其他: {\n"
    "  \"display_name\":\"中文名\",\"origin\":\"原文名\",\"tagline\":\"代表性一句话\",\n"
    "  \"roleplay_rules\":[\"硬规则...\"],\"identity\":{\"who\":\"我是谁(第一人称)\",\"world\":\"世界观\",\"first_impression\":\"第一印象\"},\n"
    "  \"behavior\":{\"default\":\"默认行为(情境化)\",\"under_pressure\":\"压力行为\",\"core_conflict\":\"核心矛盾\",\"facing_others\":\"对不同的人\"},\n"
    "  \"expression\":{\"sentence_style\":\"句式节奏\",\"verbal_ticks\":\"口癖/自称/语尾\",\"emotion_tells\":\"情绪泄露\",\n"
    "    \"classic_lines\":[{\"line\":\"台词原文\",\"meaning\":\"含义\",\"emotion\":\"适用情绪\",\"scene\":\"适用场景\"}]},\n"
    "  \"social_cognition\":{\"default_reading\":\"默认解读\",\"notices\":\"注意到\",\"ignores\":\"忽略\",\"relationship_patterns\":\"关系模板\"},\n"
    "  \"decision_logic\":{\"core_motivation\":\"核心动机\",\"value_priority\":[\"价值优先级\"],\"hard_limits\":[\"硬约束\"]},\n"
    "  \"knowledge_boundary\":{\"knows\":\"所知\",\"not_knows\":\"所不知(勿用上帝视角)\",\"when_unknown\":\"面对不知\"},\n"
    "  \"sample_dialogues\":[{\"scene\":\"情境\",\"user_line\":\"用户会触发的话\",\"role_line\":\"角色实际回应(含口癖)\",\"inner\":\"内心\",\"action\":\"肢体/语气/动作\"}],\n"
    "  \"skills\":[{\"skill_name\":\"技能名\",\"trigger\":\"触发情境\",\"keywords\":[\"关键词\"],\"action\":\"动作/应对步骤\",\"tone\":\"基调\",\"forbidden\":\"禁止(可选)\"}]\n"
    "}\n"
    "规则: 用'情境->行为'描述而非形容词堆砌; 保留原角色口癖/自称/语尾(保留日文+中文说明); 资料不足写'推测'别编造; 保留矛盾。\n"
    "skills 必须 5-8 条, 覆盖(至少6类): 情绪低谷 / Live前 / 初次见面 / 朋友分歧 / 独处 / 被夸奖 / 被请求帮忙 / 冷场。\n"
    "sample_dialogues 必须含 user_line + role_line 成对, 展示角色'接话'的即时反应。classic_lines 每条约含适用情绪/场景。"
)


_FENCE = chr(96) * 3  # 三个反引号, 用于剥离 JSON 围栏


def distill_character(adapter, character, source_text, work=''):
    """调用 LLM 蒸馏, 返回行为 JSON dict"""
    prompt = DISTILL_SYSTEM + '\n\n角色名: ' + character + ('\n作品: ' + work if work else '') + '\n资料:\n' + source_text
    try:
        text = adapter.chat([{'role': 'system', 'content': DISTILL_SYSTEM}, {'role': 'user', 'content': prompt}])
        text = re.sub(r'^' + _FENCE + '(?:json)?\s*|\s*' + _FENCE + '$', '', (text or '').strip())
        data = json.loads(_extract_json(text))
        return data if isinstance(data, dict) else None
    except Exception as e:
        print('[distill] 失败:', e)
        return None


def _extract_json(text):
    """从文本中提取第一个完整 JSON 对象。

    优先整体解析；失败时用花括号配对扫描提取真正闭合的对象，
    避免贪婪正则被对象值内部的 '{}' 或尾部杂质截断。
    """
    if not text:
        return '{}'
    try:
        json.loads(text)
        return text
    except Exception:
        pass
    start = text.find('{')
    if start < 0:
        return '{}'
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return '{}'


def _norm_list(x):
    """把标量/列表统一成列表（防御）"""
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _render_classic_lines(classic):
    """富化渲染经典台词(兼容旧 string / 新 dict 两种)。返回 list[str]"""
    lines = []
    for c in _norm_list(classic):
        if isinstance(c, dict):
            line = str(c.get('line', '') or '')
            if not line:
                continue
            tag = []
            if c.get('emotion'):
                tag.append(str(c['emotion']))
            if c.get('scene'):
                tag.append(str(c['scene']))
            if c.get('meaning'):
                tag.append(str(c['meaning']))
            suffix = ('（' + ' · '.join(tag) + '）') if tag else ''
            lines.append('  - 「' + line + '」' + suffix)
        else:
            lines.append('  - 「' + str(c) + '」')
    return lines


def _render_sample_dialogues(samples):
    """富化渲染接话示例(兼容旧 scene/inner/action 与新 user_line/role_line)"""
    blocks = []
    for s in _norm_list(samples):
        if not isinstance(s, dict):
            continue
        lines = []
        lines.append('### ' + str(s.get('scene', '场景')))
        if s.get('user_line'):
            lines.append('- **用户触发**: ' + str(s['user_line']))
        if s.get('role_line'):
            lines.append('- **角色回应**: ' + str(s['role_line']))
        if s.get('inner'):
            lines.append('- **内心**: ' + str(s['inner']))
        if s.get('action'):
            lines.append('- **言行**: ' + str(s['action']))
        blocks.append('\n'.join(lines))
    return blocks


def _render_skills(skills):
    """渲染技能块: 触发->动作"""
    blocks = []
    for sk in _norm_list(skills):
        if not isinstance(sk, dict):
            continue
        name = sk.get('skill_name')
        if not name:
            continue
        lines = ['### ' + str(name)]
        if sk.get('trigger'):
            lines.append('- **触发**: ' + str(sk['trigger']))
        if sk.get('keywords'):
            lines.append('- **关键词**: ' + ' / '.join(str(k) for k in _norm_list(sk['keywords'])))
        if sk.get('action'):
            lines.append('- **动作**: ' + str(sk['action']))
        if sk.get('tone'):
            lines.append('- **基调**: ' + str(sk['tone']))
        if sk.get('forbidden'):
            lines.append('- **禁止**: ' + str(sk['forbidden']))
        blocks.append('\n'.join(lines))
    return blocks


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
    skills = (data.get('skills') or []) if isinstance(data, dict) else []

    display = str(data.get('display_name') or character)
    origin = str(data.get('origin') or '') if data.get('origin') else ''
    L = ['你是' + display + (('（' + origin + '）') if origin else '')]
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
        L.extend(_render_classic_lines(classic))
    L.append('')

    # ===== 角色技能（触发->动作）· 新增，压 OOC 核心 =====
    if skills:
        L.append('# 角色技能（触发→动作）')
        L.append('遇到下列触发情境时，按对应动作自然应对，像角色本人一样，不要生硬念脚本：')
        L.extend(_render_skills(skills))
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
        blocks = _render_sample_dialogues(samples)
        if blocks:
            for b in blocks:
                L.append(b)
                L.append('')
            while L and L[-1] == '':
                L.pop()
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
