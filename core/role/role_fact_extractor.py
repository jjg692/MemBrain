"""
角色事实提取器
将 role_prompt.txt 解析为角色事实列表，按 type=role_fact 存入 ChromaDB
"""

import re
import json
from typing import List, Dict

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

输出 JSON 数组，格式：[{{"fact": "具体事实", "category": "分类"}}]
只输出 JSON，不要其他内容。
"""


def extract_role_facts(role_prompt: str, tool_adapter) -> List[Dict]:
    """提取角色事实，返回事实列表"""
    try:
        prompt = ROLE_FACT_PROMPT.format(role_prompt=role_prompt[:3000])
        result = tool_adapter.chat_with_tools(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": "请提取该角色的事实信息。"}
            ],
            tools=None
        )
        content = result.get("content", "")
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            facts = json.loads(json_match.group())
            if isinstance(facts, list):
                valid = [f for f in facts if isinstance(f, dict) and f.get("fact")]
                print(f"[RoleFactExtractor] 提取到 {len(valid)} 条角色事实")
                return valid
        return []
    except Exception as e:
        print(f"[RoleFactExtractor] 提取失败: {e}")
        return []