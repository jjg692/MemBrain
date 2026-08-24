"""
嵌入与重排序工具

1. OllamaEmbeddingFunction - 调用 Ollama nomic-embed-text 生成向量（离线，只要 Ollama 在跑）
2. BgeRerankerV2M3         - 用本地 ONNX BAAI/bge-reranker-v2-m3 对检索结果重排序（中文友好，完全离线）
3. CrossEncoderReranker    - 旧实现：本地 ONNX ms-marco-MiniLM-L-6-v2（英文场景兜底）
"""
import re
import threading
import traceback
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

from core.config import (
    EMBEDDING_MODE,
    EMBEDDING_MODEL_DIR,
    EMBEDDING_MODEL_NAME,
    OLLAMA_HOST,
    OLLAMA_EMBED_MODEL,
    RERANKER_BACKEND,
    BGE_RERANKER_DIR,
    BGE_RERANKER_ONNX,
    CROSS_ENCODER_ONNX_PATH,
    ENABLE_RERANK,
)
from core.logger import log_info, log_error

# ===================== 嵌入函数单例 =====================
_embedding_fn = None
_embedding_lock = threading.Lock()


class OllamaEmbeddingFunction:
    """通过 Ollama (nomic-embed-text) 生成嵌入向量，兼容 ChromaDB EmbeddingFunction 接口"""

    def __init__(self, model: str = "nomic-embed-text", host: Optional[str] = None):
        import ollama
        from ollama import Client as OllamaClient
        self.model = model
        # host 来自配置（默认 localhost）
        self._client = OllamaClient(host=host) if host else ollama

    def name(self) -> str:
        """ChromaDB 要求实现 name()，否则 get_or_create_collection 校验会报错"""
        return f"ollama-{self.model}"

    def _embed_texts(self, texts) -> List[List[float]]:
        """底层嵌入：texts 为字符串列表，逐条调用 Ollama，返回二维向量列表"""
        embeddings = []
        for t in texts:
            try:
                resp = self._client.embeddings(model=self.model, prompt=str(t))
                embedding = resp.get("embedding")
                if embedding is None and isinstance(resp.get("embeddings"), list):
                    embedding = resp["embeddings"][0]
                embeddings.append(list(embedding) if embedding else [0.0] * 384)
            except Exception as e:
                log_error("Embedding", f"Ollama 嵌入失败: {e}")
                embeddings.append([0.0] * 384)
        return embeddings

    def embed_query(self, input=None, query=None) -> List[List[float]]:
        """ChromaDB query 时调用，输入 input=list[str] 或 query=str。
        返回二维 Embeddings（含一个向量），与 __call__ 契约一致。
        """
        if query is not None:
            texts = [query] if isinstance(query, str) else list(query)
        elif input is not None:
            texts = [input] if isinstance(input, str) else list(input)
        else:
            texts = [""]
        return self._embed_texts(texts)

    def __call__(self, input: Sequence[str]) -> List[List[float]]:
        """ChromaDB 调用接口：输入文本列表，输出向量列表（二维）"""
        # 允许传入单个字符串或字符串列表
        if isinstance(input, str):
            input = [input]
        texts = list(input)
        return self._embed_texts(texts)


def _local_sentence_transformer() -> Optional[object]:
    """（备选）本地 sentence-transformers 嵌入（需 PyTorch 模型文件）"""
    try:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
        model_path = Path(EMBEDDING_MODEL_DIR)
        if model_path.exists():
            return SentenceTransformerEmbeddingFunction(model_name=str(model_path), device="cpu")
        return SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL_NAME, device="cpu")
    except Exception as e:
        log_error("Embedding", f"本地 sentence-transformers 不可用: {e}")
        return None


def get_embedding_function():
    """根据 EMBEDDING_MODE 返回嵌入函数（单例）"""
    global _embedding_fn
    if _embedding_fn is not None:
        return _embedding_fn
    with _embedding_lock:
        if _embedding_fn is not None:
            return _embedding_fn
        if EMBEDDING_MODE == "local":
            fn = _local_sentence_transformer()
            if fn is not None:
                _embedding_fn = fn
                log_info("Embedding", "使用本地 sentence-transformers 嵌入")
                return _embedding_fn
            log_info("Embedding", "本地嵌入不可用，回退 Ollama")
        # 默认 / 回退：Ollama
        try:
            _embedding_fn = OllamaEmbeddingFunction(model=OLLAMA_EMBED_MODEL, host=OLLAMA_HOST)
            log_info("Embedding", f"使用 Ollama 嵌入: {OLLAMA_EMBED_MODEL}")
        except Exception as e:
            log_error("Embedding", f"Ollama 嵌入初始化失败: {e}")
            try:
                from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
                _embedding_fn = DefaultEmbeddingFunction()
            except Exception:
                raise
    return _embedding_fn


