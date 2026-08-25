"""
L3 主动信息池：外部信息采集 + 主动推送

- L3Collector：周期性采集外部实时信息源（复用 search_web），去重后入库 ChromaDB type=l3_info
- L3Pusher:周期性扫描未推送的 L3 条目，调用 Agent.proactive_message 生成主动消息，
           并通过 WebSocket 私聊通道推送给在线用户
"""
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

from core.config import (
    L3_UPDATE_INTERVAL,
    L3_PUSH_INTERVAL,
    L3_KEYWORDS,
    L3_MAX_ITEMS,
    L3_ENABLED,
)
from core.logger import log_info, log_error
from core.memory.vector_store import SimpleMemory
from core.adapters import LLMAdapter
from core.tools import search_web


class L3Collector:
    """外部信息采集器：周期性拉取给定关键词的实时信息，存为 L3 条目"""

    def __init__(self, memory: SimpleMemory, adapter: LLMAdapter, keywords: Optional[List[str]] = None):
        self.memory = memory
        self.adapter = adapter
        self.keywords = [k.strip() for k in (keywords or L3_KEYWORDS) if k.strip()]
        self._lock = threading.Lock()

    def collect_once(self) -> int:
        """执行一轮采集，返回新增条目数"""
        if not self.keywords:
            return 0
        new_count = 0
        for kw in self.keywords:
            try:
                content = search_web(kw)
                if not content or "搜索服务不可用" in content or "降级" in content:
                    # 免费/降级结果也可以存，但跳过明显失败
                    pass
                self._store_item(kw, content)
                new_count += 1
            except Exception as e:
                log_error("L3", f"采集[{kw}]失败: {e}")
        return new_count

    def _store_item(self, keyword: str, content: str):
        """入库一条 L3 信息（带去重：同 keyword 近 N 分钟不重复存）"""
        now = time.time()
        # 简单去重：查最近同类条目，若内容已存在则跳过
        where = self.memory._build_where(
            {"type": "l3_info"}, {"keyword": keyword}
        )
        existing = self.memory.get(where=where, limit=3)
        for it in existing["results"]:
            meta = it.get("metadata", {})
            if meta.get("content_hash") == _hash(content):
                return  # 重复内容
        doc_id = f"l3_{keyword}_{int(now*1000)}"
        try:
            self.memory.add_with_title(
                title=f"L3_{keyword}",
                content=content,
                user_id="__system__",
                role_id=keyword,
                type_="l3_info",
                meta={
                    "keyword": keyword,
                    "collected_at": datetime.now().isoformat(),
                    "content_hash": _hash(content),
                    "pushed": False,
                    "_ts": now,
                },
                doc_id=doc_id,
            )
        except Exception as e:
            # 暂时性失败(如嵌入服务暂时不可用),记录但不中断循环
            log_error("L3", f"入库失败[{keyword}]: {e}")

    def start_loop(self, stop_event: threading.Event):
        """后台线程：按 L3_UPDATE_INTERVAL 周期采集"""
        log_info("L3", f"采集线程启动，间隔 {L3_UPDATE_INTERVAL}s")
        while not stop_event.is_set():
            start = time.time()
            try:
                n = self.collect_once()
                if n:
                    log_info("L3", f"本轮采集新增 {n} 条")
            except Exception as e:
                log_error("L3", f"采集循环异常: {e}")
            stop_event.wait(max(10, L3_UPDATE_INTERVAL - (time.time() - start)))


class L3Pusher:
    """推送器：扫描未推送的 L3 条目，让 Agent 主动开口并推送给在线用户"""

    def __init__(self, memory: SimpleMemory, agent_factory, push_callback=None):
        self.memory = memory
        self.agent_factory = agent_factory
        # push_callback: callable(user_id, data_dict)  用于 WS 推送; None 表示只记 L1
        self.push_callback = push_callback
        self._lock = threading.Lock()
        # 记录已推送过的 user_id,避免每次全量
        self._pushed_keywords: set = set()

    def push_once(self) -> int:
        """扫描未推送 L3 条目, 对每个当前在线用户尝试主动消息, 返回推送条数"""
        where = self.memory._build_where({"type": "l3_info"}, {"pushed": False})
        items = self.memory.get(where=where, limit=10)["results"]
        if not items:
            return 0
        # 用户列表：私聊在线用户（从 ws_manager 取）
        from api.websocket_manager import single_ws_manager
        if hasattr(single_ws_manager, "user_ids"):
            users = list(single_ws_manager.user_ids())
        else:
            users = list(single_ws_manager.get_all())
        if not users:
            # 无在线用户, 暂不推送, 稍后再试
            return 0
        pushed = 0
        for item in items:
            meta = item.get("metadata", {})
            kw = meta.get("keyword", "")
            if kw in self._pushed_keywords:
                continue
            content = item.get("document", "")
            for uid in users:
                # 取该用户的默认角色 agent（私聊）
                agent = self.agent_factory.get_agent(uid, _default_role(self.agent_factory.initializer))
                text = agent.proactive_message(uid, trigger=f"外部信息池有新的分享", context=content)
                if text:
                    if self.push_callback:
                        data = {"type": "proactive", "role_id": agent.role_id,
                                "content": text, "trigger": "l3", "keyword": kw}
                        try:
                            self.push_callback(uid, data)
                        except Exception as e:
                            log_error("L3", f"推送失败 {uid}: {e}")
                    pushed += 1
            # 标记已推送
            self._pushed_keywords.add(kw)
            self.memory.update_meta(item["id"], {**meta, "pushed": True})
        return pushed

    def start_loop(self, stop_event: threading.Event):
        log_info("L3", f"推送线程启动，间隔 {L3_PUSH_INTERVAL}s")
        while not stop_event.is_set():
            start = time.time()
            try:
                n = self.push_once()
                if n:
                    log_info("L3", f"本轮推送 {n} 条")
            except Exception as e:
                log_error("L3", f"推送循环异常: {e}")
            stop_event.wait(max(10, L3_PUSH_INTERVAL - (time.time() - start)))


def _default_role(initializer):
    return initializer.role_manager.get_default_role() or "kasumi"


def _hash(text: str) -> str:
    import hashlib
    return hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()[:12]
