"""
PC 控制模块 - 通过独立虚拟环境执行 Windows 自动化任务
"""
import subprocess
import json
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).parent.parent.parent

# 独立虚拟环境 Python 解释器路径
VENV_PYTHON = BASE_DIR / "pc_env" / "Scripts" / "python.exe"
# Worker 脚本路径（与 __init__.py 同目录）
WORKER_SCRIPT = Path(__file__).parent / "worker.py"

def execute_pc_task(task: str) -> str:
    """
    执行 PC 控制任务，通过子进程调用独立虚拟环境
    """
    if not VENV_PYTHON.exists():
        return "❌ PC 控制环境未配置，请先运行 'python -m venv pc_env' 并安装 windows-use"

    if not WORKER_SCRIPT.exists():
        return "❌ PC 控制 Worker 脚本缺失"

    try:
        result = subprocess.run(
            [str(VENV_PYTHON), str(WORKER_SCRIPT), task],
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=120
        )

        if result.returncode != 0:
            return f"❌ PC 控制执行失败: {result.stderr.strip()}"

        try:
            data = json.loads(result.stdout.strip())
            if data.get("success"):
                return data.get("result", "任务执行成功")
            else:
                return f"❌ {data.get('error', '未知错误')}"
        except json.JSONDecodeError:
            return f"❌ 无法解析 Worker 输出: {result.stdout.strip()}"

    except subprocess.TimeoutExpired:
        return "⏰ PC 控制任务超时（超过120秒）"
    except Exception as e:
        return f"❌ PC 控制异常: {e}"

__all__ = ['execute_pc_task']