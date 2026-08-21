
# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from tokenizers import Tokenizer
from tokenizers.models import WordPiece
from tokenizers.normalizers import BertNormalizer
from tokenizers.pre_tokenizers import BertPreTokenizer
from tokenizers.processors import TemplateProcessing

lines = open('models/ms-marco-MiniLM-L-6-v2/vocab.txt', encoding='utf-8').read().splitlines()
vocab = {t.strip(): i for i, t in enumerate(lines)}
tok = Tokenizer(WordPiece(vocab=vocab, unk_token='[UNK]'))
tok.normalizer = BertNormalizer(lowercase=True, strip_accents=True, clean_text=True, handle_chinese_chars=True)
tok.pre_tokenizer = BertPreTokenizer()
post = TemplateProcessing(
    single='[CLS] $A [SEP]',
    pair='[CLS] $A [SEP] $B [SEP]',
    special_tokens=[('[CLS]', vocab['[CLS]']), ('[SEP]', vocab['[SEP]'])],
)
tok.post_processor = post
tok.enable_truncation(max_length=512)
tok.enable_padding(pad_id=0, pad_token='[PAD]')

for a, b in [
    ('你喜欢吃火锅吗', ''),
    ('我喜欢吃火锅和烧烤', '我喜欢吃火锅和烧烤'),
    ('LEGO bricks are fun', 'LEGO bricks'),
    ('天气怎么样今天', '今天天气如何'),
    ('香澄喜欢猫', '猫很可爱'),
]:
    pair = b if b else ' '
    enc = tok.encode(a, pair)
    print(repr(a), '//', repr(b), '->', list(enc.tokens))
    print('   ids sample:', enc.ids[:14])
