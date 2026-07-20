from typing import List
from .schemas import InfoItem

def filter_by_llm(items: List[InfoItem], user_interests: List[str], tool_adapter) -> List[InfoItem]:
    if not items or not user_interests:
        return []
    
    prompt = f"""
你是一个信息过滤器。判断以下信息与用户兴趣的相关程度，只返回相关度 > 0.6 的信息。

用户兴趣关键词：{', '.join(user_interests[:5])}

信息列表：
{chr(10).join([f"{i+1}. {item.title} {item.content[:100]}" for i, item in enumerate(items)])}

输出格式：只返回数字序号（如 1,3,5），表示相关度高的信息。如果没有，输出 "无"。
"""
    try:
        result = tool_adapter.chat_with_tools(
            messages=[{"role": "system", "content": prompt}],
            tools=None
        )
        content = result.get("content", "").strip()
        if content == "无" or not content:
            return []
        indices = [int(x.strip()) - 1 for x in content.split(',') if x.strip().isdigit()]
        return [items[i] for i in indices if 0 <= i < len(items)]
    except Exception as e:
        print(f"[L3] 过滤失败: {e}")
        return []