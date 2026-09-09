"""
角色关系图谱消费层：
- 从世界观蒸馏产物(universe)里，抽出与某个具体角色相关的关系子集
- 生成"人物关系"提示词区块（可注入角色 prompt）
- 生成"群聊成员身份映射"文本（多人群聊用，靠说话人标注认出成员）
"""
from typing import Dict, List, Optional


def normalize_role_name(name) -> str:
    """标准化角色名，用于关系 from/to 匹配（去空格、统一大小写）。"""
    return re_ws(str(name or ""))


def re_ws(s: str) -> str:
    import re
    return re.sub(r"\s+", "", s or "").strip()


def find_relations(universe: Optional[dict], role_names: List[str]) -> List[dict]:
    """从 universe.relationships 中挑出与 role_names 中任一角色相关的边。

    返回去重后的关系列表，每条含 from/to/relation/familiarity。
    """
    if not universe:
        return []
    rels = universe.get("relationships") or []
    names = {normalize_role_name(n) for n in role_names if n}
    if not names:
        return []
    seen = set()
    out = []
    for r in rels:
        if not isinstance(r, dict):
            continue
        a = normalize_role_name(r.get("from") or "")
        b = normalize_role_name(r.get("to") or "")
        if not a or not b:
            continue
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        seen.add(key)
        if a in names or b in names:
            out.append({
                "from": r.get("from"),
                "to": r.get("to"),
                "relation": r.get("relation", ""),
                "familiarity": r.get("familiarity", ""),
            })
    return out


def render_relationship_block(universe: Optional[dict], self_name: str, max_items: int = 20) -> str:
    """生成"人物关系"区块文本（可注入角色提示词）——围绕当前角色 self_name。"""
    if not universe:
        return ""
    rels = find_relations(universe, [self_name])
    if not rels:
        return ""
    self_n = normalize_role_name(self_name)
    lines = ["## 人物关系（同作品角色）"]
    lines.append(f"你在作品《{universe.get('work') or ''}》中认识：")
    count = 0
    for r in rels:
        other = r["to"] if normalize_role_name(r["from"]) == self_n else r["from"]
        fam = r.get("familiarity") or ""
        rel = r.get("relation") or ""
        tag = f"（{fam}）" if fam else ""
        detail = f" · {rel}" if rel else ""
        if count < max_items:
            lines.append(f"- {other}{tag}{detail}")
            count += 1
    lines.append("")
    return "\n".join(lines)


def render_room_members(
    universe: Optional[dict],
    self_role_id: str,
    members: List[dict],
) -> str:
    """群聊用：生成"当前房间成员身份映射"。

    输入 members: [{role_id, display_name}] 房间里所有角色（含自己）。
    输出仅列'除自己外的其他成员 + 与我的关系'，靠说话人标注(display_name)即可认出。
    """
    if not members:
        return ""
    others = [m for m in members if m.get("role_id") != self_role_id and m.get("display_name")]
    if not others:
        return ""
    self_name = next((m.get("display_name") for m in members if m.get("role_id") == self_role_id), self_role_id)
    role_names = [self_name] + [m.get("display_name") for m in others]
    rels = find_relations(universe, role_names)

    # 建立 关系 查找表：other_display -> relation/familiarity
    rel_map = {}
    for r in rels:
        a, b = r["from"], r["to"]
        for other in others:
            on = other.get("display_name")
            if on and (normalize_role_name(a) == normalize_role_name(self_name) and normalize_role_name(b) == normalize_role_name(on)):
                rel_map[on] = r
            elif on and (normalize_role_name(b) == normalize_role_name(self_name) and normalize_role_name(a) == normalize_role_name(on)):
                rel_map[on] = r

    lines = ["【当前房间成员】", f"你是 {self_name}。房间里的其他成员："]
    for m in others:
        on = m.get("display_name")
        r = rel_map.get(on)
        if r:
            fam = r.get("familiarity") or ""
            rel_detail = r.get("relation") or ""
            tag = f"，{fam}" if fam else ""
            detail = f"，{rel_detail}" if rel_detail else ""
            lines.append(f"- {on}{tag}{detail}")
        else:
            lines.append(f"- {on}（与你无明显已知关系，正常认识/相处即可）")
    return "\n".join(lines)
