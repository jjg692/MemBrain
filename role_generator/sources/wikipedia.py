"""
中文维基百科 (zh.wikipedia.org) 数据源 adapter
搜词条 → 抓全文；搜不到用 extract 兜底（中文维基二次元角色条目常很简短）
"""
import urllib.parse

from .base import fetch_site_text_full, mediawiki_search, pick_search_hit, fetch_site_text

API_URL = "https://zh.wikipedia.org/w/api.php"
PAGE_URL = "https://zh.wikipedia.org/wiki/{title}"


def fetch(character, work='', timeout=20):
    """先搜索命中中文维基词条，再抓全文；失败降级 extract。返回 SourceDoc"""
    page_title = character
    try:
        hits = mediawiki_search(API_URL, character + ((" " + work) if work else ""), timeout=timeout)
        hit = pick_search_hit(hits, character)
        if hit:
            page_title = hit
    except Exception:
        pass

    page_url = PAGE_URL.format(title=urllib.parse.quote(page_title))
    doc = fetch_site_text_full(
        site_key='wikipedia',
        titles=page_title,
        api_url=API_URL,
        page_url=page_url,
        timeout=timeout,
    )
    return doc
