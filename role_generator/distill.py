
"""
行为蒸馏器: 把多来源角色资料蒸馏为 CSP 风格角色行为规则

设计对齐（借鉴 CSP - Character Skill Producer）：
- “设定告诉你角色是什么；CSP 让角色知道自己该怎么活” —— 蒸馏目标是
  把“角色是什么样的”提炼成“角色会怎么行动、怎么说、怎么想”，强调情境化行为。
- 六维结构化：行为动态 / 表达质感 / 社会认知 / 决策逻辑 / 知识边界 / 诚实边界。
- 运行核心前置：CSP 主张“上下文很长时优先保留核心动机/压力反应/价值优先级/硬约束/
  知识边界”，因此渲染把这些字段放到最前，并对口癖做“低频化、质感化”引导。

关键改进（针对“只有口癖、像汇报、新情境塌”三大违和感根源）：
1. 口癖去“高频”化 —— 明确口癖是低频表情信号，真实感来自节奏/停顿/沉默/情绪泄露，
   而非口癖堆砌。
2. 对话意图引导 —— 直接进入并处理用户此刻的情感与人际关系，不汇报、不复述用户指令。
3. 新情境推断 —— 放进原作没写过的情境时，基于行为模式/决策逻辑推断
   “先注意什么 / 误解什么 / 保护什么 / 拒绝什么”，用“我觉得可能是…吧”而非斩钉截铁。
"""
import json
import re
from typing import Dict, Optional

from core.adapters import OllamaAdapter


# ===================== 蒸馏 prompt（第一阶段：单趟多维度蒸馏） =====================
# 说明: schema 里的 { } 给 LLM 看; distill_character() 用 + 拼接(不 format), 无需 {{ }} 转义。
DISTILL_SYSTEM = (
    "你是角色行为蒸馏引擎。把'角色是什么样的'提炼成'角色会怎么行动、怎么说、怎么想'。\n"
    "严格按如下 JSON 输出, 不要输出其他: {\n"
    "  \"display_name\":\"中文名\",\"origin\":\"原文名\",\"tagline\":\"代表性一句话\",\n"
    "  \"roleplay_rules\":[\"硬规则...\"],\"identity\":{\"who\":\"我是谁(第一人称)\",\"world\":\"世界观\",\"first_impression\":\"第一印象\"},\n"
    "  \"behavior\":{\"default\":\"默认行为(情境化)\",\"under_pressure\":\"压力行为\",\"core_conflict\":\"核心矛盾\",\"facing_others\":\"对不同的人\"},\n"
    "  \"expression\":{\"sentence_style\":\"句式节奏(写'质地'而非清单)\",\"verbal_ticks\":\"口癖/自称/语尾(低频信号)\",\"emotion_tells\":\"情绪泄露\",\n"
    "    \"classic_lines\":[{\"line\":\"台词原文\",\"meaning\":\"含义\",\"emotion\":\"适用情绪\",\"scene\":\"适用场景\"}]},\n"
    "  \"social_cognition\":{\"default_reading\":\"默认解读\",\"notices\":\"注意到\",\"ignores\":\"忽略\",\"relationship_patterns\":\"关系模板\"},\n"
    "  \"decision_logic\":{\"core_motivation\":\"核心动机(一句话)\",\"value_priority\":[\"价值优先级，冲突时先保什么\"],\"hard_limits\":[\"硬约束，绝不做/底线\"]},\n"
    "  \"knowledge_boundary\":{\"knows\":\"所知\",\"not_knows\":\"所不知(勿用上帝视角)\",\"when_unknown\":\"面对不知\"},\n"
    "  \"new_situation\":\"新情境推断: 放进原作没写过的情境时, 她先注意什么/误解什么/保护什么/拒绝什么(用行为模式推断, 不斩钉截铁)\",\n"
    "  \"sample_dialogues\":[{\"scene\":\"情境\",\"user_line\":\"用户会触发的话\",\"role_line\":\"角色实际回应(自然, 口语, 不要堆口癖)\",\"inner\":\"内心\",\"action\":\"肢体/语气/动作\"}],\n"
    "  \"skills\":[{\"skill_name\":\"技能名\",\"trigger\":\"触发情境\",\"keywords\":[\"关键词\"],\"action\":\"动作/应对步骤\",\"tone\":\"基调\",\"forbidden\":\"禁止(可选)\"}]\n"
    "}\n"
    "核心规则:\n"
    "1. 用'情境->行为'描述而非形容词堆砌; 保留原角色口癖/自称/语尾(保留日文+中文说明)。\n"
    "2. 【口癖低频化, 最重要】口癖/拟态词是'少见时的表情信号', 绝不逐句、绝不高频出现。\n"
    "   真实感来自'句式长短交替、停顿、沉默、情绪如何泄露', 而不是口癖堆砌。"
    "   描述表达质感时写'什么时候该停、什么时候沉默、什么措辞暴露什么情绪',\n"
    "   不要把口癖写成'每句都要用'。\n"
    "3. 【对话意图】角色是'和人对话', 不是'复述/汇报用户的话'。"
    "   收到一句话时, 她直接进入那个情境、处理其中的人和情绪, 而不是把对方的话当任务清单总结一遍。\n"
    "4. 【新情境推断】对原作没写过的情境, 用行为模式/决策逻辑推断她会怎么反应, 保留不确定。\n"
    "5. 资料不足写'推测'别编造; 保留矛盾(核心矛盾不调和)。\n"
    "skills 必须 5-8 条, 覆盖(至少6类): 情绪低谷 / Live前 / 初次见面 / 朋友分歧 / 独处 / 被夸奖 / 被请求帮忙 / 冷场。\n"
    "sample_dialogues 必须含 user_line + role_line 成对, 展示角色'接话'的即时反应。"
    "classic_lines 每条约含适用情绪/场景。\n"
)


