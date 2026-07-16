"""
混合检索器：向量 + BM25 + Cross-Encoder 精排
"""
import re
import time
from typing import List, Dict, Optional
import numpy as np

from core.config import MEMORY_DEBUG, CROSS_ENCODER_MODEL


def log_dbg(msg: str):
    if MEMORY_DEBUG:
        print(f"[Retriever] {msg}")


class HybridRetriever:
    """向量 + BM25 + Cross-Encoder 三阶段检索"""

    def __init__(self, memory, cross_encoder_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        self.memory = memory
        self.cross_encoder_model = cross_encoder_model or CROSS_ENCODER_MODEL
        self._bm25_index = None
        self._corpus = []
        self._corpus_ids = []
        self._corpus_metas = []
        self._cross_encoder = None
        self._user_id_cache = None
        

    def _lazy_load_cross_encoder(self):
        """懒加载 Cross-Encoder（首次检索时才加载）"""
        if self._cross_encoder is None:
            try:
                from sentence_transformers import CrossEncoder
                log_dbg(f"加载 Cross-Encoder: {self.cross_encoder_model}")
                self._cross_encoder = CrossEncoder(self.cross_encoder_model)
            except Exception as e:
                log_dbg(f"Cross-Encoder 加载失败: {e}")
                self._cross_encoder = None

    def _rebuild_bm25_index(self, user_id: str):
        """重建 BM25 索引（按用户隔离）"""
        _start = time.time()
        """重建 BM25 索引（只索引短期记忆，不包括事实）"""
        all_docs = self.memory.collection.get(
            where={"$and": [{"user_id": user_id}, {"type": "short_term"}]}
        )
        if all_docs and all_docs["documents"]:
            self._corpus = all_docs["documents"]
            self._corpus_ids = all_docs["ids"]
            self._corpus_metas = all_docs["metadatas"]
            tokenized_corpus = [self._tokenize(doc) for doc in self._corpus]
            try:
                from rank_bm25 import BM25Okapi
                self._bm25_index = BM25Okapi(tokenized_corpus)
            except ImportError:
                log_dbg("rank_bm25 未安装，BM25 检索将跳过")
                self._bm25_index = None
        else:
            self._corpus = []
            self._corpus_ids = []
            self._corpus_metas = []
            self._bm25_index = None
        log_dbg(f"BM25 索引重建完成，{len(self._corpus)} 条，耗时: {(time.time()-_start)*1000:.2f}ms")

    def _tokenize(self, text: str) -> List[str]:
        """简单分词（中文 + 英文）"""
        text = re.sub(r'[^\w\u4e00-\u9fff]', ' ', text)
        return [w.lower() for w in text.split() if len(w) > 0]

    def search(self, query: str, user_id: str, top_k: int = 5) -> List[Dict]:
        print(f"[DEBUG] retriever.search 被调用，query={query}")
        """
        三阶段检索：
        1. 向量检索（语义）
        2. BM25 检索（关键词）
        3. Cross-Encoder 精排
        """
        _start = time.time()
        log_dbg(f"检索开始: '{query[:30]}...'")

        # === 检查是否需要重建 BM25 索引 ===
        if self._user_id_cache != user_id:
            self._rebuild_bm25_index(user_id)
            self._user_id_cache = user_id

        # === 阶段1: 向量检索 ===
        vec_results = self.memory.search(
            query=query,
            user_id=user_id,
            threshold=0.3,  # 阈值放低，召回更多
            n_results=top_k * 4
        ).get("results", [])

        # === 阶段2: BM25 关键词检索 ===
        bm25_results = []
        if self._bm25_index and self._corpus:
            tokenized_query = self._tokenize(query)
            scores = self._bm25_index.get_scores(tokenized_query)
            top_indices = np.argsort(scores)[-top_k * 2:][::-1]
            for idx in top_indices:
                if scores[idx] > 0:
                    bm25_results.append({
                        "document": self._corpus[idx],
                        "score": float(scores[idx]),
                        "id": self._corpus_ids[idx],
                        "metadata": self._corpus_metas[idx]
                    })

        # === 合并去重（按 document 内容） ===
        merged = {}
        for r in vec_results:
            key = r["document"]
            merged[key] = {
                "document": key,
                "vec_score": r["score"],
                "bm25_score": 0.0,
                "metadata": r.get("metadata", {})
            }
        for r in bm25_results:
            key = r["document"]
            if key in merged:
                merged[key]["bm25_score"] = r["score"]
            else:
                merged[key] = {
                    "document": key,
                    "vec_score": 0.0,
                    "bm25_score": r["score"],
                    "metadata": r.get("metadata", {})
                }

        candidates = list(merged.values())
        log_dbg(f"合并去重后: {len(candidates)} 条候选")

        # === 阶段3: Cross-Encoder 精排 ===
        if len(candidates) > 1:
            self._lazy_load_cross_encoder()
            if self._cross_encoder is not None:
                pairs = [[query, c["document"]] for c in candidates]
                scores = self._cross_encoder.predict(pairs)
                for i, c in enumerate(candidates):
                    c["rerank_score"] = float(scores[i])
                candidates.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
                log_dbg(f"Cross-Encoder 精排完成")
            else:
                # 降级：混合分数排序
                candidates.sort(key=lambda x: x["vec_score"] * 0.6 + x["bm25_score"] * 0.4, reverse=True)
        else:
            # 只有一个候选，直接返回
            pass

        results = candidates[:top_k]
        log_dbg(f"检索完成: 返回 {len(results)} 条，耗时: {(time.time()-_start)*1000:.2f}ms")
        return results