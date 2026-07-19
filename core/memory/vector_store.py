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
        """添加单条记忆（带时间衰减元数据）"""
        _start = time.time()
        doc_id = f"{user_id}_{int(time.time() * 1000)}"
        now_ts = datetime.now().isoformat()
        metadatas = {
            "user_id": user_id,
            "title": title,
            "timestamp": now_ts,
            "last_access_time": now_ts,
            "access_count": 0,
            "is_fuzzy": "false"
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
    def _build_filter(self, user_id: str, role_id: Optional[str] = None, extra: Optional[Dict] = None) -> Dict:
        """构建带 role_id 隔离的过滤条件"""
        parts = [{"user_id": user_id}]
        if role_id:
            parts.append({"role_id": role_id})
        if extra:
            parts.append(extra)
        return {"$and": parts} if len(parts) > 1 else parts[0]

    def get_recent(self, user_id: str, role_id: Optional[str] = None, n: int = 3) -> List[str]:
        """获取用户最近 N 条记忆（按时间戳倒序，按 role_id 隔离）"""
        results = self.collection.get(
            where=self._build_filter(user_id, role_id, {"type": "fact"}),
            limit=n * 2
        )
        if results and results["documents"]:
            pairs = list(zip(results["documents"], results["metadatas"]))
            pairs.sort(key=lambda x: x[1].get("timestamp", ""), reverse=True)
            return [doc for doc, _ in pairs[:n]]
        return []

    def get_recent_conversations(self, user_id: str, role_id: Optional[str] = None, n: int = 10) -> List[str]:
        """获取用户最近 N 条对话记录（type=short_term，按 role_id 隔离）"""
        results = self.collection.get(
            where=self._build_filter(user_id, role_id),
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

    def get_facts(self, user_id: str, role_id: Optional[str] = None, n: int = 5) -> List[Dict]:
        """获取用户的重要事实（type=fact，按重要性倒序，按 role_id 隔离）"""
        results = self.collection.get(
            where=self._build_filter(user_id, role_id, {"type": "fact"}),
            limit=n * 2
        )
        if not results or not results["documents"]:
            return []
        ids = results.get("ids", [])
        triples = list(zip(results["documents"], results["metadatas"], ids))
        triples.sort(key=lambda x: x[1].get("importance", 0), reverse=True)
        return [
            {"document": doc, "metadata": {**meta, "id": id_}}
            for doc, meta, id_ in triples[:n]
        ]

    # ==================== 检索 ====================
    def search(self, query: str, user_id: str, role_id: Optional[str] = None,
               threshold: float = 0.5, n_results: int = 3,
               where: Optional[Dict] = None) -> Dict:
        """
        向量语义检索
        - where: 额外过滤条件，如 {"type": "short_term"}
        """
        _start = time.time()
        filter_cond = self._build_filter(user_id, role_id, where)
        
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
    def clean_old_short_term(self, user_id: str, role_id: Optional[str] = None, keep_n: int = 50) -> int:
        """
        分批删除旧短期记忆，保留最近 keep_n 轮（按 role_id 隔离）
        返回删除条数
        """
        from core.config import MEMORY_SHORT_TERM_MAX_ROUNDS
        keep_n = keep_n or MEMORY_SHORT_TERM_MAX_ROUNDS
        total_deleted = 0
        filter_cond = self._build_filter(user_id, role_id, {"type": "short_term"})
        
        while True:
            results = self.collection.get(
                where=filter_cond,
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

    
            
    # ==================== L5: 角色事实 ====================
    def get_role_facts(self, role_id: str) -> List[str]:
        """获取角色事实（type=role_fact）"""
        results = self.collection.get(
            where={"$and": [{"role_id": role_id}, {"type": "role_fact"}]}
        )
        return results["documents"] if results and results["documents"] else []

    # ==================== 时间衰减辅助 ====================
    def update_access_time(self, doc_id: str) -> None:
        """更新记忆的访问时间和访问次数"""
        now_ts = datetime.now().isoformat()
        # ChromaDB 不支持直接更新 metadata，需要先查出现有的
        results = self.collection.get(ids=[doc_id])
        if not results or not results["metadatas"]:
            return
        old_meta = results["metadatas"][0]
        new_meta = dict(old_meta)
        new_meta["last_access_time"] = now_ts
        new_meta["access_count"] = int(old_meta.get("access_count", 0)) + 1
        # 用相同 id 覆盖（ChromaDB update 方式）
        self.collection.update(
            ids=[doc_id],
            metadatas=[new_meta]
        )

    def apply_time_decay(self, results: List[Dict]) -> List[Dict]:
        """
        对检索结果应用时间衰减，计算衰减后分数
        每条结果需包含 metadata 字段
        """
        from core.config import MEMORY_HALF_LIFE_DAYS
        import math
        from datetime import datetime as dt

        half_life_days = MEMORY_HALF_LIFE_DAYS
        now = dt.now()

        scored = []
        for r in results:
            original_score = r.get("score", 0.5)
            meta = r.get("metadata", {})
            ts_str = meta.get("timestamp", "")
            if ts_str:
                try:
                    ts = dt.fromisoformat(ts_str)
                    days_diff = (now - ts).total_seconds() / 86400.0
                    decay = math.pow(0.5, days_diff / half_life_days)
                except Exception:
                    decay = 1.0
            else:
                decay = 1.0

            access_count = int(meta.get("access_count", 0))
            # 访问越频繁，衰减越慢（boost = 1 + log2(access_count+1) * 0.1）
            access_boost = 1.0 + math.log2(access_count + 1) * 0.1

            decayed_score = original_score * decay * access_boost
            r["decayed_score"] = round(decayed_score, 4)
            r["decay"] = round(decay, 4)
            scored.append(r)

        # 按衰减后分数排序
        scored.sort(key=lambda x: x["decayed_score"], reverse=True)
        return scored

    # ==================== 模糊化 ====================
    def fuzzify_old_memories(self, user_id: str, role_id: Optional[str] = None, max_days: int = 14, summary_length: int = 50) -> int:
        """
        模糊化：将超过 max_days 未被访问的记忆，替换为简短摘要（按 role_id 隔离）
        返回模糊化处理的条数
        """
        from core.config import MEMORY_FUZZY_DAYS, MEMORY_FUZZY_SUMMARY_LENGTH
        max_days = max_days or MEMORY_FUZZY_DAYS
        summary_length = summary_length or MEMORY_FUZZY_SUMMARY_LENGTH

        now = datetime.now()
        fuzzified = 0

        # 获取用户所有非角色、非模糊化的记忆
        results = self.collection.get(
            where=self._build_filter(user_id, role_id, {"is_fuzzy": "false"})
        )
        if not results or not results["ids"]:
            return 0

        for i, doc_id in enumerate(results["ids"]):
            meta = results["metadatas"][i]
            last_access = meta.get("last_access_time", meta.get("timestamp", ""))
            if not last_access:
                continue
            try:
                last_dt = datetime.fromisoformat(last_access)
                days_since = (now - last_dt).total_seconds() / 86400.0
            except Exception:
                continue

            if days_since > max_days:
                original_doc = results["documents"][i]
                # 截取前 summary_length 字符作为摘要
                if len(original_doc) > summary_length:
                    fuzzy_doc = original_doc[:summary_length] + "...（已模糊化）"
                else:
                    fuzzy_doc = original_doc + "（已模糊化）"

                new_meta = dict(meta)
                new_meta["is_fuzzy"] = "true"

                self.collection.update(
                    ids=[doc_id],
                    documents=[fuzzy_doc],
                    metadatas=[new_meta]
                )
                fuzzified += 1

        if fuzzified > 0:
            log_dbg(f"模糊化: {user_id} 处理了 {fuzzified} 条旧记忆")
        return fuzzified