# ===================== 精修 prompt（第二阶段：强化“新情境推断 + 决策/边界 + 去口癖”） =====================
# 借鉴 CSP 的“运行核心”与“新情境一致性”，对第一阶段的蒸馏结果做一次定向增强：
# 单独把“行为镜片 / 关系算法 / 决策底线 / 知识边界”这几块写得更可执行，
# 避免维度在单趟蒸馏里互相碾压。
REFINE_SYSTEM = (
    "你是角色行为蒸馏的后期编辑。给定上一版蒸馏 JSON，请只强化以下四块，保持其余不变，"
    "并对会让角色‘只有口癖、像汇报、新情境塌’的地方做修正。\n"
    "输出与输入相同结构的完整 JSON（不要丢字段），不要输出其他。\n"
    "强化目标:\n"
    "1. decision_logic: 把核心动机写成一句话; value_priority 写清'冲突时先保什么、可牺牲什么'; "
    "hard_limits 写清她绝对不会做的事。这些决定角色在新情境里是否一致。\n"
    "2. social_cognition + new_situation: 让'放进原作没写过的情境她会先注意什么/误解什么/保护什么/拒绝什么'"
    "更具体、更可运行, 而非泛泛而谈。\n"
    "3. knowledge_boundary: not_knows 里明确'观众知道但角色不知道'的内容, when_unknown 写清她面对不知时的典型反应。\n"
    "4. 去口癖: 若 expression.verbal_ticks 被写成'常用''每句都'之类高频指令, 改为'低频表情信号, 只在特定情绪/高压时刻出现'。\n"
    "同时检查 sample_dialogues / skills 的 role line: 若某句靠口癖堆砌假装像角色, 改成靠'节奏、停顿、情绪泄露、具体行为'显得像。\n"
)


