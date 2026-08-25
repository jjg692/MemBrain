"""
角色资料检索 - 统一 MediaWiki API 拉取器
用于从萌娘百科 / Wikipedia / Fandom 等 MediaWiki 系站点抓取角色页面文本。

返回结构化 SourceDoc:
    {site, title, url, text, retrieved_at, status}
"""
import urllib.parse
import urllib.request
import json
from typing import Dict, List, Optional
from datetime import datetime, timezone


UA = "Mozilla/5.0 (MemBrain role-generator/1.0)"


class SourceError(Exception):
    pass


def _fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        raise SourceError(f"fetch {url}: {e}") from e


def mediawiki_extract(
    api_url: str,
    titles: str,
    prop: str = "extracts",
    explaintext: str = "1",
    exlimit: str = "1",
    redirects: str = "1",
    timeout: int = 20,
) -> Dict:
    """调用 MediaWiki action=query&prop=extracts，返回首个页面的 JSON 页对象"""
    params = {
        "action": "query",
        "titles": titles,
        "prop": prop,
        "explaintext": explaintext,
        "exlimit": exlimit,
        "redirects": redirects,
        "format": "json",
    }
    url = api_url + "?" + urllib.parse.urlencode(params)
    body = _fetch(url, timeout)
    try:
        data = json.loads(body)
    except Exception as e:
        raise SourceError(f"json parse {url[:80]}: {e}") from e
    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return {}
    # 取第一个（排除 -1 missing）
    for page in pages.values():
        if page.get("missing"):
            continue
        return page
    return {}


def fetch_site_text(
    site_key: str,
    titles: str,
    api_url: str,
    page_url: str,
    timeout: int = 20,
) -> Optional[Dict]:
    """抓取一个站点并返回 SourceDoc（任意 MediaWiki 站点）"""
    try:
        page = mediawiki_extract(api_url, titles, timeout=timeout)
    except SourceError as e:
        return {"site": site_key, "title": titles, "url": page_url,
                "text": "", "retrieved_at": _now(), "status": f"error: {e}"}
    if not page or "extract" not in page:
        return {"site": site_key, "title": titles, "url": page_url,
                "text": "", "retrieved_at": _now(), "status": "missing"}
    return {
        "site": site_key,
        "title": page.get("title", titles),
        "url": page_url,
        "text": page.get("extract", ""),
        "retrieved_at": _now(),
        "status": "ok",
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="minutes")
