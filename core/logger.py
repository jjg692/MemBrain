# core/logger.py
"""
统一调试日志模块
提供带时间戳和分类的日志打印
"""
import time
from datetime import datetime


def log_time(step_name: str, start_time: float = None) -> None:
    """打印耗时日志"""
    if start_time:
        elapsed = (time.time() - start_time) * 1000
        print(f"[时间] {step_name} 耗时：{elapsed:.2f}ms")
    else:
        print(f"[时间] {step_name}，时间：{datetime.now().strftime('%H:%M:%S.%f')[:-3]}")


def log_debug(module: str, msg: str) -> None:
    """打印调试日志"""
    print(f"[调试] {module}: {msg}")


def log_router(msg: str) -> None:
    """打印路由日志"""
    print(f"[路由] {msg}")


def log_store(msg: str) -> None:
    """打印存储日志"""
    print(f"[存储] {msg}")