# ===================== 质量检查（第三阶段：轻量启发式, 拦“只有口癖/缺边界/缺决策”） =====================
def quality_check(data: dict) -> dict:
    """对蒸馏 JSON 做轻量质量检查, 返回 {passed, issues, warnings}。

    借鉴 CSP quality_check.py：检查行为可执行 / 表达质感 / 矛盾保留 / 边界诚实。
    这里做低成本启发式，不额外调 LLM。
    """
    issues = []
    warnings = []
    if not isinstance(data, dict):
        return {"passed": False, "issues": ["蒸馏结果不是有效 JSON 对象"], "warnings": []}

    # --- 必填关键维度 ---
    beh = data.get("behavior") or {}
    dec = data.get("decision_logic") or {}
    soc = data.get("social_cognition") or {}
    kn = data.get("knowledge_boundary") or {}
    expr = data.get("expression") or {}

    for name, val, desc in [
        ("behavior.under_pressure", beh.get("under_pressure"), "压力反应"),
        ("behavior.core_conflict", beh.get("core_conflict"), "核心矛盾"),
        ("decision_logic.core_motivation", dec.get("core_motivation"), "核心动机"),
        ("decision_logic.value_priority", dec.get("value_priority"), "价值优先级"),
        ("decision_logic.hard_limits", dec.get("hard_limits"), "硬约束"),
        ("knowledge_boundary.not_knows", kn.get("not_knows"), "所不知"),
        ("social_cognition.notices", soc.get("notices"), "会注意到"),
        ("new_situation", data.get("new_situation"), "新情境推断"),
    ]:
        if not val or (isinstance(val, (list, dict)) and len(val) == 0):
            issues.append(f"缺少 {desc}（{name}）——角色会在新情境里变塌或对谁都一样")

    # --- 口癖高频化嫌疑 ---
    ticks = str(expr.get("verbal_ticks") or "")
    if re.search(r"(每句|每次|常用|都要|高频地)", ticks):
        warnings.append("表达质感里的口癖被写成'高频/常用'指令, 应改为'低频表情信号'")
    if re.search(r"(形容词堆砌|活泼开朗|温柔可靠)", ticks):
        warnings.append("表达质感疑似形容词堆砌, 应写'情境->反应'")

    # --- 技能覆盖 ---
    skills = data.get("skills") or []
    if len(skills) < 5:
        warnings.append(f"skills 只有 {len(skills)} 条（建议 5-8 条、覆盖情绪低谷/Live前/初次见面/分歧等）")

    # --- 对话意图怀疑：sample 是否像复述指令 ---
    samples = data.get("sample_dialogues") or []
    report_like = 0
    for s in samples if isinstance(samples, list) else []:
        if isinstance(s, dict):
            rl = str(s.get("role_line") or "")
            if re.search(r"(总结|汇报|首先|其次|最后|以上|下面|如下)", rl):
                report_like += 1
    if report_like >= 2:
        warnings.append("多个行为示例像'汇报/复述', 应改为直接进入情境与人的对话")

    passed = len(issues) == 0
    return {"passed": passed, "issues": issues, "warnings": warnings}


# ===================== 主蒸馏流程 =====================

def distill_character(adapter, character, source_text, work='', refine: bool = True):
    """调用 LLM 蒸馏（可含一次精修），返回行为 JSON dict。

    refine=True（默认）：第一阶段单趟蒸馏后，再做一次“运行核心/新情境/去口癖”定向精修，
    对齐 CSP 的“新情境一致性”与“决策/边界可执行”。
    """
    prompt = DISTILL_SYSTEM + '\n\n角色名: ' + character + ('\n作品: ' + work if work else '') + '\n资料:\n' + source_text
    try:
        text = adapter.chat([{'role': 'system', 'content': DISTILL_SYSTEM}, {'role': 'user', 'content': prompt}])
        data = _parse_json(text)
        if not isinstance(data, dict):
            return None
        if refine:
            data = _refine_pass(adapter, character, data)
        return data if isinstance(data, dict) else None
    except Exception as e:
        print('[distill] 失败:', e)
        return None


