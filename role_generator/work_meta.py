"""
作品元数据层：作品名规范化 + 世界观蒸馏产物的缓存管理。

对应方案：
- 世界观蒸馏是"作品级"的，只在该作品的第一个角色第一次蒸馏时执行一次并缓存；
  之后同作品的新角色只做角色蒸馏，复用同一套世界观 —— 避免每个角色重复蒸馏世界观。
- 世界观蒸馏采用**中立权重**：输入检索与蒸馏 prompt 不偏向触发它的那个角色，
  保证世界观/关系图谱是"作品本位的"，而不是"以某某角色为中心"。
"""
import json
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional

from .sources import fetch_character_sources, to_merge_text

# 作品缓存根目录（与 distill_output 分开：作品级产物独立目录）
WORKS_DIR = Path(__file__).resolve().parent.parent / "distill_output" / "works"


# ===================== 作品名规范化 =====================

# 子串 -> (规范 key, 规范显示名)。按 key 分组，用于作品名识别。
_WORK_HINTS = [
    # 长/明确子串优先
    ("ave mujica", "ban_g_dream", "BanG Dream!"),
    ("bang dream", "ban_g_dream", "BanG Dream!"),
    ("ban g dream", "ban_g_dream", "BanG Dream!"),
    ("bangdream", "ban_g_dream", "BanG Dream!"),
    ("bandori", "ban_g_dream", "BanG Dream!"),
    ("mygo", "ban_g_dream", "BanG Dream!"),
    ("孤独摇滚", "bocchi", "孤独摇滚！"),
    ("bocchi the rock", "bocchi", "孤独摇滚！"),
    ("bocchi", "bocchi", "孤独摇滚！"),
    ("明日方舟", "arknights", "明日方舟"),
    ("arknights", "arknights", "明日方舟"),
    ("原神", "genshin", "原神"),
    ("genshin", "genshin", "原神"),
    ("赛马娘", "umamusume", "赛马娘"),
    ("umamusume", "umamusume", "赛马娘"),
    ("偶像大师", "idolmaster", "偶像大师"),
    ("idolmaster", "idolmaster", "偶像大师"),
    ("the idolm@ster", "idolmaster", "偶像大师"),
    ("love live", "lovelive", "Love Live!"),
    ("lovelive", "lovelive", "Love Live!"),
]

# 旧别名：直接映射到规范 key（英文小写下划线）
_WORK_COMPACT = {
    "bangdream": "ban_g_dream",
    "bang dream": "ban_g_dream",
    "ban dream": "ban_g_dream",
    "bandori": "ban_g_dream",
    "bang_dream": "ban_g_dream",
    "邦邦": "ban_g_dream",
}


def _norm_text(s: str) -> str:
    """统一大小写/全半角/空白。"""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "").strip().lower())


def normalize_work(work: str) -> Optional[str]:
    """把作品名规范化为统一缓存 key（英文小写下划线）。无法识别返回 None。

    识别顺序：直接别名 → 子串命中 _WORK_HINTS → 取首词分词。
    """
    if not work or not work.strip():
        return None
    w = _norm_text(work)
    if w in _WORK_COMPACT:
        return _WORK_COMPACT[w]
    for sub, key, _disp in _WORK_HINTS:
        if sub in w:
            return key
    # 兜底：取首词（英文）转 key
    m = re.search(r"[a-z0-9][\w ]*", w)
    if m:
        token = re.sub(r"[^a-z0-9_]+", "_", m.group(0)).strip("_")
        if token:
            return token[:40]
    return None


def work_display_name(work_key: str) -> str:
    """由缓存 key 反查规范显示名（用于产物可读性）。"""
    for _sub, key, disp in _WORK_HINTS:
        if key == work_key:
            return disp
    return work_key.replace("_", " ").title()


# ===================== 世界观蒸馏产物缓存 =====================

def works_dir() -> Path:
    WORKS_DIR.mkdir(parents=True, exist_ok=True)
    return WORKS_DIR


def work_cache_path(work_key: str) -> Path:
    return works_dir() / f"{work_key}.json"


