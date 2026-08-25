"""
角色资料检索 - 多源编排: 并行抓取萌娘百科/Wikipedia/Fandom
"""
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from . import moegirl, wikipedia, fandom

# 站点等级（参考 CSP）: 高=官方/百科主站, 中=作品 Wiki
SOURCE_RANK = {
    'moegirl': 'high',
    'wikipedia': 'high',
    'fandom': 'medium',
}


def _rank_of(site: str) -> str:
    key = site.split(':')[0].lower()
    return SOURCE_RANK.get(key, 'low')


def fetch_character_sources(character: str, work: str = '', timeout: int = 20) -> List[Dict]:
    """并行抓取多个来源, 返回带 rank 标注的 SourceDoc 列表"""
    adapters = [moegirl.fetch, wikipedia.fetch, fandom.fetch]
    names = ['moegirl', 'wikipedia', 'fandom']
    results: List[Optional[Dict]] = [None] * len(adapters)

    def run(i, fn):
        try:
            results[i] = fn(character, work, timeout=timeout)
        except Exception as e:
            results[i] = {'site': names[i], 'status': f'error: {e}', 'text': '', 'url': ''}

    with ThreadPoolExecutor(max_workers=len(adapters)) as ex:
        for i, fn in enumerate(adapters):
            ex.submit(run, i, fn)

    docs = []
    for i, d in enumerate(results):
        if not d:
            continue
        d = dict(d)
        d.setdefault('rank', _rank_of(d.get('site', names[i])))
        docs.append(d)
    return docs


def to_merge_text(character: str, docs: List[Dict], max_chars_per_source: int = 3000) -> str:
    """把多来源合并成一段供 LLM 蒸馏的文本（带站点等级标注）"""
    blocks = ['# 角色: ' + character + ' 资料汇总']
    for d in docs:
        site = d.get('site', '?')
        rank = d.get('rank', '?')
        status = d.get('status', '?')
        text = (d.get('text') or '').strip()
        if status != 'ok' or not text:
            blocks.append('\n## 来源: ' + site + ' (rank=' + rank + ', status=' + status + ')\n无有效内容')
            continue
        if len(text) > max_chars_per_source:
            text = text[:max_chars_per_source]
        blocks.append('\n## 来源: ' + site + ' (rank=' + rank + ', retrieved_at=' + str(d.get('retrieved_at', '')) + ')\n' + text)
    return '\n'.join(blocks)