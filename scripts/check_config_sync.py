#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
配置同步校验（防配置文档漂移）

检查 `core/config.py` 中所有 `os.getenv(...)` 读取的配置项，是否都在 `.env.example`
里有对应的示例行。项目规范：`.env.example` 是配置权威来源，新增/删除配置必须同步。

用法:
  python scripts/check_config_sync.py            # 只检查，有漂移则退出码 1
  python scripts/check_config_sync.py --json     # 输出 JSON（便于 CI/脚本解析）

退出码:
  0 = 全部配置项在 .env.example 有对应样例（含明确的白名单例外）
  1 = 存在漂移（config 有、env.example 缺）
"""
import re
import sys
from pathlib import Path

# 兼容 Windows 默认 GBK 控制台：不打印 emoji，只用 ASCII 标记
OK_MARK = "[OK]"
MISS_MARK = "[MISSING]"
INFO_MARK = "[INFO]"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PY = PROJECT_ROOT / "core" / "config.py"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"

# 允许出现在 config.py、但无需在 .env.example 展示的 key：
# - 内部路径常量（非用户配置，由代码推导）
# - 纯派生/内部变量，不通过 os.getenv 暴露
WHITELIST = {
    "PROJECT_ROOT",
    "CHROMA_DB_PATH",       # 内部推导路径（chromadb/）
    "ROLES_FILE",           # 内部路径（config/roles.json）
    "ROLE_PROMPTS_DIR",     # 内部路径（role_prompts/）
    "AVATARS_DIR",          # 内部路径（static/avatars/agents）
    "UPLOADS_DIR",          # 内部路径（uploads/）
}


def extract_getenv_keys(text: str) -> set:
    """提取 config.py 中 os.getenv("KEY", default) 用到的全部 key。"""
    keys = set()
    # 常规: os.getenv("KEY", ...)
    keys |= set(re.findall(r'os\.getenv\(\s*"([A-Z][A-Z0-9_]*)"', text))
    # 占位拼接: os.getenv("KEY", ...) 在 Path(...) 内
    keys |= set(re.findall(r'os\.getenv\(\s*\'([A-Z][A-Z0-9_]*)\'', text))
    keys |= set(re.findall(r'os\.getenv\(\s*"([A-Z][A-Z0-9_]*)"', text))
    return {k for k in keys if k}


def extract_env_keys(text: str) -> set:
    """提取 .env.example 中 'KEY=value' 的 key。"""
    return set(re.findall(r'^([A-Z][A-Z0-9_]*)\s*=', text, re.MULTILINE))


def check() -> dict:
    cfg_text = CONFIG_PY.read_text(encoding="utf-8")
    env_text = ENV_EXAMPLE.read_text(encoding="utf-8")
    config_keys = extract_getenv_keys(cfg_text)
    env_keys = extract_env_keys(env_text)
    missing = sorted(k for k in config_keys if k not in env_keys and k not in WHITELIST)
    syncable = sorted(env_keys - config_keys)  # env 有但 config 没用到的（冗余提示，不算错）
    return {
        "config_keys": len(config_keys),
        "env_keys": len(env_keys),
        "missing": missing,
        "config_only_whitelisted": sorted(k for k in config_keys if k in WHITELIST),
        "env_not_in_config": syncable,
    }


def main() -> int:
    import json as _json
    result = check()
    as_json = "--json" in sys.argv
    if as_json:
        print(_json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"config.py os.getenv keys : {result['config_keys']}")
        print(f".env.example keys        : {result['env_keys']}")
        if result["missing"]:
            print(f"\n{MISS_MARK} 以下配置项在 config.py 读取，但 .env.example 缺示例（漂移）:")
            for k in result["missing"]:
                print(f"   - {k}")
        else:
            print(f"{OK_MARK} 所有 config.py 读取的配置项在 .env.example 均有对应示例。")
        if result["env_not_in_config"]:
            print(f"\n{INFO_MARK}  .env.example 里有、但 config.py 未读取的项（冗余，可留意）:")
            for k in result["env_not_in_config"]:
                print(f"   - {k}")
    return 1 if result["missing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
