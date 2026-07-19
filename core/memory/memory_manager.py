"""
记忆管理器 - 五层记忆架构（无 L3）
- L1: 内存上下文（由 graph.py 管理）
- L2: 短期记忆（向量库，混合检索）
- L4: 重要事实（向量库，type=fact）
- L5: 角色记忆（静态）
"""
import threading
import json
import re
import time
from datetime import datetime
from typing import Optional, List, Dict

from core.adapters import LLMAdapter
from core.config import (
    MEMORY_SHORT_TERM_MAX_ROUNDS,
    MEMORY_IMPORTANCE_THRESHOLD,
    MEMORY_DEBUG
)
from core.logger import log_store, log_debug


def log_dbg(msg: str):
    if MEMORY_DEBUG:
        print(f"[MemoryManager] {msg}")


class MemoryManager:
    def __init__(self, memory, tool_adapter: LLMAdapter, main_adapter: Optional[LLMAdapter] = None):
        self.memory = memory
        self.tool_adapter = tool_adapter
        self.main_adapter = main_adapter or tool_adapter
        
        # 延迟初始化混合检索器
        self._retriever = None
    
    @property
    def retriever(self):
        """懒加载混合检索器"""
        if self._retriever is None:
            from core.memory.retriever import HybridRetriever
            self._retriever = HybridRetriever(self.memory)
        return self._retriever

    # ==================== 重要性判断（规则版） ====================
    def judge_importance(self, user_msg: str, assistant_msg: str) -> float:
        """
        用 LLM 判断对话重要性（0-1）
        - 0.0-0.3: 日常闲聊
        - 0.4-0.6: 涉及个人偏好但表述不明确
        - 0.7-0.9: 明确表达喜好、承诺、事件、人际关系
        - 1.0: 极其重要（重大事件、情感宣泄、明确承诺）
        """
        prompt = f"""判断以下对话是否值得长期记住（作为用户的重要事实）：

        用户：{user_msg}
        助手：{assistant_msg}

        评分标准：
        - 0.0-0.3: 日常闲聊，不包含任何用户个人信息
        - 0.4-0.6: 涉及用户偏好或习惯，但表述不够明确或强度不足
        - 0.7-0.9: 明确表达喜好、厌恶、承诺、计划、重要事件、人际关系
        - 1.0: 极其重要（重大事件、强烈情感表达、明确约定）

        只输出一个数字（0.0-1.0），不要其他内容。"""
        
        try:
            result = self.tool_adapter.chat_with_tools(
                messages = [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": "请根据上述标准输出一个数字分数。"}],
                tools=None
            )
            content = result.get("content", "").strip()
            import re
            match = re.search(r'(\d+\.?\d*)', content)
            if match:
                score = float(match.group(1))
                return min(max(score, 0.0), 1.0)
            return 0.3
        except Exception as e:
            print(f"[重要性判断] LLM 调用失败: {e}")
            return 0.3

    # ==================== L1: 上下文压缩 ====================
    def compress_context(self, rounds: List[Dict]) -> str:
        """
        压缩多轮对话为摘要（用于 L1 上下文压缩）
        返回压缩后的摘要文本
        """
        if not rounds:
            return ""
        try:
            text = "\n".join([
                f"{r.get('role', 'unknown')}: {r.get('content', '')}"
                for r in rounds if r.get('role') != 'system'
            ])
            if not text.strip():
                return ""
            
            prompt = f"请用一句话总结以下对话的核心内容：\n{text}"
            result = self.tool_adapter.chat_with_tools(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text}
                ],
                tools=None
            )
            summary = result.get("content", "").strip()
            if not summary:
                summary = text[:50] + "..."
            log_dbg(f"上下文压缩: {summary[:50]}...")
            return summary
        except Exception as e:
            log_dbg(f"压缩失败: {e}")
            return text[:50] + "..." if text else ""

    # ==================== L2 + L4 + L5综合检索 ====================
    def retrieve_memory_context(self, user_id: str, query: str, top_k: int = 5, role_id: Optional[str] = None) -> Dict:
        """
        综合检索（L5 角色事实 + L4 事实 + L2 短期记忆），按 role_id 隔离
        返回: {"facts": [...], "short_term": [...], "context": "合并后的文本"}
        """
        # === L5: 角色事实（优先级最高） ===
        role_fact_texts = []
        if role_id:
            role_facts = self.memory.get_role_facts(role_id)
            role_fact_texts = role_facts
            log_dbg(f"L5 角色事实: {len(role_fact_texts)} 条")

        # === L4: 按 role_id 过滤拉取事实 ===
        # 1. 常驻注入：重要性最高的 2 条事实（无论查询）
        important_facts = self.memory.get_facts(user_id, role_id, n=2)
        important_texts = [f["document"] for f in important_facts]

        # 2. 语义检索 L4 事实（与查询相关）
        l4_results = self.memory.search(
            query=query,
            user_id=user_id,
            role_id=role_id,
            threshold=0.5,
            n_results=3,
            where={"type": "l4_fact"}
        )
        l4_texts = [f["document"] for f in l4_results.get("results", [])]

        # 合并去重
        fact_texts = list(dict.fromkeys(important_texts + l4_texts))
        
        # === L2: 混合检索短期记忆（按 role_id 过滤） ===
        short_term_results = self.retriever.search(
            query=query,
            user_id=user_id,
            role_id=role_id,
            top_k=top_k
        )
        short_term_texts = [r["document"] for r in short_term_results]
        
        # === 更新被检索到的记忆的访问时间 ===
        for r in short_term_results:
            doc_id = r.get("id", "")
            if doc_id:
                try:
                    self.memory.update_access_time(doc_id)
                except Exception:
                    pass  # 更新失败不影响主流程
        
        # === 合并上下文（事实优先） ===
        all_texts = fact_texts + short_term_texts
        context = "\n".join([f"- {t}" for t in all_texts]) if all_texts else ""
        
        log_dbg(f"检索完成: 事实 {len(fact_texts)} 条, 短期 {len(short_term_texts)} 条")
        return {
            "facts": fact_texts,
            "short_term": short_term_texts,
            "role_facts": role_fact_texts,
            "context": context,
            "raw": short_term_results
            }

    # ==================== 定期模糊化 ====================
    def trigger_fuzzify(self, user_id: str, role_id: Optional[str] = None) -> int:
        """触发用户记忆的模糊化（按 role_id 隔离），返回处理条数"""
        try:
            return self.memory.fuzzify_old_memories(user_id, role_id=role_id)
        except Exception as e:
            log_dbg(f"模糊化失败: {e}")
            return 0


    # ==================== 存储入口 ====================
    def save_memory(self, user_id: str, role_id: str, user_message: str, reply: str,
                    query_type: str = None, importance: float = 0.3) -> None:
        """
        异步存储记忆（入口方法）
        - 存 L2 短期记忆
        - 判断重要性，触发 L4 事实抽取
        """
        try:
            # === L2: 存储短期记忆 ===
            doc = f"用户说：{user_message}\n助手回复：{reply}"
            self.memory.add_with_title(
                title=user_message[:15] + "...",
                content=doc,
                user_id=user_id,
                meta={
                    "type": "short_term",
                    "role_id": role_id,
                    "timestamp": datetime.now().isoformat(),
                    "query_type": query_type or "UNKNOWN",
                    "importance": importance
                }
            )
            log_store(f"短期记忆存储完成")

            # === L2: 异步清理旧短期记忆（保留50轮） ===
            threading.Thread(
                target=self.memory.clean_old_short_term,
                args=(user_id, role_id, MEMORY_SHORT_TERM_MAX_ROUNDS),
                daemon=True
            ).start()

            # === L4: 判断是否需要抽取事实 ===
            if importance >= MEMORY_IMPORTANCE_THRESHOLD:
                threading.Thread(
                    target=self._extract_and_save_facts,
                    args=(user_id, role_id, user_message, reply),
                    daemon=True
                ).start()

        except Exception as e:
            log_store(f"存储失败: {e}")

     # ==================== L4: 事实抽取 ====================
    def _extract_and_save_facts(self, user_id: str, role_id: str, user_msg: str, assistant_msg: str) -> None:
        """异步抽取事实并存储（L4）"""
        try:
            from core.memory.fact_extractor import extract_facts
            facts = extract_facts(user_msg, assistant_msg, self.tool_adapter)
            
            for fact in facts:
                # ========== 冲突仲裁 ==========
                try:
                    # 获取该用户+该角色已有事实
                    existing_facts = self.memory.get_facts(user_id, role_id=role_id, n=20)
                    
                    from core.memory.arbitrator import FactArbitrator
                    arbitrator = FactArbitrator(self.memory, self.tool_adapter)
                    result = arbitrator.arbitrate(fact, existing_facts)
                    
                    action = result.get("action", "store")
                    
                    if action == "skip":
                        log_dbg(f"仲裁跳过存储: {fact['fact']} | {result.get('reason', '')}")
                        continue
                        
                    if action == "replace":
                        # 找到冲突的事实 ID 并删除
                        conflict_text = result.get("conflict_with", "")
                        if conflict_text:
                            for ef in existing_facts:
                                if ef["document"] == conflict_text:
                                    ef_id = ef.get("metadata", {}).get("id", "")
                                    if ef_id:
                                        self.memory.delete_by_ids([ef_id])
                                        log_dbg(f"仲裁替换: 删除旧事实 [{ef_id}] {conflict_text}")
                                    break
                    
                    if action == "merge":
                        # merge 也是存新事实，旧事实保留（后续检索时会同时命中）
                        log_dbg(f"仲裁合并: 保留旧事实 + 存储新事实")
                    
                except Exception as e:
                    log_dbg(f"仲裁器异常，直接存储: {e}")
                # =================================

                self.memory.add_with_title(
                    title=fact["fact"][:20],
                    content=fact["fact"],
                    user_id=user_id,
                    meta={
                        "type": "fact",
                        "role_id": role_id,
                        "category": fact.get("category", "general"),
                        "importance": MEMORY_IMPORTANCE_THRESHOLD,
                        "timestamp": datetime.now().isoformat(),
                        "source": user_msg[:50]
                    }
                )
                log_dbg(f"事实存储: {fact['fact']}")
        except Exception as e:
            import traceback
            log_dbg(f"事实抽取失败: {e}")
            log_dbg(f"traceback: {traceback.format_exc()[:500]}")