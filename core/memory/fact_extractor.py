"""
L4 事实抽取器：从对话中提取用户偏好/事件/承诺
"""
import re
import json
from typing import List, Dict

from core.config import MEMORY_DEBUG


def log_dbg(msg: str):
    if MEMORY_DEBUG:
        print(f"[FactExtractor] {msg}")


FACT_EXTRACTION_PROMPT = """
分析以下对话，提取关于用户的**事实信息**（偏好、习惯、重要事件、人际关系）。

注意事项：
1. 只提取用户**明确表达**的信息，不要推断
2. 如果用户没有表达任何事实信息，输出空数组 []
3. 每条事实必须是一句完整的话
4. 分类说明：
   - "preference": 喜好/偏好（喜欢吃什么、喜欢什么音乐等）
   - "event": 事件（用户提到做了什么事、去了哪里）
   - "commitment": 承诺/约定（用户答应做什么）
   - "relationship": 人际关系（提到家人、朋友等）

对话：
用户：{user_msg}
助手：{assistant_msg}

输出 JSON 数组，格式：[{{"fact": "...", "category": "preference|event|commitment|relationship"}}]
只输出 JSON，不要其他内容。
"""


def extract_facts(user_msg: str, assistant_msg: str, tool_adapter) -> List[Dict]:
    """抽取事实，返回事实列表，失败时返回空列表"""
    try:
        prompt = FACT_EXTRACTION_PROMPT.format(
            user_msg=user_msg[:500],
            assistant_msg=assistant_msg[:500]
        )
        result = tool_adapter.chat_with_tools(
            messages=[{"role": "system", "content": prompt}],
            tools=None
        )
        content = result.get("content", "")
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            facts = json.loads(json_match.group())
            if isinstance(facts, list):
                # 过滤无效事实
                valid = [
                    f for f in facts
                    if isinstance(f, dict) and f.get("fact")
                ]
                log_dbg(f"抽取到 {len(valid)} 条事实")
                return valid
        return []
    except Exception as e:
        log_dbg(f"抽取失败: {e}")
        return []