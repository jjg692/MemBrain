"""
MemBrain — 每角色 TTS 独立服务管理（GPT-SoVITS）

在原有全局 TTS（core/tts_client.py，单端口连外部已启动服务）之上，新增**每角色一套
独立 GPT-SoVITS 服务**的能力：

  ┌─────────────────────────────────────────────────────────────────┐
  │  MemBrain 后端                                                     │
  │   role_id ──▶ 查 config/tts_roles.json ──▶ 得到该角色 {port,       │
  │                exec_file, config_file(yaml), ref_audio, lang,     │
  │                prompt_text, weights{pth,ckpt}, enabled}            │
  │   · 启动：subprocess 拉起该角色的 api_v2.py（独立端口 + 独立 yaml）    │
  │   · 合成：向该角色端口 POST /tts，携带其参照音频/语言/prompt          │
  └─────────────────────────────────────────────────────────────────┘

为什么"权重(pth/ckpt)+语言+参照音频+prompt"都进角色配置：
- api_v2.py 的**权重**由 `-c <tts_infer.yaml>` 决定（yaml 里 t2s_weights_path/vits_weights_path）
- **语言(text_lang/prompt_lang)与参照音频(ref_audio_path)+转写(prompt_text)**
  是每次 `/tts` 请求的参数（api_v2.py 启动后按请求带）
- 因此初始化某角色 = 生成一份该角色的 yaml（写权重）+ 记录端口/语言/参照音频/prompt，
  启动时 `-c 该yaml -p 该端口`，合成时按角色带参照音频+语言+prompt。

设计原则（与 core/tts_client.py 一致）：默认关闭、纯隔离、失败静默、可开关。
本模块是**新增能力**：未配置任何角色时不产生任何副作用，不影响既有行为。

典型用法：
    from core.tts_roles import list_roles, start_role_tts, stop_role_tts, \
        role_status, speak_for_role
    start_role_tts("kasumi")            # 上一启动该角色 GPT-SoVITS 服务
    speak_for_role("kasumi", "你好呀")   # 用该角色端口合成+播放
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional

from core.config import PROJECT_ROOT

logger = logging.getLogger("membrain.tts_roles")

# 每角色 TTS 配置持久化文件（本地机器专属路径，不入 git；见 .gitignore）
TTS_ROLES_FILE = Path(PROJECT_ROOT) / "config" / "tts_roles.json"
# 自动生成的每角色 tts_infer yaml 输出目录
TTS_ROLES_CONFIGS_DIR = Path(PROJECT_ROOT) / "config" / "tts_roles_configs"

# 默认参照音频/权重目录（GPT-SoVITS 安装根；未配置时按此探测）
_DEFAULT_SOVITS_ROOT = r"D:\Program\GPT-SoVITS-v2pro-20250604-nvidia50"
_DEFAULT_EXEC = "runtime/python.exe"     # 相对 SOVITS 根；可被角色配置覆盖为绝对路径
_DEFAULT_API = "api_v2.py"


# ===================== 配置读写 =====================
# tts_roles.json 结构：
#   { "defaults": {...}, "roles": { "<role_id>": { ...每角色字段... } } }
# 每角色字段：
#   enabled       bool   该角色 TTS 是否启用
#   exec_file     str    api_v2.py 所在 python 解释器（绝对，或相对 GPT-SoVITS 根）
#   api_file      str    api_v2.py 路径（绝对，或相对 GPT-SoVITS 根）
#   sovits_root   str    GPT-SoVITS 安装根目录（用于解析相对路径/生成 yaml 的相对路径）
#   config_file   str    tts_infer yaml 路径（绝对；后端启动时 `-c` 用它）
#   port          int    该角色服务端口
#   lang          str    "zh" 中文 / "ja" 日文
#   ref_audio_path str   参照音频绝对路径
#   prompt_text   str    参照音频转写文本
#   weights       {pth, ckpt}  该角色启动权重（vits/pth + t2s/ckpt）

_ROLE_FIELDS = {
    "enabled": False,
    "exec_file": None,
    "api_file": None,
    "sovits_root": _DEFAULT_SOVITS_ROOT,
    "config_file": None,
    "port": 9880,
    "lang": "zh",
    "ref_audio_path": None,
    "prompt_text": None,
    "weights": None,  # {pth, ckpt}
}


def _defaults_dict() -> dict:
    d = dict(_ROLE_FIELDS)
    d["exec_file"] = os.path.join(_DEFAULT_SOVITS_ROOT, _DEFAULT_EXEC)
    d["api_file"] = os.path.join(_DEFAULT_SOVITS_ROOT, _DEFAULT_API)
    return d


def _load_raw() -> dict:
    try:
        if TTS_ROLES_FILE.exists():
            return json.loads(TTS_ROLES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[tts_roles] 读取配置失败，按空初始化: %s", e)
    return {"defaults": _defaults_dict(), "roles": {}}


def _save(raw: dict):
    try:
        TTS_ROLES_FILE.parent.mkdir(parents=True, exist_ok=True)
        TTS_ROLES_FILE.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("[tts_roles] 保存配置失败: %s", e)


def _role_defaults() -> dict:
    raw = _load_raw()
    return dict(_defaults_dict()) | dict(raw.get("defaults") or {})


def get_role_config(role_id: str) -> dict:
    """返回某角色的 TTS 配置（合并默认值；不存在则返回默认配置）。"""
    raw = _load_raw()
    role = dict(_role_defaults())
    role.update(raw.get("roles", {}).get(role_id, {}) or {})
    role["role_id"] = role_id
    return role


def get_all_role_configs() -> List[dict]:
    raw = _load_raw()
    defaults = _role_defaults()
    roles = raw.get("roles", {}) or {}
    out = []
    for rid, cfg in roles.items():
        merged = dict(defaults)
        merged.update(cfg or {})
        merged["role_id"] = rid
        out.append(merged)
    # 未配置但存在默认 enable 需求的角色不自动加入——保持"零副作用"原则，
    # 管理页只展示 roles.json 中已配置/已启用的角色。
    return out


def save_role_config(role_id: str, cfg: dict) -> dict:
    """保存某角色的 TTS 配置到 tts_roles.json。返回合并后的完整配置。"""
    raw = _load_raw()
    roles = raw.setdefault("roles", {})
    # 只存非空字段（避免把默认值整段写入）
    clean = {}
    for k, v in (cfg or {}).items():
        if v not in (None, ""):
            clean[k] = v
    roles[role_id] = clean
    _save(raw)
    return get_role_config(role_id)


def delete_role_config(role_id: str):
    raw = _load_raw()
    raw.get("roles", {}).pop(role_id, None)
    _save(raw)


def role_configured(role_id: str) -> bool:
    raw = _load_raw()
    return role_id in (raw.get("roles", {}) or {})


# ===================== yaml 生成 =====================
# 生成该角色的 tts_infer.yaml（基于 GPT-SoVITS 默认 tts_infer.yaml 的 custom 段骨架，
# 把权重路径替换成该角色权重；相对路径基于 sovits_root）。
_CUSTOM_YAML_TEMPLATE = """custom:
  bert_base_path: GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large
  cnhuhbert_base_path: GPT_SoVITS/pretrained_models/chinese-hubert-base
  device: cuda
  is_half: true
  t2s_weights_path: {ckpt}
  version: {version}
  vits_weights_path: {pth}
