"""
作品 Fandom Wiki 数据源（MediaWiki API）
按作品名匹配已知 Fandom 域；未收录时如实降级（不落错源）
"""
import urllib.parse

from .base import (
    fetch_site_text_full, mediawiki_search, pick_search_hit, fetch_site_text,
)

# 常见作品 → 其 Fandom/Wiki 域名。key 用小写子串匹配作品名。
KNOWN_FANDOMS = {
    # BanG Dream!
    'bang dream': 'https://bandori.fandom.com',
    'bangdream': 'https://bandori.fandom.com',
    'bandori': 'https://bandori.fandom.com',
    'ban dream': 'https://bandori.fandom.com',
    # BanG Dream! MyGO / Ave Mujica 也在 bandori
    'mygo': 'https://bandori.fandom.com',
    'ave mujica': 'https://bandori.fandom.com',
    # 孤独摇滚
    'bocchi': 'https://bocchi-the-rock.fandom.com',
    'lonely rock': 'https://bocchi-the-rock.fandom.com',
    '孤独摇滚': 'https://bocchi-the-rock.fandom.com',
    # 明日方舟
    'arknights': 'https://arknights.fandom.com',
    '明日方舟': 'https://arknights.fandom.com',
    # 原神
    'genshin': 'https://genshin-impact.fandom.com',
    '原神': 'https://genshin-impact.fandom.com',
    # Love Live!
    'love live': 'https://love-live.fandom.com',
    'lovelive': 'https://love-live.fandom.com',
    # 偶像大师
    'idolmaster': 'https://idolmaster.fandom.com',
    '偶像大师': 'https://idolmaster.fandom.com',
    'the idolm@ster': 'https://idolmaster.fandom.com',
    # 赛马娘
    'umamusume': 'https://umamusume.fandom.com',
    '赛马娘': 'https://umamusume.fandom.com',
    # 少女革命
    'revolutionary girl utena': 'https://shoujokakumei.fandom.com',
}


def _domain_for_work(work: str) -> str:
    """按作品名子串匹配已知 Fandom 域；无匹配返回 ''（表示未收录，不硬猜）"""
    w = (work or '').strip().lower()
    if not w:
        return ''
    # 优先最长子串匹配（避免 'live' 命中 'love live' 之类）
    best = ''
    best_len = 0
    for k, v in KNOWN_FANDOMS.items():
        if k in w and len(k) > best_len:
            best = v
            best_len = len(k)
    return best


def fetch(character, work='', timeout=20):
    """拉取作品 Fandom Wiki 角色页全文。

    - 能按作品匹配到已知 Fandom 域 → 搜索命中再抓全文
    - 匹配不到 → 返回 status=unknown_fandom（不错误落回 Bandori，遵循"不伪造"）
    """
    domain = _domain_for_work(work)
    if not domain:
        return {
            'site': 'fandom',
            'title': character,
            'url': '',
            'text': '',
            'retrieved_at': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(timespec='minutes'),
            'status': 'unknown_fandom（未收录该作品的 Fandom 域，跳过，不硬猜）',
        }

    api_url = domain.rstrip('/') + '/api.php'
    page_title = character
    try:
        hits = mediawiki_search(api_url, character + ((" " + work) if work else ""), timeout=timeout)
        hit = pick_search_hit(hits, character)
        if hit:
            page_title = hit
    except Exception:
        pass  # 搜索失败则按角色名直接抓

    page_url = domain.rstrip('/') + '/wiki/' + urllib.parse.quote(page_title.replace(' ', '_'))
    doc = fetch_site_text_full(
        site_key='fandom:' + domain,
        titles=page_title,
        api_url=api_url,
        page_url=page_url,
        timeout=timeout,
    )
    return doc
