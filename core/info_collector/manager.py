from typing import List, Optional
from core.memory import SimpleMemory
from .collector import fetch_rss, fetch_search
from .filter import filter_by_llm
from .schemas import InfoItem
from datetime import datetime
import json

class L3Manager:
    def __init__(self, memory: SimpleMemory, tool_adapter):
        self.memory = memory
        self.tool_adapter = tool_adapter

    def update_for_user(self, user_id: str, user_interests: List[str]):
        """采集并存储该用户的高兴趣信息"""
        # 1. 采集
        items = []
        items.extend(fetch_rss(user_id))
        items.extend(fetch_search(user_id, user_interests))
        if not items:
            return

        # 2. 过滤
        high_interest = filter_by_llm(items, user_interests, self.tool_adapter)
        if not high_interest:
            return

        # 3. 存储（不覆盖已存在）
        for item in high_interest:
            # 检查是否已存在（简单去重：标题前20字符）
            existing = self.memory.collection.get(
                where={"user_id": user_id, "type": "l3_info"}
            )
            if existing and existing["documents"]:
                if any(item.title[:20] in doc for doc in existing["documents"]):
                    continue
            self.memory.add_with_title(
                title=item.title[:20],
                content=json.dumps({
                    "title": item.title,
                    "content": item.content,
                    "source": item.source,
                    "url": item.url
                }),
                user_id=user_id,
                meta={
                    "type": "l3_info",
                    "timestamp": item.timestamp.isoformat(),
                    "expires_at": item.expires_at.isoformat(),
                    "interest_score": item.interest_score,
                    "shared": False
                }
            )

    def get_pending_shares(self, user_id: str, n: int = 2) -> List[dict]:
        """获取未分享的高分信息"""
        results = self.memory.collection.get(
            where={"user_id": user_id, "type": "l3_info", "shared": False},
            limit=n * 2
        )
        if not results or not results["documents"]:
            return []
        # 按 interest_score 排序
        pairs = list(zip(results["documents"], results["metadatas"]))
        pairs.sort(key=lambda x: x[1].get("interest_score", 0), reverse=True)
        return [{"content": doc, "meta": meta} for doc, meta in pairs[:n]]

    def mark_shared(self, user_id: str, doc_id: str):
        self.memory.collection.update(
            ids=[doc_id],
            metadatas=[{"shared": True}]
        )