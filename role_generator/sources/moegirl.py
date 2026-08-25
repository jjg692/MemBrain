"""
萌娘百科 (zh.moegirl.org.cn) 数据源 adapter
"""
from .base import fetch_site_text

API_URL = "https://zh.moegirl.org.cn/api.php"
# 萌娘百科角色词条通常叫 "<角色名>"
PAGE_URL = "https://zh.moegirl.org.cn/{title}"


def fetch(character: str, work: str = "", timeout: int = 20):
    """拉取萌娘百科角色页文本；返回 SourceDoc"""
    import urllib.parse
    page_title = character
    page_url = PAGE_URL.format(title=urllib.parse.quote(character))
    return fetch_site_text(
        site_key="moegirl",
        titles=page_title,
        api_url=API_URL,
        page_url=page_url,
        timeout=timeout,
    )