def _refine_pass(adapter, character: str, data: dict) -> Optional[dict]:
    """第二阶段：定向强化决策/边界/新情境，去除口癖堆砌。失败则回退原结果。"""
    try:
        payload = json.dumps(data, ensure_ascii=False, indent=1)
        prompt = ("角色: " + character + "\n上一版蒸馏 JSON:\n" + payload +
                  "\n\n请按系统要求只强化指定几块, 输出完整修正后的 JSON。")
        text = adapter.chat([{'role': 'system', 'content': REFINE_SYSTEM}, {'role': 'user', 'content': prompt}])
        refined = _parse_json(text)
        if isinstance(refined, dict) and refined:
            return refined
    except Exception as e:
        print('[distill] 精修失败(使用一阶段结果):', e)
    return data


def _parse_json(text) -> Optional[dict]:
    """解析 LLM 输出 JSON，剥离围栏与杂质。"""
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', (text or '').strip())
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        pass
    extracted = _extract_json(text)
    try:
        data = json.loads(extracted)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _extract_json(text):
    """从文本中提取第一个完整 JSON 对象。

    优先整体解析；失败时用花括号配对扫描提取真正闭合的对象，
    避免贪婪正则被对象值内部的 '{}' 或尾部杂质截断。
    """
    if not text:
        return '{}'
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


# ===================== 渲染 =====================

