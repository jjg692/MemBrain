"""
向量存储 SimpleMemory（ChromaDB 封装，单例）
包含 ChromaDB 多条件查询规范：
  - 所有多键 where 必须用 $and 包裹
  - 统一通过 _build_where() 生成合法的 where 条件
"""
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings

from core.config import CHROMA_DB_PATH
from core.logger import log_info, log_error

# 嵌入函数单例（避免重复初始化）
from core.memory.embeddings import get_embedding_function


class SimpleMemory:
    """ChromaDB 封装，按 (user_id, role_id, type) 组织记忆"""

    def __init__(self, path: Optional[str] = None):
        self.path = path or CHROMA_DB_PATH
        Path(self.path).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=self.path,
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self._get_or_create_collection()
        log_info("Memory", f"ChromaDB 已就绪: {self.path}")

    def _get_or_create_collection(self):
        name = "membrain_memory"
        try:
            return self._client.get_or_create_collection(
                name=name,
                embedding_function=get_embedding_function(),
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as e:
            log_error("Memory", f"创建集合失败: {e}")
            return self._client.create_collection(name=name)

    # ===================== ChromaDB 多条件查询规范 =====================

    @staticmethod
    def _build_where(*conditions: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        统一构建合法的 where 条件。
        多个顶层键必须用 $and 包裹，否则 ChromaDB 报
        'Expected where to have exactly one operator'。
        """
        conds = [c for c in conditions if c]
        if not conds:
            return None
        if len(conds) == 1:
            return conds[0]
        return {"$and": conds}

    # ===================== CRUD =====================

    def _meta(self, base: dict) -> dict:
        """补充元数据公共字段（字符串化时间戳用于排序）"""
        base = dict(base)
        base.setdefault("timestamp", datetime.now().isoformat())
        base["_ts"] = time.time()
        return base

    def add_with_title(
        self,
        title: str,
        content: str,
        user_id: str,
        role_id: str,
        type_: str,
        meta: Optional[dict] = None,
        doc_id: Optional[str] = None,
    ):
        """新增一条记忆"""
        m = self._meta(meta or {})
        m.update({
            "user_id": user_id,
            "role_id": role_id,
            "type": type_,
            "title": title,
        })
        doc_id = doc_id or f"{type_}_{user_id}_{role_id}_{int(time.time()*1000)}"
        self.collection.upsert(
            ids=[doc_id],
            documents=[content],
            metadatas=[m],
        )
        return doc_id

    def delete(self, doc_id: str):
        try:
            self.collection.delete(ids=[doc_id])
        except Exception:
            pass

    def update_meta(self, doc_id: str, meta: dict):
        try:
            self.collection.update(ids=[doc_id], metadatas=[meta])
        except Exception as e:
            log_error("Memory", f"更新元数据失败: {e}")

    def get(self, where: Optional[dict] = None, limit: int = 100, include_docs: bool = True):
        """按 where 条件查询原始记录（FIFO 排序）"""
        try:
            result = self.collection.get(
                where=where,
                limit=limit,
                include=["documents", "metadatas"],
            )
        except Exception as e:
            log_error("Memory", f"查询失败: {e}")
            return {"results": []}

        items = []
        docs = result.get("documents") or []
        metas = result.get("metadatas") or []
        ids = result.get("ids") or []
        for i, doc in enumerate(docs):
            meta = metas[i] if i < len(metas) else {}
            items.append({
                "id": ids[i] if i < len(ids) else "",
                "document": doc,
                "metadata": meta,
            })
        # 按 _ts 升序（FIFO）
        items.sort(key=lambda x: x["metadata"].get("_ts", 0))
        return {"results": items}

    def search(
        self,
        query: str,
        user_id: str,
        role_id: str,
        type_: str,
        n_results: int = 5,
        threshold: float = 0.0,
    ) -> List[dict]:
        """向量检索指定 (user, role, type) 下的记忆"""
        where = self._build_where(
            {"user_id": user_id},
            {"role_id": role_id},
            {"type": type_},
        )
        if not query.strip():
            # 无 query 时退回按序获取
            res = self.get(where=where, limit=n_results)
            return res["results"]
        try:
            result = self.collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            log_error("Memory", f"向量检索失败: {e}")
            return []
        items = []
        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]
        for i in range(len(ids)):
            distance = dists[i] if i < len(dists) else 0.0
            score = 1.0 - distance  # cosine
            if score < threshold:
                continue
            items.append({
                "id": ids[i],
                "document": docs[i],
                "metadata": metas[i] if i < len(metas) else {},
                "score": round(score, 4),
            })
        return items

    # ===================== 便捷方法 =====================

    def count(self, where: Optional[dict] = None) -> int:
        """统计满足 where 条件的数量（count(where=) 在此版本不可靠，用 get 计数）"""
        if where is None:
            try:
                return self.collection.count()
            except Exception:
                return 0
        try:
            # 分页取全部 ids（最多 1 万条）
            offset = 0
            total = 0
            while True:
                res = self.collection.get(where=where, limit=1000, offset=offset, include=[])
                ids = res.get("ids") or []
                total += len(ids)
                if len(ids) < 1000:
                    break
                offset += 1000
            return total
        except Exception:
            return 0

    def count_by_type(self, type_: str) -> int:
        return self.count(where={"type": type_})

    def raw_collection(self):
        return self.collection


# ===================== 单例 =====================
_memory_instance: Optional[SimpleMemory] = None
_memory_lock = threading.Lock()


def get_memory() -> SimpleMemory:
    global _memory_instance
    if _memory_instance is None:
        with _memory_lock:
            if _memory_instance is None:
                _memory_instance = SimpleMemory()
    return _memory_instance
