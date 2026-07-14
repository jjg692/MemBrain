# agent/router.py
def classify_query(self, user_message: str) -> str:
    """
    判断改写后的问题是否需要联网搜索。
    返回: "REALTIME" | "PERSONAL"
    """
    # 由于问题已经改写完整，不再需要 HYBRID 分支
    # 直接用关键词或调用模型判断是否需要联网
    router_prompt = f"""
判断用户问题是否需要联网搜索才能回答。

【问题】
{user_message}

【规则】
1. 需要联网：天气、新闻、实时数据、最新动态、推荐、查询具体信息
2. 不需要联网：闲聊、个人偏好、历史事实、角色知识

【输出】
只输出 "需要联网" 或 "不需要联网"
"""
    try:
        result = self.tool_adapter.chat_with_tools(
            messages=[{"role": "system", "content": router_prompt}],
            tools=None
        )
        content = result.get("content", "")
        if "需要联网" in content:
            return "REALTIME"
        else:
            return "PERSONAL"
    except Exception:
        # 默认走记忆
        return "PERSONAL"