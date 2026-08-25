"""
中文维基百科 (zh.wikipedia.org) 数据源 adapter
"""
from .base import fetch_site_text

API_URL = "https://zh.wikipedia.org/w/api.php"
PAGE_URL = "https://zh.wikipedia.org/wiki/{title}"


def fetch(character, work='', timeout=20):
    """拉取中文维基角色页文本, 返回 SourceDoc"""
    import urllib.parse
    page_url = PAGE_URL.replace('{title}', urllib.parse.quote(character))
    return fetch_site_text(
        site_key='wikipedia',
        titles=character,
        api_url=API_URL,
        page_url=page_url,
        timeout=timeout,
    )