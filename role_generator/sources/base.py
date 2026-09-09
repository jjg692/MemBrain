"""
角色资料检索 - 统一 MediaWiki API 拉取器
用于从萌娘百科 / Wikipedia / Fandom 等 MediaWiki 系站点抓取角色页面文本。

返回结构化 SourceDoc:
    {site, title, url, text, retrieved_at, status}

优化（对齐 CSP source_search 思路）：
1. mediawiki_search: 先"搜索→命中"词条，再抓取（角色名↔词条名不必完全一致）。
2. fetch_site_text_full: 用 action=parse 拿整页全文(wikitext/HTML→纯文本) + 分类，
   并尝试解析 infobox（信息栏），比仅 extract 摘要信息量大得多。
3. fetch_site_text: 保留旧的 extract 摘要路径（兼容/降级）。
"""
import re
import urllib.parse
import urllib.request
import json
from typing import Dict, List, Optional
from datetime import datetime, timezone


UA = "Mozilla/5.0 (MemBrain role-generator/1.0; +source-gathering)"


class SourceError(Exception):
    pass


def _fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        raise SourceError(f"fetch {url}: {e}") from e


def _json_get(url: str, timeout: int = 20) -> dict:
    body = _fetch(url, timeout)
    try:
        data = json.loads(body)
    except Exception as e:
        raise SourceError(f"json parse {url[:80]}: {e}") from e
    if not isinstance(data, dict):
        raise SourceError(f"non-object json {url[:80]}")
    return data


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="minutes")


# ===================== 搜索 → 命中 =====================

def mediawiki_search(api_url: str, query: str, limit: int = 3, timeout: int = 20) -> List[Dict]:
    """用 action=query&list=search 搜索词条，返回命中的 [{title, snippet}]。

    解决“角色名与词条名不完全一致”的问题：先搜再决定抓哪个页面。
    """
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": str(limit),
        "format": "json",
        "utf8": "1",
    }
    url = api_url + "?" + urllib.parse.urlencode(params)
    data = _json_get(url, timeout)
    hits = (data.get("query") or {}).get("search") or []
    return [{"title": h.get("title", ""), "snippet": h.get("snippet", "")} for h in hits if h.get("title")]


def pick_search_hit(hits: List[Dict], character: str) -> Optional[str]:
    """从搜索结果挑选最合适的命中标题。

    优先完全相等(去掉空格)；否则选包含角色名的；否则取第一条非空。
    返回 None 表示无可命中。
    """
    if not hits:
        return None
    base = character.replace(" ", "").strip()
    for h in hits:
        t = h.get("title", "").replace(" ", "").strip()
        if t == base:
            return h["title"]
    for h in hits:
        t = h.get("title", "")
        if base and base.lower() in t.lower():
            return h["title"]
    return hits[0]["title"]


# ===================== 抓取（extract 摘要） =====================

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
    """抓取一个站点 extract 摘要，返回 SourceDoc（兼容旧路径/降级）"""
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


# ===================== 抓取（全文 + infobox + 分类） =====================

def parse_infobox(text: str) -> str:
    """从 wikitext 里提取 infobox（信息栏/资料卡）字段。

    匹配 {{Infobox ... }} 或 {{角色信息 ... }} 等模板块，
    抽出 |key = value 行拼成一段，供蒸馏使用。
    """
    if not text:
        return ""
    # 找第一个模板块内的 key=value
    lines = []
    for m in re.finditer(r"\{\{\s*(?:Infobox|角色|Infobox character|Infobox character2)[^}]*\}\}", text, re.IGNORECASE):
        block = m.group(0)
        for kv in re.finditer(r"\|\s*([\u4e00-\u9fff\w ()（）/-]+?)\s*=\s*([^\n|]+)", block):
            k = kv.group(1).strip()
            v = kv.group(2).strip()
            # 过滤掉模板名本身与明显噪音
            if not k or k.lower() in ("infobox", "角色信息") or len(v) > 200:
                continue
            lines.append(f"{k}: {v}")
        if lines:
            break
    return "\n".join(lines)


def _strip_wikitext(text: str) -> str:
    """轻量把 wikitext 转成可读纯文本（去模板/链接/引用/HTML 注释）。"""
    t = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    t = re.sub(r"\{\{[^{}]*\}\}", "", t)          # 一层 {{...}}
    t = re.sub(r"\[\[[^\[\]]*?\|([^\[\]]*?)\]\]", r"\1", t)  # [[a|b]] -> b
    t = re.sub(r"\[\[([^\[\]]*?)\]\]", r"\1", t)  # [[a]] -> a
    t = re.sub(r"<ref[^>]*>.*?</ref>", " ", t, flags=re.DOTALL)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"'{2,}", "", t)                   # 斜体/粗体引号
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


def mediawiki_full(
    api_url: str,
    titles: str,
    timeout: int = 20,
) -> Dict:
    """用 action=parse 抓整页全文 + 分类，返回 {title, wikitext, text, categories}。

    相比 extract，能拿到 info 栏/章节/人物关系等更完整内容。
    """
    params = {
        "action": "parse",
        "page": titles,
        "prop": "wikitext|categories",
        "format": "json",
        "redirects": "1",
        "disablelimitreport": "1",
    }
    url = api_url + "?" + urllib.parse.urlencode(params)
    data = _json_get(url, timeout)
    parse = data.get("parse") or {}
    if not parse:
        return {}
    wt = ((parse.get("wikitext") or {}).get("*")) or ""
    cats = [c.get("title", "").replace("Category:", "").replace("分类:", "")
            for c in ((parse.get("categories") or []) if isinstance(parse.get("categories"), list) else [])]
    text = _strip_wikitext(wt)
    title = parse.get("title") or titles
    return {"title": title, "wikitext": wt, "text": text, "categories": cats}


def fetch_site_text_full(
    site_key: str,
    titles: str,
    api_url: str,
    page_url: str,
    timeout: int = 20,
    max_chars: int = 15000,
) -> Optional[Dict]:
    """抓取一个站点全文 + infobox + 分类，返回富化 SourceDoc。

    失败时自动回退到 fetch_site_text（extract 摘要），保证不丢源。
    """
    try:
        full = mediawiki_full(api_url, titles, timeout=timeout)
    except Exception:
        return fetch_site_text(site_key, titles, api_url, page_url, timeout=timeout)

    if not full or not (full.get("text") or "").strip():
        return fetch_site_text(site_key, titles, api_url, page_url, timeout=timeout)

    text = full["text"]
    if len(text) > max_chars:
        text = text[:max_chars]

    # 组装富文本：infobox + 分类 + 正文
    parts = []
    infobox = parse_infobox(full.get("wikitext") or "")
    if infobox:
        parts.append("【信息栏】\n" + infobox)
    cats = full.get("categories") or []
    if cats:
        parts.append("【分类】" + "、".join(cats[:20]))
    parts.append(text)
    merged = "\n\n".join(parts)

    return {
        "site": site_key,
        "title": full.get("title", titles),
        "url": page_url,
        "text": merged,
        "wikitext": full.get("wikitext", ""),
        "infobox": infobox,
        "categories": cats,
        "retrieved_at": _now(),
        "status": "ok",
    }