def load_work(work_key: str) -> Optional[dict]:
    """读取已缓存的世界观蒸馏产物；不存在返回 None。"""
    p = work_cache_path(work_key)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_work(work_key: str, data: dict) -> Path:
    p = work_cache_path(work_key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def get_or_distill_work(adapter, work: str, force: bool = False) -> Optional[dict]:
    """获取作品世界观蒸馏产物：已有缓存则返回；否则蒸馏并缓存。

    - 这是"首角色触发"入口：同作品后续角色直接复用 cache。
    - force=True 时忽略缓存强制重蒸馏。
    """
    key = normalize_work(work)
    if not key:
        return None
    if not force:
        cached = load_work(key)
        if cached:
            return cached
    data = distill_work(adapter, work, work_key=key)
    if data:
        save_work(key, data)
    return data


# ===================== 作品检索（中立权重） =====================

def fetch_work_sources(work: str):
    """检索作品的资料源。

    中立权重关键：**不检索任何具体角色**，只检索作品名 / 乐队 / 团体词条，
    避免把触发角色带偏。返回 SourceDoc 列表。
    """
    return fetch_character_sources(work, work, timeout=20)


# ===================== 作品级蒸馏 =====================

WORK_DISTILL_SYSTEM = (
    "你是二次元作品世界观蒸馏引擎。把一部作品的世界观、团体（乐队/组织/阵营）、"
    "以及作品内`角色之间的社交关系图谱`提炼成结构化 JSON。\n"
    "**重要·中立权重**: 你不认识也不偏向任何具体触发角色——你只站在'整部作品'的高度，"
    "对所有角色一视同仁，不把任何角色写成主角或中心。\n"
    "严格按如下 JSON 输出, 不要输出其他: {\n"
    "  \"work\":\"作品标准名\",\"work_key\":\"归一key\",\"world_background\":\"世界观/背景(学校/事务所/LIVE HOUSE/年代/基调 等, 中立概括)\",\n"
    "  \"groups\":[{\"name\":\"团体/乐队名\",\"purpose\":\"定位\",\"members\":[\"成员标准名(中文)\"]}],\n"
    "  \"relationships\":[{\"from\":\"角色A\",\"to\":\"角色B\",\"relation\":\"两人关系描述\",\"familiarity\":\"亲密度(同队/熟人/同校认识/路上见过/不认识/敌对 等)\"}],\n"
    "  \"settings\":[\"出现的标志性地点/设定\"],\n"
    "  \"common_knowledge\":\"作品内角色普遍默认知道的事(如xx是学校、xx乐队很有名等)\"\n"
    "}\n"
    "规则:\n"
    "1. groups 覆盖主要团体及其成员; relationships 覆盖**所有作品内主要角色之间的社交关系**,"
    "不仅是主角的圈子——只要是同作品/同世界观的角色关系都要收录。\n"
    "2. relationship 里 from/to 用角色中文标准名（与后续角色蒸馏对齐）。\n"
    "3. familiarity 用简短标签: 同队队友 / 同校同学 / 认识但不熟 / 仅知道名字 / 不认识 / 对手 / 好友 等,"
    "让人一眼能判断'这个角色认不认识另一个'。\n"
    "4. 资料没提到的关系写'资料未提及', 不臆造。世界背景保持中立, 不偏向任何角色。\n"
)


def distill_work(adapter, work: str, work_key: Optional[str] = None) -> Optional[dict]:
    """执行作品级世界观蒸馏（中立权重）。失败返回 None。"""
    key = work_key or normalize_work(work)
    print(f"[work] 检索作品资料(中立): {work} ...", flush=True)
    docs = fetch_work_sources(work)
    for d in docs:
        print(f"    - {d.get('site','?'):20s} {d.get('status','?'):10s} {len(d.get('text') or '')} chars", flush=True)
    merged = to_merge_text(work, docs)
    prompt = (WORK_DISTILL_SYSTEM + "\n\n作品名: " + work +
              "\n作品资料:\n" + (merged or "（未检索到有效作品资料）"))
    try:
        from .distill import _parse_json
        text = adapter.chat([
            {"role": "system", "content": WORK_DISTILL_SYSTEM},
            {"role": "user", "content": prompt},
        ])
        data = _parse_json(text)
        if not isinstance(data, dict) or not data:
            print("[work] 蒸馏失败: 无有效 JSON")
            return None
        data.setdefault("work", work)
        data.setdefault("work_key", key)
        data["relationships"] = data.get("relationships") or []
        data["groups"] = data.get("groups") or []
        return data
    except Exception as e:
        print(f"[work] 蒸馏失败: {e}")
        return None
