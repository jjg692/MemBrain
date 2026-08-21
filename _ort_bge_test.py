# -*- coding: utf-8 -*-
"""用 onnxruntime 跑 BGE ONNX,并与 transformers 结果对比"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import warnings; warnings.filterwarnings('ignore')
import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer

MP = 'models/bge-reranker-v2-m3'
PROV = ['CPUExecutionProvider']
sess = ort.InferenceSession(f'{MP}/model.onnx', providers=PROV)
print('BGE ONNX loaded; providers:', sess.get_providers())

tok = AutoTokenizer.from_pretrained(MP, local_files_only=True)

def run_ort(q, p):
    enc = tok(q, p, return_tensors='np', max_length=512, truncation=True, padding=True)
    return sess.run(['logits'], {
        'input_ids': enc['input_ids'],
        'attention_mask': enc['attention_mask'],
    })[0][0][0]

cases = [
    ('你喜欢吃火锅吗', '用户说他周末和朋友一起去吃重庆火锅，很喜欢麻辣口味。'),
    ('你喜欢吃火锅吗', '用户今天在讨论编程项目，提到使用 FastAPI 和 LangGraph。'),
    ('户山香澄喜欢猫', '香澄养了一只叫小雪的白猫，最喜欢陪它玩耍。'),
    ('户山香澄喜欢猫', '香澄今天是元气满满的一天。'),
    ('LEGO bricks are fun', 'LEGO sets are sold in toy stores and online shops.'),
]
for q, p in cases:
    print(f'{q!r} <-> {p[:22]!r}: {run_ort(q, p):.4f}')
