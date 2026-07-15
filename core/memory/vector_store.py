"""
向量数据库封装 - ChromaDB 单例模式
支持：增删改查、分批清理、调试日志
"""
import time
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

import chromadb
from chromadb.utils import embedding_functions

from core.config import CHROMA_DB_PATH, MEMORY_DEBUG


def log_dbg(msg: str):
    if MEMORY_DEBUG:
        print(f"[VectorStore] {msg}")


class SimpleMemory:
    """ChromaDB 单例封装，全局共享 embedding 模型"""
    
    _instance: Optional['SimpleMemory'] = None
    _embedding_fn = None
    _initialized = False

    def __new__(cls, path: Optional[str] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, path: Optional[str] = None):
        if self._initialized:
            return
        
        _start = time.time()
        if path is None:
            path = CHROMA_DB_PATH
        
        # 单例加载 embedding 模型（只加载一次）
        if SimpleMemory._embedding_fn is None:
            model_path = str(Path(__file__).parent.parent.parent / "models" / "all-MiniLM-L6-v2")
            log_dbg(f"加载 embedding 模型: {model_path}")
            SimpleMemory._embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=model_path
            )
        
        self.client = chromadb.PersistentClient(path=path)
        self.collection = self.client.get_or_create_collection(
            name="memories",
            embedding_function=SimpleMemory._embedding_fn,
            metadata={"hnsw:space": "cosine"}
        )
        self._initialized = True
        log_dbg(f"初始化完成，耗时: {(time.time()-_start)*1000:.2f}ms")

    # ==================== 写入 ====================
    def add_with_title(self, title: str, content: str, user_id: str, meta: Optional[Dict] = None) -> Dict:
        """添加单条记忆"""
        _start = time.time()
        doc_id = f"{user_id}_{int(time.time() * 1000)}"
        metadatas = {
            "user_id": user_id,
            "title": title,
            "timestamp": datetime.now().isoformat()
        }
        if meta:
            metadatas.update(meta)
        
        self.collection.add(
            ids=[doc_id],
            documents=[content],
            metadatas=[metadatas]
        )
        log_dbg(f"添加记忆: {doc_id} -> {content[:30]}... 耗时: {(time.time()-_start)*1000:.2f}ms")
        return {"id": doc_id, "message": "写入成功"}

    def add_batch(self, items: List[Dict]) -> None:
        """批量添加记忆，items = [{"id": str, "document": str, "metadata": dict}]"""
        if not items:
            return
        _start = time.time()
        ids = [it["id"] for it in items]
        docs = [it["document"] for it in items]
        metas = [it.get("metadata", {}) for it in items]
        self.collection.add(ids=ids, documents=docs, metadatas=metas)
        log_dbg(f"批量添加 {len(items)} 条，耗时: {(time.time()-_start)*1000:.2f}ms")

    # ==================== 读取 ====================
    def get_recent(self, user_id: str, n: int = 3) -> List[str]:
        """获取用户最近 N 条记忆（按时间戳倒序）"""
        results = self.collection.get(
            where={"$and": [{"user_id": user_id}, {"type": "fact"}]},
            limit=n * 2
        )
        if results and results["documents"]:
            pairs = list(zip(results["documents"], results["metadatas"]))
            pairs.sort(key=lambda x: x[1].get("timestamp", ""), reverse=True)
            return [doc for doc, _ in pairs[:n]]
        return []

    def get_recent_conversations(self, user_id: str, n: int = 10) -> List[str]:
        """获取用户最近 N 条对话记录（type=short_term）"""
        results = self.collection.get(
            where={"user_id": user_id},
            limit=n * 3
        )
        if not results or not results["documents"]:
            return []
        pairs = list(zip(results["documents"], results["metadatas"]))
        pairs.sort(key=lambda x: x[1].get("timestamp", ""), reverse=True)
        filtered = []
        for doc, meta in pairs:
            doc_type = meta.get("type", "short_term")
            if doc_type in ("short_term", "conversation"):
                filtered.append(doc)
                if len(filtered) >= n:
                    break
        return filtered

    def get_facts(self, user_id: str, n: int = 5) -> List[Dict]:
        """获取用户的重要事实（type=fact，按重要性倒序）"""
        results = self.collection.get(
            where={"$and": [{"user_id": user_id}, {"type": "fact"}]},
            limit=n * 2
        )
        if not results or not results["documents"]:
            return []
        pairs = list(zip(results["documents"], results["metadatas"]))
        pairs.sort(key=lambda x: x[1].get("importance", 0), reverse=True)
        return [
            {"document": doc, "metadata": meta}
            for doc, meta in pairs[:n]
        ]

    # ==================== 检索 ====================
    def search(self, query: str, user_id: str, threshold: float = 0.5,
               n_results: int = 3, where: Optional[Dict] = None) -> Dict:
        """
        向量语义检索
        - where: 额外过滤条件，如 {"type": "short_term"}
        """
        _start = time.time()
        filter_parts = [{"user_id": user_id}]
        if where:
            filter_parts.append(where)
        if len(filter_parts) == 1:
            filter_cond = filter_parts[0]
        else:
            filter_cond = {"$and": filter_parts}
        
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results,
            where=filter_cond
        )
        
        filtered = []
        if results["documents"] and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                score = 1 - results["distances"][0][i]
                if score >= threshold:
                    filtered.append({
                        "document": doc,
                        "score": score,
                        "metadata": results["metadatas"][0][i],
                        "id": results["ids"][0][i]
                    })
        log_dbg(f"向量检索: '{query[:20]}...' 命中 {len(filtered)} 条, 耗时: {(time.time()-_start)*1000:.2f}ms")
        return {"results": filtered}

    # ==================== 清理 ====================
    def clean_old_short_term(self, user_id: str, keep_n: int = 50) -> int:
        """
        分批删除旧短期记忆，保留最近 keep_n 轮
        返回删除条数
        """
        from core.config import MEMORY_SHORT_TERM_MAX_ROUNDS
        keep_n = keep_n or MEMORY_SHORT_TERM_MAX_ROUNDS
        total_deleted = 0
        
        while True:
            results = self.collection.get(
                where={"$and": [{"user_id": user_id}, {"type": "short_term"}]},
                limit=100
            )
            if not results or not results["ids"]:
                break
            
            pairs = list(zip(results["ids"], results["metadatas"]))
            pairs.sort(key=lambda x: x[1].get("timestamp", ""), reverse=True)
            
            if len(pairs) <= keep_n:
                break
            
            ids_to_delete = [p[0] for p in pairs[keep_n:]]
            self.collection.delete(ids=ids_to_delete)
            total_deleted += len(ids_to_delete)
            log_dbg(f"清理 {user_id}: 删除了 {len(ids_to_delete)} 条（本轮剩余 {len(pairs)} 条）")
            
            if len(pairs) <= keep_n + 100:
                break
        
        if total_deleted > 0:
            log_dbg(f"清理完成 {user_id}: 共删除 {total_deleted} 条旧短期记忆")
        return total_deleted

    def delete_by_ids(self, ids: List[str]) -> None:
        """按 ID 删除"""
        if ids:
            self.collection.delete(ids=ids)
            log_dbg(f"删除 {len(ids)} 条")