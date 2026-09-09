#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
角色 Prompt 生成器 CLI

从萌娘百科/Wikipedia/Fandom 检索角色资料 -> LLM 行为蒸馏 -> 生成 CSP 风格 role prompt

用法:
  python scripts/generate_role.py "高松灯" --work "BanG Dream! It's MyGO!!!!!"
  python scripts/generate_role.py "户山香澄" --work "BanG Dream!" --out role_prompts/role_prompt_kasumi.txt
  python scripts/generate_role.py --list-sources

改进（对齐 CSP）:
  - 蒸馏含“一阶段多维度 + 精修(运行核心/新情境/去口癖)”，可用 --no-refine 关闭。
  - 作品级世界观蒸馏：首个同作品角色触发一次并缓存（distill_output/works/），
    后续同作品新角色只做角色蒸馏、复用同一套中立世界观（含人物关系图谱）。
    可用 --force-work 强制重蒸馏世界观。
  - 生成后自动跑轻量质量检查（quality_check），打印 issues/warnings。
"""
import argparse
import sys
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from role_generator.sources import fetch_character_sources, to_merge_text
from role_generator.distill import distill_character, render_skill_prompt, quality_check
from role_generator.work_meta import get_or_distill_work, load_work, work_cache_path
from role_generator.relationships import render_relationship_block, render_room_members
from core.llm_manager import LLMManager


def list_sources() -> str:
    return (
        '可用数据源 (MediaWiki API):\n'
        '  - moegirl   萌娘百科 (zh.moegirl.org.cn)  rank=high\n'
        '  - wikipedia 中文维基 (zh.wikipedia.org)    rank=high\n'
        '  - fandom    作品 Fandom Wiki (按作品自动选) rank=medium\n'
    )


def main():
    ap = argparse.ArgumentParser(description='检索并蒸馏生成角色 prompt')
    ap.add_argument('character', nargs='?', help='角色名（如 高松灯 / 户山香澄）')
    ap.add_argument('--work', default='', help='作品名（如 BanG Dream!，用于选 Fandom 与世界观蒸馏）')
    ap.add_argument('--out', default='', help='输出 role prompt 文件路径（默认打印到 stdout）')
    ap.add_argument('--model', default='', help='蒸馏用 LLM 模型（默认用 TOOL_LLM_MODEL）')
    ap.add_argument('--no-refine', action='store_true', help='跳过第二阶段精修（只做一阶段蒸馏）')
    ap.add_argument('--no-work', action='store_true', help='跳过作品级世界观蒸馏（不注入人物关系）')
    ap.add_argument('--force-work', action='store_true', help='强制重新蒸馏作品世界观（忽略缓存）')
    ap.add_argument('--save-source', default='', help='把资料汇总保存到 json（调试用）')
    ap.add_argument('--save-distill', default='', help='把蒸馏 JSON 保存到文件（调试/审计用）')
    ap.add_argument('--list-sources', action='store_true', help='列出数据源')
    args = ap.parse_args()

    if args.list_sources:
        print(list_sources())
        return

    if not args.character:
        print('请提供角色名，例如: python scripts/generate_role.py "高松灯" --work "BanG Dream! It Ms MyGO"')
        sys.exit(1)

    # 蒸馏用 LLM：与项目主体保持一致（LLMManager 按当前 provider 构建）
    import os
    _mgr = LLMManager()
    if args.model:
        env_key = 'LLM_REMOTE_MODEL' if os.environ.get('LLM_PROVIDER', 'ollama').strip().lower() == 'openai' else 'LLM_MODEL'
        os.environ[env_key] = args.model
    adapter = _mgr.build_llm_adapter()
    model = getattr(adapter, 'model', '') or args.model

    # ===== 0. 作品级世界观蒸馏（中立权重；首角色触发一次，之后复用） =====
    universe = None
    relationships_block = ''
    if args.work and not args.no_work:
        print('[0/x] 处理作品级世界观 ...')
        universe = get_or_distill_work(adapter, args.work, force=args.force_work)
        if universe:
            from role_generator.relationships import render_relationship_block
            relationships_block = render_relationship_block(universe, args.character)
            print(f'    [world] 世界观就绪: {"work="+str(universe.get("work"))} '
                  f'groups={len(universe.get("groups") or [])} '
                  f'relations={len(universe.get("relationships") or [])}')
            if relationships_block:
                print('    [world] 已为该角色生成人物关系区块')
        else:
            print('    [world] 作品世界观蒸馏失败/未识别，跳过（不影响角色蒸馏）')

    # ===== 1. 检索角色资料 =====
    print(f'[1/4] 检索资料: {args.character} ...')
    docs = fetch_character_sources(args.character, args.work)
    for d in docs:
        st = d.get('status', '?')
        t = (d.get('text') or '')
        print(f"    - {d.get('site', '?'):24s} {st:8s} {len(t)} chars")
    merged = to_merge_text(args.character, docs)
    if args.save_source:
        Path(args.save_source).write_text(json.dumps(docs, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'    [来源已存] {args.save_source}')

    # ===== 2. 角色行为蒸馏 =====
    step = '一阶段蒸馏 + 精修' if not args.no_refine else '单趟蒸馏'
    print(f'[2/4] 行为蒸馏 ({step}, provider代管, model={model}) ...')
    data = distill_character(adapter, args.character, merged, work=args.work, refine=not args.no_refine)
    if not data:
        print('[蒸馏失败] 无法从 LLM 得到有效结果；请确认 LLM 服务可用（当前 provider 配置）')
        sys.exit(2)

    if args.save_distill:
        Path(args.save_distill).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'    [蒸馏JSON已存] {args.save_distill}')

    # ===== 3. 质量检查 =====
    print('[3/4] 质量检查 ...')
    qc = quality_check(data)
    if qc.get('issues'):
        print('    [已拦截] 关键维度缺失:')
        for i in qc['issues']:
            print(f'      - {i}')
    if qc.get('warnings'):
        print('    [提醒] 可优化项:')
        for w in qc['warnings']:
            print(f'      - {w}')
    if not qc.get('passed'):
        print('    [注意] 存在缺失维度。如需更完整结果可重试（LLM 偶发截断），'
              '或 --save-distill 导出后手工补齐。')

    # ===== 4. 渲染 =====
    ok_docs = [d for d in docs if d.get('status') == 'ok']
    src_lines = [f'- {d.get("site")} ({d.get("rank", "")}, {d.get("url", "")})' for d in ok_docs]
    sources_desc = '\n'.join(src_lines) if src_lines else ''
    retrieved_at = datetime.now().strftime('%Y-%m-%d')

    print('[4/4] 渲染 prompt ...')
    prompt_text = render_skill_prompt(
        data, args.character, work=args.work,
        retrieved_at=retrieved_at, sources_desc=sources_desc,
        relationships_block=relationships_block,
    )

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(prompt_text, encoding='utf-8')
        print(f'[完成] 已写入: {out}')
    else:
        print('\n===== 生成的角色 prompt =====\n')
        print(prompt_text)


if __name__ == '__main__':
    main()