v1:
  bert_base_path: GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large
  cnhuhbert_base_path: GPT_SoVITS/pretrained_models/chinese-hubert-base
  device: cpu
  is_half: false
  t2s_weights_path: GPT_SoVITS/pretrained_models/s1bert25hz-2kh-longer-epoch=68e-step=50232.ckpt
  version: v1
  vits_weights_path: GPT_SoVITS/pretrained_models/s2G488k.pth
"""


def _rel_if_under(path: str, root: Optional[str]) -> str:
    """若 path 位于 root 下则返回相对 root 的路径；否则原样返回（合成请求需要绝对路径时不受影响）。"""
    if not path or not root:
        return path or ""
    try:
        rp = Path(root).resolve()
        pp = Path(path).resolve()
        return str(pp.relative_to(rp)).replace("\\", "/")
    except Exception:
        return path or ""


def ensure_role_yaml(role_id: str) -> Optional[str]:
    """为该角色生成 tts_infer yaml（写到 config/tts_roles_configs/）。返回 yaml 绝对路径；失败 None。

    权重来自角色配置的 weights.pth / weights.ckpt，替换进 custom 段的
    vits_weights_path / t2s_weights_path。若权重缺失则不生成（返回 None）。
    """
    cfg = get_role_config(role_id)
    weights = cfg.get("weights") or {}
    pth = (weights.get("pth") or "").strip()
    ckpt = (weights.get("ckpt") or "").strip()
    if not pth or not ckpt:
        logger.info("[tts_roles] 角色 %s 未配置权重(pth/ckpt)，跳过 yaml 生成", role_id)
        return None
    root = cfg.get("sovits_root") or ""
    pth_rel = _rel_if_under(pth, root) or pth
    ckpt_rel = _rel_if_under(ckpt, root) or ckpt
    version = _detect_version(pth, ckpt)
    body = _CUSTOM_YAML_TEMPLATE.format(ckpt=ckpt_rel, pth=pth_rel, version=version)
    try:
        TTS_ROLES_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
        yaml_path = TTS_ROLES_CONFIGS_DIR / f"tts_{role_id}.yaml"
        yaml_path.write_text(body, encoding="utf-8")
        return str(yaml_path)
    except Exception as e:
        logger.warning("[tts_roles] 生成 yaml 失败(角色 %s): %s", role_id, e)
        return None


def _detect_version(pth: str, ckpt: str) -> str:
    """从权重文件名粗略推断 version（v2/v3/v4…）。仅供参考，api_v2 以 yaml 的 version 为准。
    参考默认目录名/文件名：v2/v2Pro/v3/v4 等。默认为 custom 的 v2。"""
    low = (str(pth) + str(ckpt)).lower()
    for v in ("v4", "v3", "v2prop", "v2pp", "v2"):
        if v in low:
            return {"v4": "v4", "v3": "v3", "v2prop": "v2Pro",
                    "v2pp": "v2Pro", "v2pro": "v2Pro", "v2": "v2"}.get(v, "v2")
    return "v2"


# ===================== 服务进程管理 =====================
# 每角色一个后台子进程。进程池按 role_id 记录 Popen 句柄。

_procs: Dict[str, subprocess.Popen] = {}
_procs_lock = threading.Lock()


def _exec_cmd(role_id: str) -> Optional[List[str]]:
    cfg = get_role_config(role_id)
    exec_file = cfg.get("exec_file") or ""
    api_file = cfg.get("api_file") or ""
    port = int(float(cfg.get("port") or 0))
    if not exec_file or not api_file or not port:
        logger.info("[tts_roles] 角色 %s 未配置 exec_file/api_file/port，无法启动", role_id)
        return None
    yaml_path = cfg.get("config_file") or ensure_role_yaml(role_id)
    if not yaml_path:
        return None
    return [exec_file, api_file, "-a", "127.0.0.1", "-p", str(port), "-c", yaml_path]


def start_role_tts(role_id: str) -> dict:
    """启动某角色的 GPT-SoVITS 服务（后台子进程）。已运行则返回当前状态。"""
    with _procs_lock:
        existing = _procs.get(role_id)
        if existing is not None and existing.poll() is None:
            return {"ok": True, "running": True, "message": "已在运行",
                    "pid": existing.pid, **role_summary(role_id)}
    cmd = _exec_cmd(role_id)
    if not cmd:
        return {"ok": False, "running": False,
                "message": "配置不完整：需 exec_file/api_file/port 及权重(pth+ckpt)"}
    try:
        # 在 GPT-SoVITS 根目录下启动（相对路径/资源解析依赖它）
        root = get_role_config(role_id).get("sovits_root") or _DEFAULT_SOVITS_ROOT
        proc = subprocess.Popen(
            cmd, cwd=root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        logger.warning("[tts_roles] 启动 %s 失败: %s", role_id, e)
        return {"ok": False, "running": False, "message": f"启动失败: {e}"}
    with _procs_lock:
        _procs[role_id] = proc
    logger.info("[tts_roles] 已启动 %s (pid=%s)", role_id, proc.pid)
    return {"ok": True, "running": True, "pid": proc.pid, **role_summary(role_id)}


def stop_role_tts(role_id: str) -> dict:
    """停止某角色的 GPT-SoVITS 服务。"""
    with _procs_lock:
        proc = _procs.get(role_id)
        if proc is None or proc.poll() is not None:
            _procs.pop(role_id, None)
            return {"ok": True, "running": False, "message": "未在运行"}
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        except Exception as e:
            logger.warning("[tts_roles] 停止 %s 失败: %s", role_id, e)
        _procs.pop(role_id, None)
    return {"ok": True, "running": False, "message": "已停止"}


def role_running(role_id: str) -> bool:
    with _procs_lock:
        proc = _procs.get(role_id)
        return bool(proc is not None and proc.poll() is None)


def role_pid(role_id: str) -> Optional[int]:
    with _procs_lock:
        proc = _procs.get(role_id)
        return proc.pid if (proc is not None and proc.poll() is None) else None


def role_summary(role_id: str) -> dict:
    cfg = get_role_config(role_id)
    return {
        "role_id": role_id,
        "running": role_running(role_id),
        "port": cfg.get("port"),
        "lang": cfg.get("lang"),
        "enabled": bool(cfg.get("enabled")),
        "configured": role_configured(role_id),
        "ref_audio_path": cfg.get("ref_audio_path"),
        "prompt_text": cfg.get("prompt_text"),
    }


def list_roles_status() -> List[dict]:
    """返回所有已配置角色的状态列表（供管理页展示）。"""
    return [role_summary(c["role_id"]) for c in get_all_role_configs()]


def stop_all():
    with _procs_lock:
        for rid, proc in list(_procs.items()):
            try:
                if proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass
        _procs.clear()


# ===================== 按角色合成 =====================
def speak_for_role(role_id: str, text: str, timeout: float = 30.0) -> bool:
    """用某角色的 TTS 服务合成并播放一段语音。
    向该角色端口 POST /tts，携带其 ref_audio_path/text_lang/prompt_text。
    失败静默返回 False。"""
    cfg = get_role_config(role_id)
    port = int(float(cfg.get("port") or 0))
    if not port or not role_running(role_id):
        # 服务没起，用 health 探测一下再决定（可能外部已手动启动该端口服务）
        if not _port_alive(port):
            logger.info("[tts_roles] 角色 %s 服务未运行，跳过合成", role_id)
            return False
    ref = cfg.get("ref_audio_path") or ""
    lang = cfg.get("lang") or "zh"
    prompt = cfg.get("prompt_text") or ""
    if not ref:
        logger.info("[tts_roles] 角色 %s 未配置参照音频，跳过合成", role_id)
        return False
    try:
        from core.tts_client import TTSClient
        client = TTSClient(host="127.0.0.1", port=port)
        # 覆盖参照音频 / 语言 / prompt（TTSClient 构造函数会读 .env 默认，这里再覆盖回角色配置）
        client.ref_audio_path = ref
        client.text_lang = lang
        client.prompt_lang = lang
        client.prompt_text = prompt or ""
        return client.speak(text, timeout=timeout)
    except Exception as e:
        logger.warning("[tts_roles] %s 合成失败: %s", role_id, e)
        return False


def _port_alive(port: int, timeout: float = 2.0) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(("127.0.0.1", int(port)))
            return True
        except Exception:
            return False
