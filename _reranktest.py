
# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from core.memory.embeddings import CrossEncoderReranker
import re

reranker = CrossEncoderReranker()

def scores_of(query, passages):
    items = [{"id": i, "document": p} for i, p in enumerate(passages)]
    out = reranker.rerank(query, items, top_k=None)
    return [(it["document"], round(it["rerank_score"], 3)) for it in out]

# 中文：相关 vs 不相关
print("==== 中文相关对 ====")
print(scores_of("你喜欢吃火锅吗", [
    "用户说他周末和朋友一起去吃重庆火锅，很喜欢麻辣口味。",   # 相关
    "用户今天在讨论编程项目，提到使用 FastAPI 和 LangGraph。", # 不相关
]))

print("==== 中文：强相关 vs 弱相关 ====")
print(scores_of("户山香澄喜欢猫", [
    "香澄养了一只叫小雪的白猫，最喜欢陪它玩耍。",   # 强相关
    "香澄今天是元气满满的一天。",                     # 弱相关
]))

print("==== 英文基线（对照） ====")
print(scores_of("Where can I buy LEGO bricks", [
    "LEGO sets are sold in toy stores and online shops.",
    "BanG Dream is an anime about a rock band.",
]))

print("==== 中文同 UNK 污染对照：几乎全是 UNK ====")
q = "香澄喜欢猫"
print("query tokens:")
# 直接展示两条 fact 的 logit 差距
print(scores_of(q, ["香澄养猫", "苹果很好吃"]))
