import feedparser
from typing import List
from core.tools import search_baidu_api
from core.config import RSS_FEEDS  # 需在 config.py 新增
from .schemas import InfoItem
import uuid

def fetch_rss(user_id: str) -> List[InfoItem]:
    items = []
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                items.append(InfoItem(
                    id=f"{user_id}_{uuid.uuid4().hex[:8]}",
                    user_id=user_id,
                    title=entry.get('title', ''),
                    content=entry.get('summary', '') or entry.get('description', ''),
                    source='rss',
                    url=entry.get('link')
                ))
        except Exception as e:
            print(f"[L3] RSS 拉取失败: {feed_url} - {e}")
    return items

def fetch_search(user_id: str, keywords: List[str]) -> List[InfoItem]:
    items = []
    for kw in keywords[:3]:
        try:
            result = search_baidu_api(kw, max_results=3)
            if result and "失败" not in result:
                # 简单解析，实际可改进
                for line in result.split('\n'):
                    if line.strip():
                        items.append(InfoItem(
                            id=f"{user_id}_{uuid.uuid4().hex[:8]}",
                            user_id=user_id,
                            title=line[:50],
                            content=line,
                            source='search'
                        ))
        except Exception as e:
            print(f"[L3] 搜索失败: {kw} - {e}")
    return items