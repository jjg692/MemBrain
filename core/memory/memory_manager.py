"""
记忆管理器 - 五层记忆架构

L1: 内存上下文（按 user_id + role_id 隔离）   容量=50轮，超限压缩
L2: 短期记忆（ChromaDB type=short_term）      容量=50轮，FIFO；冷启动时加载到 L1
L3: 主动信息池（ChromaDB type=l3_info）       永久（本项目可不实现主动采集）
L4: 重要事实（ChromaDB type=fact）            永久（带衰减），LLM 自动抽取
L5: 角色事实（ChromaDB type=role_fact）       永久，仅按 role_id 隔离，启动时一次性加载

关键机制：
  - L2 仅在冷启动时加载到 L1，对话过程中不查询 L2
  - L4 由 LLM 抽取（无硬编码规则）
  - 其他记忆按 (user_id, role_id) 双键隔离；L5 仅按 role_id
"""
import json
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from core.config import (
    MEMORY_CONTEXT_MAX_ROUNDS,
    MEMORY_SHORT_TERM_MAX_ROUNDS,
    MEMORY_IMPORTANCE_THRESHOLD,
    MEMORY_FACT_DECAY_DAYS,
    MEMORY_DEBUG,
)
from core.logger import log_debug, log_error, log_info
from core.memory.vector_store import SimpleMemory
from core.adapters import LLMAdapter


