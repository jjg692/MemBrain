"""
轻量日志工具
"""
import sys
from datetime import datetime


def _prefix(tag: str) -> str:
    return f"[{datetime.now().strftime('%H:%M:%S')}][{tag}]"


def log_info(tag: str, msg: str):
    print(f"{_prefix(tag)} {msg}", flush=True)


def log_debug(tag: str, msg: str):
    from core.config import MEMORY_DEBUG
    if MEMORY_DEBUG:
        print(f"{_prefix('DBG')}[{tag}] {msg}", flush=True)


def log_error(tag: str, msg: str):
    print(f"{_prefix('ERR')}[{tag}] {msg}", file=sys.stderr, flush=True)
