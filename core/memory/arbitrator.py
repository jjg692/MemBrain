"""
L4 事实冲突仲裁器

在存储新事实前，与用户已有事实对比，判断冲突类型：
- clear_conflict: 明显矛盾（如"喜欢猫" vs "讨厌猫"）
- partial: 部分矛盾（如"喜欢猫" vs "对猫毛有点过敏"）
- supplement: 互补信息（如"喜欢猫" vs "养了一只布偶猫"）
- no_conflict: 无冲突

用法：
    arbitrator = FactArbitrator(memory, tool_adapter)
    result = arbitrator.arbitrate(new_fact, existing_facts)
    # 返回: {"conflict_type": "no_conflict", "action": "store", ...}
"""

import re
import json
from typing import List, Dict, Optional
from core.config import MEMORY_DEBUG


def log_dbg(msg: str):
    if MEMORY_DEBUG:
        print(f"[Arbitrator] {msg}")


ARBITRATION_PROMPT = """
你是一个事实冲突仲裁器。判断【新事实】与【已有事实列表】之间是否存在冲突。

【判断规则】
1. clear_conflict: 新事实与某条已有事实明显矛盾、对立（如"喜欢"vs"讨厌"、"能吃辣"vs"不能吃辣"）
2. partial: 新事实与某条已有事实部分矛盾，但不完全对立（程度/条件/时间上的变化）
3. supplement: 新事实与已有事实互补，提供额外细节但不矛盾（可并存）
4. no_conflict: 新事实与所有已有事实无关或无矛盾

【输出格式】
{
    "conflict_type": "clear_conflict|partial|supplement|no_conflict",
    "conflict_with": "与之冲突的已有事实原文（如无则为空）",
    "reason": "判断理由（一句话）",
    "action": "replace|merge|store"
}

【action 规则】
- clear_conflict → action = "replace"（新事实覆盖旧事实）
- partial → action = "merge"（需要合并两条事实）
- supplement → action = "store"（直接存，不需要特殊处理）
- no_conflict → action = "store"（直接存）

【额外要求】
- 当 conflict_type 为 "no_conflict" 时，conflict_with 必须为 ""（空字符串）
- 所有字段都必须出现，即使为空也要写
- 只输出 JSON，不要其他任何内容

【示例】
新事实：用户说“我最喜欢吃草莓蛋糕”
已有事实：用户说“我喜欢吃草莓蛋糕”
输出：
{
    "conflict_type": "clear_conflict",
    "conflict_with": "用户说\"我喜欢吃草莓蛋糕\"",
    "reason": "用户明确表示不喜欢草莓蛋糕，与已有事实矛盾",
    "action": "replace"
}

新事实：用户打招呼“你好”
已有事实：无
输出：
{
    "conflict_type": "no_conflict",
    "conflict_with": "",
    "reason": "新事实是打招呼，不涉及任何冲突",
    "action": "store"
}

只输出 JSON，不要其他内容。
"""


class FactArbitrator:
    """事实冲突仲裁器"""

    def __init__(self, memory, tool_adapter):
        self.memory = memory
        self.tool_adapter = tool_adapter

    def arbitrate(self, new_fact: Dict, existing_facts: List[Dict]) -> Dict:
        """
        仲裁一条新事实与已有事实列表的冲突关系

        Args:
            new_fact: {"fact": "xxx", "category": "preference"}
            existing_facts: [{"document": "xxx", "metadata": {...}}, ...]

        Returns:
            {{"conflict_type": str, "conflict_with": str, "reason": str, "action": str}}
        """
        if not existing_facts:
            return {
                "conflict_type": "no_conflict",
                "conflict_with": "",
                "reason": "无已有事实，直接存储",
                "action": "store"
            }

        # 构建已有事实列表文本
        existing_texts = "\n".join([
            f"- {f['document']} (分类: {f.get('metadata', {}).get('category', 'unknown')})"
            for f in existing_facts
        ])

        prompt = ARBITRATION_PROMPT
        user_content = f"""【新事实】
{new_fact['fact']} (分类: {new_fact.get('category', 'general')})

【已有事实】
{existing_texts}

请判断新事实与已有事实的冲突关系。"""

        try:
            result = self.tool_adapter.chat(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_content}
                ]
            )
            content = result.get("content", "")

            print(f"事实仲裁器的原始输出：{content}")
            
            # 用健壮的 JSON 解析
            data = _parse_arbitration_json(content)
            if data:
                conflict_type = data.get("conflict_type", "no_conflict")
                conflict_with = data.get("conflict_with", "")
                reason = data.get("reason", "")
                action = data.get("action", "store")

                log_dbg(f"仲裁结果: {conflict_type} | action={action} | {reason}")
                return {
                    "conflict_type": conflict_type,
                    "conflict_with": conflict_with,
                    "reason": reason,
                    "action": action
                }

            log_dbg("仲裁器返回格式异常，降级为 no_conflict")
            return {
                "conflict_type": "no_conflict",
                "conflict_with": "",
                "reason": "仲裁器返回格式异常",
                "action": "store"
            }

        except Exception as e:
            log_dbg(f"仲裁失败: {e}，降级为 no_conflict")
            return {
                "conflict_type": "no_conflict",
                "conflict_with": "",
                "reason": f"仲裁器异常: {e}",
                "action": "store"
            }

def _parse_arbitration_json(content: str) -> Optional[Dict]:
    """健壮地解析仲裁器返回的 JSON，处理单引号、多余文本等"""
    if not content or not content.strip():
        return None
    
    # 1. 尝试提取 JSON 对象 {...}
    json_match = re.search(r'\{.*\}', content, re.DOTALL)
    if not json_match:
        return None
    
    json_str = json_match.group()
    
    # 2. 尝试直接解析
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass
    
    # 3. 替换单引号为双引号再试
    _fixed = json_str.replace("'", '"')
    try:
        return json.loads(_fixed)
    except json.JSONDecodeError:
        pass
    
    # 4. 暴力提取关键字段
    result = {}
    ct = re.search(r'"[Cc]onflict_type"\s*:\s*"([^"]+)"', json_str)
    if ct:
        result["conflict_type"] = ct.group(1)
    cw = re.search(r'"[Cc]onflict_with"\s*:\s*"([^"]*)"', json_str)
    if cw:
        result["conflict_with"] = cw.group(1)
    r = re.search(r'"[Rr]eason"\s*:\s*"([^"]*)"', json_str)
    if r:
        result["reason"] = r.group(1)
    a = re.search(r'"[Aa]ction"\s*:\s*"([^"]+)"', json_str)
    if a:
        result["action"] = a.group(1)
    
    if result.get("conflict_type") and result.get("action"):
        return result
    
    return None