# agent/router.py
def classify_query(self, user_message: str) -> str:
    """
    判断是否需要联网搜索
    返回: "REALTIME" | "PERSONAL"
    优化: 关键词快速匹配 + LLM降级
    """
    # === 第一步：关键词快速匹配 ===
    # 强实时关键词（必走搜索）
    strong_realtime = ["天气", "新闻", "汇率", "股价", "股票", "油价", "联网搜索", "实时"]
    for kw in strong_realtime:
        if kw in user_message:
            if MEMORY_DEBUG:
                print(f"[Router] 强关键词命中: {kw} -> REALTIME")
            return "REALTIME"
    
    # 弱关键词（需要LLM二次确认）
    weak_realtime = ["今天", "明天", "最近", "最新", "查询", "推荐", "附近", "多少", "什么时候"]
    has_weak = any(kw in user_message for kw in weak_realtime)
    
    # === 第二步：LLM判断（仅对弱关键词或无法快速判断的情况） ===
    if has_weak or len(user_message) < 10:
        router_prompt = f"""
        判断用户问题是否需要联网搜索才能回答。

        【问题】
        {user_message}

        【规则】
        1. 需要联网：天气、新闻、实时数据、最新动态、推荐、查询具体信息、当前位置相关
        2. 不需要联网：闲聊、个人偏好、历史事实、角色知识、生活常识

        【输出格式】
        只输出 "REALTIME" 或 "PERSONAL"，不要输出其他内容。
        """
        try:
            result = self.tool_adapter.chat_with_tools(
                messages=[{"role": "system", "content": router_prompt}],
                tools=None
            )
            content = result.get("content", "").strip()
            if "REALTIME" in content or "需要联网" in content:
                return "REALTIME"
            else:
                return "PERSONAL"
        except Exception as e:
            if MEMORY_DEBUG:
                print(f"[Router] LLM判断失败: {e}")
            return "REALTIME" if has_weak else "PERSONAL"
    
    # === 第三步：默认走记忆 ===
    return "PERSONAL"