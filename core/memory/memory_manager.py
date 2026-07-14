# core/memory/memory_manager.py 迁移记忆存储/摘要逻辑
import threading
import json
import re
from datetime import datetime

from core.adapters import LLMAdapter
from core.logger import log_debug, log_store


class MemoryManager:
    """记忆管理：存储、加载、摘要生成"""
    
    def __init__(self, memory, tool_adapter: LLMAdapter):
        self.memory = memory
        self.tool_adapter = tool_adapter

    def save_memory(self, user_id: str, user_message: str, reply: str, query_type: str = None):
        """
        后台存储记忆（优化版）
        1. 存储短期记忆：原始对话，带轮次和类型标记
        2. 异步生成长期记忆摘要（如果开启）
        """
        try:
            # ========== 存储短期记忆（原始对话） ==========
            # 每条对话作为独立条目，带 type="short_term" 和轮次
            # 使用当前时间戳作为排序依据
            short_term_doc = f"用户说：{user_message}\n助手回复：{reply}"
            short_term_meta = {
                "user_id": user_id,
                "type": "short_term",
                "timestamp": datetime.now().isoformat(),
                "query_type": query_type or "UNKNOWN",
            }
            add_result = self.memory.add_with_title(
                title=f"{user_message[:15]}...",
                content=short_term_doc,
                user_id=user_id,
                meta=short_term_meta
            )
            log_store(f"短期记忆存储完成：{add_result}")

            # ========== 异步清理旧短期记忆（保留最近10轮） ==========
            threading.Thread(
                target=self.memory.clean_old_short_term,
                args=(user_id, 10),
                daemon=True
            ).start()

            # ========== 异步生成长期记忆摘要 ==========
            threading.Thread(
                target=self._generate_long_term_memory,
                args=(user_id, user_message, reply, query_type),
                daemon=True
            ).start()

        except Exception as e:
            log_store(f"存储失败：{e}")

    def _generate_long_term_memory(self, user_id: str, user_message: str, reply: str, query_type: str = None):
        """
        后台生成长期记忆摘要（提炼核心信息）
        使用工具模型（tool_adapter）生成摘要和情绪标签，然后存储到向量库，type="long_term"
        """
        try:
            summary_prompt = f"""
            请根据以下对话，提炼出核心信息摘要（一句话）并判断情绪标签。

            对话：
            用户：{user_message}
            助手：{reply}

            输出格式（JSON）：
            {{"summary": "摘要内容", "emotion": "高兴/生气/难过/惊讶/平静/其他"}}
            """
            result = self.tool_adapter.chat_with_tools(
                messages=[{"role": "system", "content": summary_prompt}],
                tools=None
            )
            content = result.get("content", "")
            # 尝试解析 JSON
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                summary = data.get("summary", "")
                emotion = data.get("emotion", "平静")
            else:
                # 降级：直接截取前30字
                summary = content[:50] + ("..." if len(content) > 50 else "")
                emotion = "平静"

            # 存储长期记忆
            long_term_doc = f"【摘要】{summary}（情绪：{emotion}）"
            long_term_meta = {
                "user_id": user_id,
                "type": "long_term",
                "timestamp": datetime.now().isoformat(),
                "emotion": emotion,
                "query_type": query_type or "UNKNOWN",
                "summary": summary,
            }
            add_result = self.memory.add_with_title(
                title=summary[:15] + "...",
                content=long_term_doc,
                user_id=user_id,
                meta=long_term_meta
            )
            log_store(f"长期记忆存储完成：{add_result}")
        except Exception as e:
            log_store(f"长期记忆生成失败：{e}")