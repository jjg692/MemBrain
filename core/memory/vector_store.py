# core/memory/vector_store.py
import time
from datetime import datetime
from pathlib import Path
import chromadb
from chromadb.utils import embedding_functions

from core.config import CHROMA_DB_PATH


# ================== SimpleMemory ==================
class SimpleMemory:
    def __init__(self, path=None):
        if path is None:
            path = CHROMA_DB_PATH
        
        # 使用本地下载的 SentenceTransformer 模型
        model_path = str(Path(__file__).parent.parent.parent / "models" / "all-MiniLM-L6-v2")
        embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=model_path
        )
        
        self.client = chromadb.PersistentClient(path=path)
        # embedding_function 在创建集合时绑定
        self.collection = self.client.get_or_create_collection(
            name="memories",
            embedding_function=embedding_fn,
            metadata={"hnsw:space": "cosine"}
        )

    def add_with_title(self, title, content, user_id, meta=None):
        """
        添加带标题的记忆，嵌入由 collection 自动处理
        Args:
            title: 记忆标题
            content: 记忆内容
            user_id: 用户ID
            meta: 额外元数据字典（可选），如 {"type": "short_term", "emotion": "高兴"}
        """
        t0 = time.time()
        doc_id = f"{user_id}_{int(time.time())}"
        # 构建基础元数据
        metadatas = {
            "user_id": user_id,
            "title": title,
            "timestamp": datetime.now().isoformat()
        }
        # 合并额外元数据（如果有）
        if meta:
            metadatas.update(meta)
        self.collection.add(
            ids=[doc_id],
            documents=[content],
            metadatas=[metadatas]
        )
        print(f"[存储] ChromaDB写入耗时：{(time.time()-t0)*1000:.2f}ms")
        return {"id": doc_id, "message": "写入成功"}

    def get_recent(self, user_id, n=3):
        """获取用户最近 N 条记忆（按时间戳倒序）"""
        results = self.collection.get(
            where={"user_id": user_id},
            limit=n * 3  # 多取一点，防止不够
        )
        if results and results["documents"]:
            pairs = list(zip(results["documents"], results["metadatas"]))
            pairs.sort(key=lambda x: x[1].get("timestamp", ""), reverse=True)
            return [doc for doc, _ in pairs[:n]]
        return []

    def search(self, query, user_id, threshold=0.5, n_results=3, where=None):
        """语义检索记忆（使用 query_texts 自动嵌入），支持额外的 where 过滤"""
        filter_condition = {"user_id": user_id}
        if where:
            # ChromaDB 要求 where 条件不能有多个顶层键，所以用 $and 组合
            filter_condition = {"$and": [{"user_id": user_id}, where]}
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results,
            where=filter_condition
        )
        filtered = []
        if results["documents"] and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                score = 1 - results["distances"][0][i]
                if score >= threshold:
                    filtered.append({
                        "document": doc,
                        "score": score,
                        "timestamp": results["metadatas"][0][i].get("timestamp", "")
                    })
        return {"results": filtered}
    
    def get_recent_conversations(self, user_id, n=10):
        """获取用户最近 N 条对话记录（按时间戳倒序），只返回 type='short_term' 或 type='conversation' 的记忆"""
        # 兼容旧数据：如果元数据中没有 type 字段，我们仍然获取，但优先筛选有 type 的
        results = self.collection.get(
            where={"user_id": user_id},
            limit=n * 3  # 多取一些，防止过滤后不足
        )
        if not results or not results["documents"]:
            return []
        # 按时间戳倒序排序
        pairs = list(zip(results["documents"], results["metadatas"]))
        pairs.sort(key=lambda x: x[1].get("timestamp", ""), reverse=True)
        # 过滤出 type 为 short_term 或 conversation 的记录（兼容旧数据没有 type）
        filtered = []
        for doc, meta in pairs:
            doc_type = meta.get("type", "short_term")  # 默认视为短期记忆
            if doc_type in ("short_term", "conversation"):
                filtered.append(doc)
                if len(filtered) >= n:
                    break
        return filtered
    
    def clean_old_short_term(self, user_id, keep_n=10):
        """
        删除该用户超过 keep_n 轮的旧短期记忆，保留最新的 keep_n 轮。
        每轮对话存储为一条文档（包含用户消息+助手回复）。
        """
        # 获取该用户所有 type='short_term' 的记忆
        results = self.collection.get(
            where={"$and": [{"user_id": user_id}, {"type": "short_term"}]}
        )
        if not results or not results["ids"]:
            return
        
        # 按时间戳降序排序（最新的在前）
        pairs = list(zip(results["ids"], results["metadatas"]))
        pairs.sort(key=lambda x: x[1].get("timestamp", ""), reverse=True)
        
        # 如果总条数超过 keep_n，删除多余的旧记录
        if len(pairs) > keep_n:
            ids_to_delete = [p[0] for p in pairs[keep_n:]]
            self.collection.delete(ids=ids_to_delete)
            print(f"[清理] 删除了 {len(ids_to_delete)} 条旧短期记忆（用户 {user_id}）")