class MemoryManager:
    def __init__(self, memory: SimpleMemory, tool_adapter: LLMAdapter):
        self.memory = memory
        self.tool_adapter = tool_adapter
        # L1: key = (user_id, role_id) -> list[dict{role, content}]
        self.l1_contexts: Dict[Tuple[str, str], List[dict]] = {}
        # 已冷启动加载标记
        self._cold_loaded: set = set()
        self._summarizer_prompt = (
            "把下面的对话压缩成一段简洁的中文摘要，保留关键信息（偏好、事件、承诺、人物关系）。"
            "只输出摘要本身，不要其他内容。\n\n对话:\n{history}"
        )

    # ===================== L1: 内存上下文 =====================

    def _key(self, user_id: str, role_id: str) -> Tuple[str, str]:
        return (user_id, role_id)

    def _ensure_l1(self, user_id: str, role_id: str) -> List[dict]:
        key = self._key(user_id, role_id)
        if key not in self.l1_contexts:
            self.l1_contexts[key] = []
        return self.l1_contexts[key]

    def cold_start_load(self, user_id: str, role_id: str):
        """冷启动：将 L2 短期记忆加载到 L1（对话过程中不查询 L2）"""
        key = self._key(user_id, role_id)
        if key in self._cold_loaded:
            return
        results = self.memory.get(
            where=self.memory._build_where(
                {"user_id": user_id}, {"role_id": role_id}, {"type": "short_term"}
            ),
            limit=MEMORY_SHORT_TERM_MAX_ROUNDS,
        )
        ctx = self._ensure_l1(user_id, role_id)
        for item in results["results"]:
            doc = item["document"]
            parts = doc.split("\n")
            role = "user" if "用户：" in doc else "assistant"
            content = doc
            ctx.append({"role": role, "content": content})
        self._cold_loaded.add(key)
        log_info("L1", f"冷启动加载 {len(results['results'])} 条 L2 记忆 -> L1 ({user_id}/{role_id})")

    def get_l1(self, user_id: str, role_id: str) -> List[dict]:
        return self._ensure_l1(user_id, role_id)

    def add_to_l1(self, user_id: str, role_id: str, role: str, content: str) -> List[dict]:
        ctx = self._ensure_l1(user_id, role_id)
        ctx.append({"role": role, "content": content})
        # 超限压缩（L1 容量 MEMORY_CONTEXT_MAX_ROUNDS 轮 = 2*条消息）
        if len(ctx) > MEMORY_CONTEXT_MAX_ROUNDS * 2:
            self._compress_l1(user_id, role_id)
        return ctx

    def _compress_l1(self, user_id: str, role_id: str):
        """超限时把最旧一半压缩为摘要"""
        ctx = self._ensure_l1(user_id, role_id)
        keep = MEMORY_CONTEXT_MAX_ROUNDS  # 保留最近 N 条
        old, recent = ctx[:keep], ctx[keep:]
        summary = self._summarize(old)
        self.l1_contexts[self._key(user_id, role_id)] = (
            [{"role": "system", "content": f"【对话摘要】{summary}"}] + recent
        )
        log_debug("L1", f"压缩完成: {len(ctx)} -> {keep+1} 条")

    def _summarize(self, history: List[dict]) -> str:
        text = "\n".join(
            f"{'用户' if m['role']=='user' else '助手'}: {m['content']}" for m in history
        )
        try:
            prompt = self._summarizer_prompt.format(history=text)
            return self.tool_adapter.chat([{"role": "user", "content": prompt}]).strip()
        except Exception as e:
            log_error("L1", f"摘要失败: {e}")
            return f"（共 {len(history)} 条历史消息）"

    # ===================== L2: 短期记忆 =====================

    def save_short_term(self, user_id: str, role_id: str, user_msg: str, reply: str):
        """L2 持久化一条对话（FIFO 淘汰）"""
        content = f"用户：{user_msg}\n助手：{reply}"
        self.memory.add_with_title(
            title=user_msg[:20],
            content=content,
            user_id=user_id,
            role_id=role_id,
            type_="short_term",
            meta={"rounds": 0},
        )
        # FIFO 淘汰：超过保留轮数则删除最旧
        self._fifo_prune(user_id, role_id, "short_term", MEMORY_SHORT_TERM_MAX_ROUNDS)

    def _fifo_prune(self, user_id: str, role_id: str, type_: str, max_count: int):
        results = self.memory.get(
            where=self.memory._build_where(
                {"user_id": user_id}, {"role_id": role_id}, {"type": type_}
            ),
            limit=10000,
        )
        items = results["results"]
        if len(items) <= max_count:
            return
        # 删除最旧的 N 条
        excess = len(items) - max_count
        for item in items[:excess]:
            if item["id"]:
                self.memory.delete(item["id"])

    # ===================== L4: 重要事实（LLM 抽取 + 衰减） =====================

    def judge_and_extract_facts(self, user_id: str, role_id: str, user_msg: str, reply: str):
        """判断重要性并抽取事实（模式B：L4 与情感/好感度分两阶段？此处独立性处理）"""
        importance = self._judge_importance(user_msg, reply)
        if importance < MEMORY_IMPORTANCE_THRESHOLD:
            return
        facts = self._extract_facts(user_msg, reply)
        for fact in facts:
            # 去重
            existing = self.memory.search(fact, user_id, role_id, "fact", n_results=1, threshold=0.9)
            if existing:
                continue
            self.memory.add_with_title(
                title=fact[:20],
                content=fact,
                user_id=user_id,
                role_id=role_id,
                type_="fact",
                meta={
                    "importance": importance,
                    "source": f"user:{user_msg[:30]}",
                    "created_at": datetime.now().isoformat(),
                    "last_seen": datetime.now().isoformat(),
                    "decay_days": MEMORY_FACT_DECAY_DAYS,
                },
            )
            log_debug("L4", f"抽取事实: {fact[:40]}")

    def _judge_importance(self, user_msg: str, reply: str) -> float:
        prompt = f"""判断以下对话对了解用户的长期价值（0-1），只输出数字：
用户：{user_msg}
助手：{reply}
标准：0.0-0.3 日常闲聊；0.4-0.6 有偏好但模糊；0.7-1.0 明确喜好/承诺/事件/关系。"""
        try:
            text = self.tool_adapter.chat([{"role": "user", "content": prompt}]).strip()
            m = re.search(r"(\d+(?:\.\d+)?)", text)
            if m:
                return max(0.0, min(1.0, float(m.group(1))))
        except Exception:
            pass
        return 0.3

    def _extract_facts(self, user_msg: str, reply: str) -> List[str]:
        prompt = f"""从对话中抽取关于用户的客观事实（偏好、习惯、承诺、事件、人际关系）。
只输出 JSON 数组（字符串列表），每项一个事实，不要重复，不要输出其他内容。
如果没有任何值得记住的事实，输出 []。
用户：{user_msg}
助手：{reply}"""
        try:
            text = self.tool_adapter.chat([{"role": "user", "content": prompt}]).strip()
            text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
            arr = json.loads(text)
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()]
        except Exception as e:
            log_debug("L4", f"事实抽取失败: {e}")
        return []

    def get_facts(self, user_id: str, role_id: str, n: int = 5) -> List[str]:
        results = self.memory.search("", user_id, role_id, "fact", n_results=n)
        return [r["document"] for r in results]

    def apply_fact_decay(self):
        """L4 事实衰减：超过 decay_days 未见的事实降低/标记"""
        try:
            all_facts = self.memory.collection.get(
                where={"type": "fact"}, limit=10000,
                include=["documents", "metadatas"],
            )
            now = datetime.now()
            ids = all_facts.get("ids") or []
            metas = all_facts.get("metadatas") or []
            for i in range(len(ids)):
                meta = metas[i] if i < len(metas) else {}
                created = meta.get("created_at")
                if not created:
                    continue
                try:
                    dt = datetime.fromisoformat(created)
                except Exception:
                    continue
                age = (now - dt).days
                if age > MEMORY_FACT_DECAY_DAYS:
                    new_meta = dict(meta)
                    new_meta["decayed"] = True
                    self.memory.update_meta(ids[i], new_meta)
        except Exception as e:
            log_error("L4", f"衰减处理失败: {e}")

    # ===================== L5: 角色事实（仅按 role_id） =====================

    def ensure_role_facts(self, role_id: str, prompt_text: str):
        """确保某角色的 L5 事实已加载（启动时一次性加载所有角色）"""
        existing = self.memory.collection.get(
            where=self.memory._build_where({"type": "role_fact"}, {"role_id": role_id}), limit=1
        )
        if existing and (existing.get("ids") or []):
            return  # 已加载
        facts = self._extract_role_facts(prompt_text)
        for f in facts:
            self.memory.add_with_title(
                title=f[:20],
                content=f,
                user_id="__role__",
                role_id=role_id,
                type_="role_fact",
                meta={"role_id": role_id},
            )
        log_info("L5", f"角色 {role_id} 加载 {len(facts)} 条事实")

    def _extract_role_facts(self, prompt_text: str) -> List[str]:
        prompt = f"""从下面的角色设定中，抽取客观的角色事实（性格、背景、知识、口头禅）。
只输出 JSON 数组（字符串列表），每项一个独立事实，不要输出其他内容。
角色设定：
{prompt_text}"""
        try:
            text = self.tool_adapter.chat([{"role": "user", "content": prompt}]).strip()
            text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
            arr = json.loads(text)
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()]
        except Exception as e:
            log_debug("L5", f"角色事实抽取失败: {e}")
        return []

    def get_role_facts(self, role_id: str, n: int = 20) -> List[str]:
        results = self.memory.collection.get(
            where=self.memory._build_where({"type": "role_fact"}, {"role_id": role_id}), limit=n,
            include=["documents"],
        )
        return results.get("documents") or []

    # ===================== 检索（供 Agent 使用） =====================

    def retrieve(self, user_id: str, role_id: str, query: str, top_k: int = 5) -> dict:
        """综合检索：L4 事实 + L5 角色事实（对话中不查 L2）
        L4 事实先经向量召回，再用 Cross-Encoder（ms-marco）重排序。"""
        facts_raw = self.memory.search(query, user_id, role_id, "fact", n_results=top_k * 3)
        # 用 Cross-Encoder 重排序（可用时）
        reranker = None
        try:
            from core.memory.embeddings import get_reranker
            reranker = get_reranker()
        except Exception:
            reranker = None
        if reranker is not None and len(facts_raw) > 1:
            facts_raw = reranker.rerank(query, facts_raw, top_k=top_k)
        else:
            facts_raw = facts_raw[:top_k]
        facts = [r["document"] for r in facts_raw]
        role_facts = self.get_role_facts(role_id, n=10)
        return {"facts": facts, "role_facts": role_facts}

    # ===================== 统计（后台管理） =====================

    def stats(self) -> dict:
        return {
            "total": self.memory.count(),
            "l2": self.memory.count_by_type("short_term"),
            "l4": self.memory.count_by_type("fact"),
            "l5": self.memory.count_by_type("role_fact"),
            "l3": self.memory.count_by_type("l3_info"),
            "emotion": self.memory.count_by_type("emotion"),
            "affection": self.memory.count_by_type("affection"),
        }
