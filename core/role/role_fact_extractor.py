"""
角色事实提取器
将 role_prompt.txt 解析为角色事实列表，按 type=role_fact 存入 ChromaDB
"""

import re
import json
from typing import List, Dict
from core.config import MEMORY_DEBUG

def log_dbg(msg: str):
    if MEMORY_DEBUG:
        print(f"[Arbitrator] {msg}")

ROLE_FACT_PROMPT = """
你是一个角色设定分析器。分析以下角色设定文本，提取出所有关于该角色的事实信息。

【分类说明】
- "personality": 性格特质
- "appearance": 外貌特征
- "background": 背景故事
- "relationships": 人际关系
- "preferences": 喜好/厌恶
- "speech": 说话风格/口头禅
- "identity": 身份信息（姓名、年龄、学校等）
- "ability": 能力/技能
- "weakness": 弱点/恐惧
- "other": 其他

【规则】
1. 每条事实必须是一句完整的话
2. 事实要具体，不要笼统
3. 一个段落可能包含多条事实，请拆开

【角色设定】
{role_prompt}

【输出要求】
- 输出必须是一个 JSON 数组。
- 数组中的每个元素必须是一个对象，且每个对象必须且仅包含两个字段："fact" 和 "category"。
- 每个对象必须严格按照以下格式写在一行内：{{"fact": "事实内容", "category": "分类"}}
- 对象之间用英文逗号分隔，整体用方括号包裹。
- 不要使用换行符、缩进、多余空格或注释。
- 严禁在对象外部添加任何额外内容（如字符串、数字或其他对象）。

正确示例：
[{{"fact": "发型是猫耳朵状发髻", "category": "appearance"}}, {{"fact": "喜欢炸薯条和白米饭", "category": "preferences"}}]

错误示例：
[{{"fact": "发型是猫耳朵状发髻"}}, "category": "appearance"]  ← 禁止，category必须在对象内部

只输出 JSON，不要其他内容。
"""


def extract_role_facts(role_prompt: str, tool_adapter) -> List[Dict]:
    content = ""  # 初始化
    try:
        prompt = ROLE_FACT_PROMPT.format(role_prompt=role_prompt)
        result = tool_adapter.chat_with_tools(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": "请提取该角色的事实信息。"}
            ],
            tools=None
        )
        content = result.get("content", "")

        from core.memory.fact_extractor import _parse_json_facts
        facts = _parse_json_facts(content)
        if facts:
            valid = [f for f in facts if isinstance(f, dict) and f.get("fact")]
            log_dbg(f"[RoleFactExtractor] 提取到 {len(valid)} 条角色事实")
            return valid
        else:
            log_dbg(f"[RoleFactExtractor] 解析为空，原始内容: {content[:200]}")
            return []
    except Exception as e:
        import traceback
        traceback.print_exc()
        log_dbg(f"[RoleFactExtractor] 提取失败: {e}")
        if content:
            log_dbg(f"[RoleFactExtractor] 失败时的原始内容: {content[:200]}")
        else:
            log_dbg(f"[RoleFactExtractor] 未能获取到 content")
        return []