# ===================== BGE 重排序（bge-reranker-v2-m3） =====================

class BgeRerankerV2M3:
    """
    基于 BAAI/bge-reranker-v2-m3（ONNX + 多语 XLMRoberta）的 Cross-Encoder 重排器。
    - 中文/多语言召回精排显著优于旧 ms-marco（旧模型词表基本不含中文，中文会退化成 [UNK]）
    - 完全离线：onnxruntime 推理 + tokenizers（tokenizer.json，sentencepiece BPE）
    """

    def __init__(self, onnx_path: Optional[str] = None):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        if onnx_path:
            self._path = Path(onnx_path)
        else:
            self._path = Path(BGE_RERANKER_DIR) / BGE_RERANKER_ONNX
        tokenizer_path = self._path.parent / "tokenizer.json"
        if not self._path.exists():
            raise FileNotFoundError(f"BGE ONNX 重排序模型不存在: {self._path}")
        if not tokenizer_path.exists():
            raise FileNotFoundError(f"BGE tokenizer.json 不存在: {tokenizer_path}")

        self._session = ort.InferenceSession(str(self._path), providers=["CPUExecutionProvider"])
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._tokenizer.enable_truncation(max_length=512)
        self._pad_id = self._tokenizer.token_to_id("<pad>") or 1

        log_info("Rerank", f"BGE 重排器已加载: {self._path.name}")

    def _encode_pairs(self, query: str, passages):
        batch_ids, batch_mask = [], []
        for p in passages:
            enc = self._tokenizer.encode(str(query), str(p))
            batch_ids.append(list(enc.ids))
            batch_mask.append(list(enc.attention_mask))
        max_len = max((len(x) for x in batch_ids), default=0)
        for ids, mask in zip(batch_ids, batch_mask):
            if len(ids) < max_len:
                ids.extend([self._pad_id] * (max_len - len(ids)))
                mask.extend([0] * (max_len - len(mask)))
        return np.array(batch_ids, dtype=np.int64), np.array(batch_mask, dtype=np.int64)

    def score(self, query: str, passages) -> List[float]:
        if not passages:
            return []
        input_ids, attention_mask = self._encode_pairs(query, passages)
        logits = self._session.run(
            ["logits"], {"input_ids": input_ids, "attention_mask": attention_mask}
        )[0]
        return [float(x[0]) for x in logits]

    def rerank(self, query: str, items: List[dict], top_k: Optional[int] = None) -> List[dict]:
        if not items:
            return items
        passages = [it["document"] for it in items]
        try:
            scores = self.score(query, passages)
        except Exception as e:
            log_error("Rerank", f"BGE 重排序失败，保持原序: {e}")
            traceback.print_exc()
            return items
        for it, s in zip(items, scores):
            it["rerank_score"] = round(float(s), 6)
        items = sorted(items, key=lambda x: x.get("rerank_score", 0), reverse=True)
        if top_k:
            items = items[:top_k]
        return items


# ===================== Cross-Encoder 重排序 =====================
# 单例
_reranker = None
_reranker_lock = threading.Lock()


