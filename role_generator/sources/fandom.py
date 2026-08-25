"""
作品 Fandom Wiki 数据源（MediaWiki API）
已知作品主域自动匹配; 默认兜底 Bandori Fandom
"""
from .base import fetch_site_text

KNOWN_FANDOMS = {
    'bandori': 'https://bandori.fandom.com',
    'bangdream': 'https://bandori.fandom.com',
    'bocchi': 'https://bocchi-the-rock.fandom.com',
    'lonely rock': 'https://bocchi-the-rock.fandom.com',
}
DEFAULT_FANDOM = "https://bandori.fandom.com"


def _domain(work):
    w = (work or '').strip().lower()
    for k, v in KNOWN_FANDOMS.items():
        if k in w:
            return v
    return DEFAULT_FANDOM


def fetch(character, work='', timeout=20):
    """拉取作品 Fandom Wiki 角色页文本"""
    import urllib.parse
    domain = _domain(work)
    api_url = domain.rstrip('/') + '/api.php'
    page_url = domain.rstrip('/') + '/wiki/' + urllib.parse.quote(character.replace(' ', '_'))
    return fetch_site_text(
        site_key='fandom:' + domain,
        titles=character,
        api_url=api_url,
        page_url=page_url,
        timeout=timeout,
    )