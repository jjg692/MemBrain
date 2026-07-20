import json
from datetime import datetime
from typing import List, Optional
from core.memory import SimpleMemory
from .collector import fetch_rss, fetch_search
from .filter import filter_by_llm
from .schemas import InfoItem

class L3Manager:
    def __init__(self, memory: SimpleMemory, tool_adapter):
        self.memory = memory
        self.tool_adapter = tool_adapter

    def update_for_user(self, user_id: str, user_interests: List[str]):
        """采集并存储该用户的高兴趣信息"""
        items = []
        items.extend(fetch_rss(user_id))
        # items.extend(fetch_search(user_id, user_interests))
        if not items:
            return

        high_interest = filter_by_llm(items, user_interests, self.tool_adapter)
        if not high_interest:
            return

        # 去重并存储（修复 where 条件）
        existing = self.memory.collection.get(
            where={"$and": [{"user_id": user_id}, {"type": "l3_info"}]}
        )
        existing_titles = set()
        if existing and existing["documents"]:
            for doc in existing["documents"]:
                try:
                    data = json.loads(doc)
                    existing_titles.add(data.get("title", "")[:20])
                except:
                    pass

        for item in high_interest:
            if item.title[:20] in existing_titles:
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
        """获取未分享的高分信息（修复 where 条件）"""
        results = self.memory.collection.get(
            where={"$and": [{"user_id": user_id}, {"type": "l3_info"}, {"shared": False}]},
            limit=n * 2
        )
        if not results or not results["documents"]:
            return []
        pairs = list(zip(results["documents"], results["metadatas"]))
        pairs.sort(key=lambda x: x[1].get("interest_score", 0), reverse=True)
        return [{"content": doc, "meta": meta, "id": results["ids"][i]} for i, (doc, meta) in enumerate(pairs[:n])]

    def mark_shared(self, user_id: str, doc_id: str):
        """标记已分享（使用 ids 更新，不需要 where）"""
        self.memory.collection.update(
            ids=[doc_id],
            metadatas=[{"shared": True}]
        )