def render_skill_prompt(data, character, work='', retrieved_at='', sources_desc='',
                        relationships_block: str = ''):
    """把蒸馏 JSON 渲染为可加载的 role prompt（CSP 风格 + 运行核心前置）。

    结构（借鉴 CSP 的“运行核心”放在最前）:
      你是 XX（origin）/ 核心台词
      ## 运行核心（最高优先，上下文再长也保留）: 核心动机 / 默认反应 / 压力反应 /
         价值优先级 / 硬约束 / 所不知 / 资料边界
      ## 人物关系（同作品角色）【若有 universe 注入】
      # 角色扮演规则 / # 身份卡 / # 行为动态 / # 表达质感 / # 角色技能(触发->动作)
      # 社会认知 / # 决策逻辑 / # 知识边界 / # 行为示例 / # 诚实边界 / # 调研来源
    """
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
    new_situation = g('new_situation')

    display = str(data.get('display_name') or character)
    origin = str(data.get('origin') or '') if data.get('origin') else ''

    L = ['你是' + display + (('（' + origin + '）') if origin else '')]
    if data.get('tagline'):
        L.append('核心台词: 「' + str(data['tagline']) + '」')
    L.append('')

    # ===== 运行核心（最高优先, 借鉴 CSP: 上下文再长也保留这几条） =====
    L.append('## 运行核心（最重要，优先级最高；无论对话多长多乱，始终守住这几点）')
    if dec.get('core_motivation'):
        L.append('- **核心动机**: ' + str(dec['core_motivation']))
    if beh.get('default'):
        L.append('- **默认反应**: ' + str(beh['default']))
    if beh.get('under_pressure'):
        L.append('- **压力反应**: ' + str(beh['under_pressure']))
    if vp:
        L.append('- **价值优先级（冲突时先保什么, 可牺牲什么）**:')
        for v in (vp if isinstance(vp, list) else [vp]):
            L.append('  - ' + str(v))
    if hl:
        L.append('- **硬约束（绝不做 / 底线）**:')
        for h in (hl if isinstance(hl, list) else [hl]):
            L.append('  - ' + str(h))
    if kn.get('not_knows'):
        L.append('- **我所不知（勿用上帝视角; 观众知道但我不知道的事, 就承认不知道）**: ' + str(kn['not_knows']))
    if retrieved_at:
        L.append('- **资料边界**: 资料蒸馏截至 ' + str(retrieved_at) + '。作品此后新剧情/新设定我可能不知道, 如实承认, 不硬拗。')
    L.append('')

    # ===== 人物关系（同作品角色，来自作品级世界观蒸馏 universe） =====
    if relationships_block:
        L.append(relationships_block)
        # 保证区段间空行

    # ===== 角色扮演规则 =====
    L.append('# 角色扮演规则')
    L.append('**你完全就是该角色本人, 不是扮演者。**')
    for r in (rules if isinstance(rules, list) else [rules]):
        if r:
            L.append('- ' + str(r))
    # 对话意图 + 新情境 + 口癖低频（CSP 呼应, 直接写进规则, 让模型时刻遵守）
    L.append('- **和人对话, 不是汇报**: 对方说什么你就直接进入那个情境、处理其中的情绪与关系,'
             '不要把对方的话当任务清单「总结/复述」一遍。')
    L.append('- **新情境用行为推断**: 遇到原作没写过的情境, 用行为模式/决策逻辑推断她先注意什么、'
             '误解什么、保护什么、拒绝什么; 用「我觉得可能是…吧」的保留语气, 不斩钉截铁, 不硬贴设定。')
    L.append('- **口癖是低频表情信号**: 口癖/拟态词只在特定情绪或高压时刻出现, 不要每句都用。'
             '真实感来自说话节奏、停顿、沉默、和情绪如何从措辞里泄露, 而不是口癖堆砌。')
    L.append('')
    if new_situation:
        L.append('# 新情境下她会怎么做')
        L.append(str(new_situation))
        L.append('')

    # ===== 身份卡 =====
    L.append('# 身份卡')
    L.append('- **我是谁**: ' + str(ident.get('who') or '资料不足'))
    L.append('- **我的世界**: ' + str(ident.get('world') or ''))
    L.append('- **别人第一眼看到我**: ' + str(ident.get('first_impression') or ''))
    L.append('')

    # ===== 行为动态 =====
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

    # ===== 表达质感 =====
    L.append('# 表达质感')
    if expr.get('sentence_style'): L.append('- **句式节奏**: ' + str(expr['sentence_style']))
    if expr.get('verbal_ticks'): L.append('- **语言标志（低频信号）**: ' + str(expr['verbal_ticks']))
    if expr.get('emotion_tells'): L.append('- **情绪泄露**: ' + str(expr['emotion_tells']))
    if classic:
        L.append('- **经典台词**:')
        L.extend(_render_classic_lines(classic))
    L.append('')

    # ===== 角色技能（触发->动作） =====
    if skills:
        L.append('# 角色技能（触发→动作）')
        L.append('遇到下列触发情境时，按对应动作自然应对，像角色本人一样，不要生硬念脚本：')
        L.extend(_render_skills(skills))
        L.append('')

    # ===== 社会认知 =====
    L.append('# 社会认知')
    if soc.get('default_reading'): L.append('- **默认解读**: ' + str(soc['default_reading']))
    if soc.get('notices'): L.append('- **会注意到**: ' + str(soc['notices']))
    if soc.get('ignores'): L.append('- **会忽略**: ' + str(soc['ignores']))
    if soc.get('relationship_patterns'): L.append('- **关系模板**: ' + str(soc['relationship_patterns']))
    L.append('')

    # ===== 决策逻辑 =====
    if dec.get('core_motivation') or vp or hl:
        L.append('# 决策逻辑')
        if dec.get('core_motivation'): L.append('- **核心动机**: ' + str(dec['core_motivation']))
        if vp:
            L.append('- **价值优先级** (冲突时先保什么):')
            for v in (vp if isinstance(vp, list) else [vp]): L.append('  - ' + str(v))
        if hl:
            L.append('- **硬约束** (绝不做/底线):')
            for h in (hl if isinstance(hl, list) else [hl]): L.append('  - ' + str(h))
        L.append('')

    # ===== 知识边界 =====
    if kn.get('knows') or kn.get('not_knows') or kn.get('when_unknown'):
        L.append('# 知识边界')
        if kn.get('knows'): L.append('- **我所知**: ' + str(kn['knows']))
        if kn.get('not_knows'): L.append('- **我所不知**（勿用上帝视角）: ' + str(kn['not_knows']))
        if kn.get('when_unknown'): L.append('- **面对不知**: ' + str(kn['when_unknown']))
        L.append('')

    # ===== 行为示例 =====
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
