"""
萌娘百科 (zh.moegirl.org.cn) 数据源 adapter
搜词条 → 抓全文+infobox+分类；搜不到用 extract 兜底
"""
import urllib.parse

from .base import fetch_site_text_full, mediawiki_search, pick_search_hit, fetch_site_text

API_URL = "https://zh.moegirl.org.cn/api.php"
PAGE_URL = "https://zh.moegirl.org.cn/{title}"


def fetch(character: str, work: str = "", timeout: int = 20):
    """先搜索命中萌娘词条，再抓全文(信息栏/分类)；失败降级 extract。返回 SourceDoc"""
    page_title = character
    try:
        hits = mediawiki_search(API_URL, (character + (" " + work if work else "")).strip(), timeout=timeout)
        hit = pick_search_hit(hits, character)
        if hit:
            page_title = hit
    except Exception:
        pass  # 搜索失败则直接按角色名抓（降级）

    page_url = PAGE_URL.format(title=urllib.parse.quote(page_title))
    doc = fetch_site_text_full(
        site_key="moegirl",
        titles=page_title,
        api_url=API_URL,
        page_url=page_url,
        timeout=timeout,
    )
    return doc
