#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
角色 Prompt 生成器 CLI

从萌娘百科/Wikipedia/Fandom 检索角色资料 -> LLM 行为蒸馏 -> 生成 CSP 风格 role prompt

用法:
  python scripts/generate_role.py "高松灯" --work "BanG Dream! It's MyGO!!!!!"
  python scripts/generate_role.py "户山香澄" --work "BanG Dream!" --out role_prompts/role_prompt_kasumi.txt
  python scripts/generate_role.py --list-sources
"""
import argparse
import sys
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from role_generator.sources import fetch_character_sources, to_merge_text
from role_generator.distill import distill_character, render_skill_prompt
from core.adapters import OllamaAdapter
from core.config import OLLAMA_HOST


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
    ap.add_argument('--work', default='', help='作品名（如 BanG Dream!，用于选 Fandom）')
    ap.add_argument('--out', default='', help='输出 role prompt 文件路径（默认打印到 stdout）')
    ap.add_argument('--model', default='', help='蒸馏用 LLM 模型（默认用 TOOL_LLM_MODEL）')
    ap.add_argument('--save-source', default='', help='把资料汇总保存到 json（调试用）')
    ap.add_argument('--list-sources', action='store_true', help='列出数据源')
    args = ap.parse_args()

    if args.list_sources:
        print(list_sources())
        return

    if not args.character:
        print('请提供角色名，例如: python scripts/generate_role.py "高松灯" --work "BanG Dream! It Ms MyGO"')
        sys.exit(1)

    print(f'[1/3] 检索资料: {args.character} ...')
    docs = fetch_character_sources(args.character, args.work)
    for d in docs:
        st = d.get('status','?')
        t = (d.get('text') or '')
        print(f"    - {d.get('site','?'):24s} {st:8s} {len(t)} chars")
    merged = to_merge_text(args.character, docs)
    if args.save_source:
        Path(args.save_source).write_text(json.dumps(docs, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'    [来源已存] {args.save_source}')

    # 确定蒸馏用模型
    model = args.model
    from core.config import TOOL_LLM_MODEL
    model = args.model or TOOL_LLM_MODEL
    adapter = OllamaAdapter(model=model, host=OLLAMA_HOST)

    print(f'[2/3] 行为蒸馏 (model={model}) ...')
    data = distill_character(adapter, args.character, merged, work=args.work)
    if not data:
        print('[蒸馏失败] 无法从 LLM 得到有效结果；请确认 Ollama 已运行 (ollama serve)')
        sys.exit(2)

    # 来源描述（写进 prompt 附录）
    ok_docs = [d for d in docs if d.get('status') == 'ok']
    src_lines = [f'- {d.get("site")} ({d.get("rank","")}, {d.get("url","")})' for d in ok_docs]
    sources_desc = '\n'.join(src_lines) if src_lines else ''
    retrieved_at = datetime.now().strftime('%Y-%m-%d')

    print('[3/3] 渲染 prompt ...')
    prompt_text = render_skill_prompt(data, args.character, work=args.work, retrieved_at=retrieved_at, sources_desc=sources_desc)

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