class CrossEncoderReranker:
    """
    用本地 ONNX 的 ms-marco-MiniLM-L-6-v2 做重排序（BertForSequenceClassification）。
    完全离线：onnxruntime 推理 + tokenizers WordPiece（vocab.txt）。
    """

    def __init__(self, onnx_path: Optional[str] = None):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        from tokenizers.models import WordPiece
        from tokenizers.normalizers import BertNormalizer
        from tokenizers.pre_tokenizers import BertPreTokenizer
        from tokenizers.processors import TemplateProcessing

        self.onnx_path = onnx_path or CROSS_ENCODER_ONNX_PATH
        self._path = Path(self.onnx_path)
        vocab_path = self._path.parent / "vocab.txt"
        if not self._path.exists():
            raise FileNotFoundError(f"ONNX 重排序模型不存在: {self._path}")
        if not vocab_path.exists():
            raise FileNotFoundError(f"vocab.txt 不存在: {vocab_path}")

        self._session = ort.InferenceSession(str(self._path), providers=["CPUExecutionProvider"])
        # WordPiece tokenizer（镜像 transformers.BertTokenizer，uncased）
        tokens = vocab_path.read_text(encoding="utf-8").splitlines()
        vocab = {tok.strip(): idx for idx, tok in enumerate(tokens)}
        self._tokenizer = Tokenizer(WordPiece(vocab=vocab, unk_token="[UNK]"))
        # normalizer / pre_tokenizer / post_processor 都挂在 Tokenizer 上
        self._tokenizer.normalizer = BertNormalizer(
            lowercase=True, strip_accents=True, clean_text=True, handle_chinese_chars=True
        )
        self._tokenizer.pre_tokenizer = BertPreTokenizer()
        cls_id = vocab["[CLS]"] if "[CLS]" in vocab else 101
        sep_id = vocab["[SEP]"] if "[SEP]" in vocab else 102
        self._tokenizer.post_processor = TemplateProcessing(
            single="[CLS] $A [SEP]",
            pair="[CLS] $A [SEP] $B [SEP]",
            special_tokens=[("[CLS]", cls_id), ("[SEP]", sep_id)],
        )
        self._tokenizer.enable_truncation(max_length=512)
        self._tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")

        log_info("Rerank", f"Cross-Encoder 已加载: {self._path.name}")

    def _tokenize_pair(self, query: str, passage: str):
        """BERT 式 tokenize pair（使用 post_processor 自动加 [CLS]/[SEP]）"""
        enc = self._tokenizer.encode(query, passage)
        ids = list(enc.ids)
        mask = list(enc.attention_mask)
        type_ids = list(enc.type_ids)
        return ids, mask, type_ids

    def score(self, query: str, passages: List[str]) -> List[float]:
        """对 query 与多个 passage 打分，返回分数列表（越大越相关）"""
        if not passages:
            return []
        batch_ids, batch_mask, batch_type = [], [], []
        for p in passages:
            ids, mask, ttype = self._tokenize_pair(query, p)
            batch_ids.append(ids)
            batch_mask.append(mask)
            batch_type.append(ttype)
        # 统一 batch 长度
        max_len = max(len(x) for x in batch_ids)
        for x in (batch_ids, batch_mask, batch_type):
            for row in x:
                pad = max_len - len(row)
                if pad > 0:
                    row.extend([0] * pad)
        # 统一 batch 首个序列长度
        for row in batch_type:
            # 保持 type_ids 长度与 ids 一致
            if len(row) < max_len:
                row.extend([0] * (max_len - len(row)))
        feeds = {
            "input_ids": np.array(batch_ids, dtype=np.int64),
            "attention_mask": np.array(batch_mask, dtype=np.int64),
            "token_type_ids": np.array(batch_type, dtype=np.int64),
        }
        logits = self._session.run(["logits"], feeds)[0]
        return [float(x[0]) for x in logits]

    def rerank(self, query: str, items: List[dict], top_k: Optional[int] = None) -> List[dict]:
        """对检索结果 items 重排序，返回排序后的列表（含 rerank_score）"""
        if not items:
            return items
        passages = [it["document"] for it in items]
        try:
            scores = self.score(query, passages)
        except Exception as e:
            log_error("Rerank", f"重排序失败，保持原序: {e}")
            traceback.print_exc()
            return items
        for it, s in zip(items, scores):
            it["rerank_score"] = round(float(s), 6)
        items = sorted(items, key=lambda x: x.get("rerank_score", 0), reverse=True)
        if top_k:
            items = items[:top_k]
        return items


def _build_reranker():
    """按 RERANKER_BACKEND 构造重排器；若指定后端失败则自动回退到另一个后端"""
    backend = (RERANKER_BACKEND or "").strip().lower()
    # bge（默认，中文友好）-> minilm（旧）
    order = ["bge", "minilm"] if backend in ("", "bge", "auto") else [backend, "minilm", "bge"]
    last_err = None
    for name in order:
        try:
            if name == "bge":
                return BgeRerankerV2M3()
            if name == "minilm":
                return CrossEncoderReranker()
        except Exception as e:  # noqa: PERF203
            last_err = e
            log_error("Rerank", f"{name} 重排器加载失败: {e}")
    raise RuntimeError(f"所有重排器均无法加载: {last_err}")


def get_reranker():
    """返回全局重排序器（懒加载单例）；未启用或加载失败返回 None"""
    global _reranker
    if not ENABLE_RERANK:
        return None
    if _reranker is not None:
        return _reranker
    with _reranker_lock:
        if _reranker is not None:
            return _reranker
        try:
            _reranker = _build_reranker()
        except Exception as e:
            log_error("Rerank", f"重排序器加载失败（本功能禁用）: {e}")
            _reranker = None
    return _reranker
