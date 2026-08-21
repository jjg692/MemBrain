# -*- coding: utf-8 -*-
"""手动导出 bge-reranker-v2-m3 为 ONNX（动态 batch/seq）"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import warnings; warnings.filterwarnings('ignore')
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer
from pathlib import Path

SRC = 'models/bge-reranker-v2-m3'
DST = 'models/bge-reranker-v2-m3/model.onnx'
SEQ = 512

print('[1] loading model...')
model = AutoModelForSequenceClassification.from_pretrained(SRC, local_files_only=True, torch_dtype=torch.float32)
model.eval()
tok = AutoTokenizer.from_pretrained(SRC, local_files_only=True)

dummy = tok(
    ['你喜欢吃火锅吗'] * 2,
    ['用户说他周末和朋友一起去吃重庆火锅，很喜欢麻辣口味。'] * 2,
    return_tensors='pt', max_length=SEQ, truncation=True, padding=True,
)

print('[2] exporting with torch.onnx.export ...')
with torch.no_grad():
    torch.onnx.export(
        model,
        (dummy['input_ids'], dummy['attention_mask']),
        DST,
        input_names=['input_ids', 'attention_mask'],
        output_names=['logits'],
        dynamic_axes={
            'input_ids': {0: 'batch', 1: 'sequence'},
            'attention_mask': {0: 'batch', 1: 'sequence'},
            'logits': {0: 'batch'},
        },
        opset_version=14,
        do_constant_folding=True,
    )
print('[3] exported ->', DST, Path(DST).stat().st_size, 'bytes')
