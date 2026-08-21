# -*- coding: utf-8 -*-
"""用纯 tokenizers 库加载 bge-m3 的 tokenizer.json, 对比 transformers 编码结果"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from tokenizers import Tokenizer
from transformers import AutoTokenizer

MP = 'models/bge-reranker-v2-m3'
# 纯 tokenizers 方式
tk = Tokenizer.from_file(f'{MP}/tokenizer.json')
print('tokenizers Tokenizer loaded;',
      'pad', tk.token_to_id('[PAD]'),
      'bos<s>', tk.token_to_id('<s>'),
      'eos</s>', tk.token_to_id('</s>'))

# transformers 参考
ref = AutoTokenizer.from_pretrained(MP, local_files_only=True)
enc_ref = ref('你喜欢吃火锅吗', '用户说他周末和朋友一起去吃重庆火锅', return_tensors='np',
              max_length=512, truncation=True, padding=True)
print('ref ids  :', enc_ref['input_ids'][0].tolist())
print('ref mask :', enc_ref['attention_mask'][0].tolist())

# tokenizers 方式: 需要自己加 padding
import numpy as np
def tok_encode(q, p):
    e = tk.encode(q, p)
    return list(e.ids), list(e.attention_mask)
ids, mask = tok_encode('你喜欢吃火锅吗', '用户说他周末和朋友一起去吃重庆火锅')
print('tk input_ids:', ids)
print('tk mask     :', mask)
print('match:', ids == enc_ref['input_ids'][0].tolist() and [1]*len(ids) == enc_ref['attention_mask'][0].tolist())
