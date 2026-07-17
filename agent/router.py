# agent/router.py
from typing import Dict, Optional
from core.config import MEMORY_DEBUG

def classify_query(self, user_message: str, rewrite_context: Optional[Dict] = None,  role_context: Optional[str] = None) -> str:
    # ========== 第一步：用户主动开关（最高优先级） ==========
    # 如果用户明确说“需要联网”，直接走搜索
    if "需要联网" in user_message or "联网搜索" in user_message:
        if MEMORY_DEBUG:
            print(f"[Router] 用户主动要求联网 -> REALTIME")
        return "REALTIME"

    # === 如果改写成功，基于改写结果判断 ===
    if rewrite_context:
        status = rewrite_context.get("status")
        rewritten = rewrite_context.get("query", "")
        entity = rewrite_context.get("entity", "")

        if status == "success" and rewritten:
            # 改写后的内容有强实时关键词 → 直接 REALTIME
            strong_realtime = ["天气", "新闻", "汇率", "股价", "股票", "油价"]
            for kw in strong_realtime:
                if kw in rewritten:
                    return "REALTIME"
            
            # 改写成功且明确指出实体 → 说明已经消歧了，走 PERSONAL
            # （因为如果是需要联网的实体，上面已经匹配到了）
            if entity:
                return "PERSONAL"

        elif status == "multiple":
            # 多候选 → 需要用户确认，走 PERSONAL
            return "PERSONAL"

    # ========== 第二步：关键词快速匹配 ==========
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

    # ========== 第三步：LLM判断（仅对弱关键词或短句） ==========
    if has_weak or len(user_message) < 10:
        router_prompt = f"""
        判断用户问题是否需要联网搜索才能回答。
        
        {"【当前角色信息】" + role_context if role_context else ""}

        【规则】
        1. 需要联网（输出 REALTIME）：天气、新闻、实时数据、最新动态、推荐、查询具体信息、当前位置相关
        2. 不需要联网（输出 PERSONAL）：闲聊、个人偏好、历史事实、角色知识、生活常识、打招呼、问候

        【示例】
        问题：今天北京天气怎么样？
        输出：REALTIME

        问题：最近有什么好看的电影？
        输出：REALTIME

        问题：你好啊
        输出：PERSONAL

        问题：你喜欢吃什么？
        输出：PERSONAL

        问题：小香澄是谁？
        输出：PERSONAL（角色知识）

        问题：帮我查一下最新的 iPhone 价格
        输出：REALTIME

        【当前问题】
        {user_message}

        【输出格式】
        只输出一个单词：REALTIME 或 PERSONAL，不要加任何其他文字。
        """
        try:
            result = self.tool_adapter.chat_with_tools(
                messages=[
                    {"role": "system", "content": router_prompt},
                    {"role": "user", "content": user_message}
                ],
                tools=None
            )
            content = result.get("content", "").strip()
            # 去掉可能的前后引号或空格
            content = content.strip('"').strip()
            # 精确匹配
            if content == "REALTIME":
                return "REALTIME"
            elif content == "PERSONAL":
                return "PERSONAL"
            else:
                # 降级：如果输出不标准，根据是否有弱关键词决定
                if MEMORY_DEBUG:
                    print(f"[Router] LLM输出异常: '{content}'，降级")
                return "REALTIME" if has_weak else "PERSONAL"
        except Exception as e:
            if MEMORY_DEBUG:
                print(f"[Router] LLM判断失败: {e}")
            return "REALTIME" if has_weak else "PERSONAL"

    # ========== 第四步：默认走记忆 ==========
    return "PERSONAL"