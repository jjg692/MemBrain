"""
后台管理 API
- 联系人管理：增删改查、Prompt 文件内容读写、头像上传/删除、设为默认
- 记忆查看：按 (user_id, role_id) 查看 L1-L5，层级过滤
- 情感/好感度查看
- 系统统计
- 配置管理（读写 .env）
"""
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile

from core.config import ROLE_PROMPTS_DIR, get_config_snapshot, update_config
from core.logger import log_error
from api.live2d import _scan_models

router = APIRouter(prefix="/admin")


def setup_admin(app):
    @router.get("/roles")
    async def list_roles():
        return {"code": 0, "data": app.role_manager.list_contacts()}

    @router.post("/roles/create")
    async def create_role(request: Request):
        body = await request.json()
        role = app.role_manager.create_role(
            role_id=(body.get("role_id") or "").strip(),
            display_name=body.get("display_name", ""),
            prompt=body.get("prompt", ""),
            description=body.get("description", ""),
            live2d_model=body.get("live2d_model", ""),
        )
        if not role:
            return {"code": -1, "message": "创建失败：role_id 为空或已存在"}
        # 立即加载 L5
        try:
            app.memory_manager.ensure_role_facts(role.role_id, body.get("prompt", ""))
        except Exception:
            pass
        return {"code": 0, "data": role.to_dict()}

    @router.post("/roles/update")
    async def update_role(request: Request):
        body = await request.json()
        role_id = body.get("role_id", "")
        ok = app.role_manager.update_role(
            role_id,
            display_name=body.get("display_name"),
            prompt=body.get("prompt"),
            description=body.get("description"),
            default=body.get("default"),
            live2d_model=body.get("live2d_model"),
        )
        if not ok:
            return {"code": -1, "message": "角色不存在"}
        # 若更新了 prompt，刷新 L5
        if body.get("prompt") is not None:
            try:
                app.memory_manager.ensure_role_facts(role_id, body.get("prompt", ""))
            except Exception:
                pass
        return {"code": 0, "message": "更新成功"}

    @router.post("/roles/delete")
    async def delete_role(request: Request):
        body = await request.json()
        ok = app.role_manager.delete_role(body.get("role_id", ""))
        return {"code": 0 if ok else -1, "message": "删除成功" if ok else "角色不存在"}

    @router.get("/roles/{role_id}/prompt")
    async def get_role_prompt(role_id: str):
        app.role_manager.reload_prompt(role_id)
        text = app.role_manager.load_prompt(role_id)
        return {"code": 0, "data": {"role_id": role_id, "prompt": text}}

    @router.post("/roles/{role_id}/avatar")
    async def upload_avatar(role_id: str, file: UploadFile = File(...)):
        data = await file.read()
        ok = app.role_manager.set_avatar(role_id, data, file.filename or "avatar.png")
        return {"code": 0 if ok else -1, "message": "上传成功" if ok else "角色不存在"}

    @router.post("/roles/{role_id}/avatar/delete")
    async def delete_avatar(role_id: str):
        ok = app.role_manager.delete_avatar(role_id)
        return {"code": 0 if ok else -1, "message": "已删除" if ok else "无头像或角色不存在"}

    @router.post("/roles/{role_id}/default")
    async def set_default(role_id: str, request: Request):
        body = await request.json()
        app.role_manager.update_role(role_id, default=body.get("default", True))
        return {"code": 0, "message": "已设置"}

    @router.post("/roles/{role_id}/render")
    async def set_role_render(role_id: str, request: Request):
        """「渲染角色管理」：开关该角色是否在桌面宠物渲染窗中渲染。
        body: {render_enabled: true|false}。默认角色（看板娘）始终渲染，不受开关影响。"""
        body = await request.json()
        rendered = bool(body.get("render_enabled", False))
        ok = app.role_manager.set_render_enabled(role_id, rendered)
        if not ok:
            return {"code": -1, "message": "角色不存在"}
        return {"code": 0, "message": "已更新", "rendered": app.role_manager.get(role_id).is_rendered()}

    @router.get("/roles/rendered")
    async def list_rendered_roles():
        """返回当前需要在桌面宠物中渲染的角色（供 Qt 多窗口宿主对齐窗口）。"""
        items = []
        for role in app.role_manager.rendered_roles():
            d = role.to_dict()
            d["rendered"] = True
            items.append(d)
        return {"code": 0, "data": items}

    # ===================== Live2D 模型路径（每角色） =====================

    @router.get("/live2d/models")
    async def live2d_model_list(role_id: str = ""):
        """列出可选 Live2D 模型（live2d/ 下含 model.json 的目录相对路径），
        供后台「浏览」按钮弹出选择框使用。

        支持按角色过滤：若传入 role_id，只返回"属于该角色"的模型——
          - 若该角色已配置 live2d_model，取其顶层目录（角色目录名）过滤同目录模型；
          - 否则用 role_id 小写匹配模型路径顶层目录名小写（role_id 与目录名都是角色罗马音）。
        不传 role_id 则返回全部。"""
        models = _scan_models()
        items = [{
            "id": m["id"],
            "name": m["name"],
            "path": m["path"],
            "model_url": m["model_url"],
        } for m in models]

        if role_id:
            role = app.role_manager.get(role_id)
            top_dir = ""
            # 优先用已配置的 live2d_model 的顶层目录
            if role and role.live2d_model:
                top_dir = role.live2d_model.split("/", 1)[0].strip()
            # 否则用 role_id 小写匹配目录名小写
            if not top_dir:
                rid_low = role_id.strip().lower()
                dirs = {m["path"].split("/", 1)[0] for m in items if "/" in m["path"]}
                for d in dirs:
                    if rid_low and rid_low in d.lower():
                        top_dir = d
                        break
            if top_dir:
                items = [m for m in items if m["path"].split("/", 1)[0] == top_dir]

        return {"code": 0, "data": items, "filter_dir": top_dir if role_id else ""}

    @router.post("/roles/{role_id}/live2d")
    async def set_role_live2d(role_id: str, request: Request):
        """为角色设置 Live2D 模型路径。body: {live2d_model: "..."}"""
        body = await request.json()
        ok = app.role_manager.update_role(
            role_id, live2d_model=body.get("live2d_model", ""))
        if not ok:
            return {"code": -1, "message": "角色不存在"}
        return {"code": 0, "message": "已更新"}

    # ===================== 记忆查看 =====================

    @router.get("/memory")
    async def view_memory(
        user_id: str = "default_user",
        role_id: str = "",
        level: str = "",
        n: int = 50,
    ):
        if not role_id:
            role_id = app.role_manager.get_default_role() or "kasumi"
        result: dict = {"code": 0, "data": {}}
        mem = app.memory
        where = mem._build_where
        # L4 事实
        if not level or level == "l4":
            res = mem.get(where({"user_id": user_id}, {"role_id": role_id}, {"type": "fact"}), limit=n)
            result["data"]["l4"] = [
                {"id": i["id"], "content": i["document"], "meta": i["metadata"]} for i in res["results"]
            ]
        # L5 角色事实
        if not level or level == "l5":
            res = mem.get(where({"type": "role_fact"}, {"role_id": role_id}), limit=n)
            result["data"]["l5"] = [
                {"id": i["id"], "content": i["document"], "meta": i["metadata"]} for i in res["results"]
            ]
        # L2 短期记忆
        if not level or level == "l2":
            res = mem.get(where({"user_id": user_id}, {"role_id": role_id}, {"type": "short_term"}), limit=n)
            result["data"]["l2"] = [
                {"id": i["id"], "content": i["document"], "meta": i["metadata"]} for i in res["results"]
            ]
        # L1 内存上下文
        if not level or level == "l1":
            agent = app.agent_factory.get_agent(user_id, role_id)
            l1 = agent.memory.get_l1(user_id, role_id)
            result["data"]["l1"] = [{"role": m["role"], "content": m["content"]} for m in l1[-n:]]
        # L3（主动信息池：全局共享，按 type 查询，与存储键 role_id=keyword 对齐）
        if not level or level == "l3":
            res = mem.get(where({"type": "l3_info"}), limit=n)
            result["data"]["l3"] = [
                {"id": i["id"], "content": i["document"], "meta": i["metadata"]} for i in res["results"]
            ]
        return result

    @router.get("/memory/emotion")
    async def view_emotion(user_id: str = "default_user", role_id: str = ""):
        if not role_id:
            role_id = app.role_manager.get_default_role() or "kasumi"
        agent = app.agent_factory.get_agent(user_id, role_id)
        info = agent.get_session_info(user_id)
        return {"code": 0, "data": info}

    @router.get("/memory/relation")
    async def view_relation(user_id: str = "default_user", role_id: str = ""):
        """读取关系记忆内核（自我模型/共同经历/反思/承诺/情绪衰减），按 role_id 的独立 JSON 文件。"""
        import copy
        if not role_id:
            role_id = app.role_manager.get_default_role() or "kasumi"
        try:
            from core.relation_memory import get_relation_memory
            rel = get_relation_memory(role_id)
            data = copy.deepcopy(rel._data.get(user_id or "default_user", {}))
            return {"code": 0, "data": data}
        except Exception as e:
            log_error("admin.relation", e)
            return {"code": -1, "message": f"读取关系记忆失败：{e}", "data": {}}

    # ===================== 系统统计 =====================

    @router.get("/stats")
    async def stats():
        mem_stats = app.memory_manager.stats()
        online = app.get_online_counts()
        return {"code": 0, "data": {**mem_stats, "online": online, "roles": len(app.role_manager.all_roles())}}

    # ===================== LLM 管理 =====================

    @router.get("/llm/config")
    async def llm_config():
        return {"code": 0, "data": app.llm_manager.get_config()}

    @router.post("/llm/test")
    async def llm_test(request: Request):
        body = await request.json()
        # 可选：临时覆盖配置再测（不持久化）；留空则测当前配置
        if body:
            # 临时性测试：应用到内存 os.environ 但不写 .env，测完恢复
            saved = {}
            mapping = app.llm_manager._map_key
            for k, v in body.items():
                env_k = mapping(k)
                if env_k:
                    saved[env_k] = __import__("os").environ.get(env_k)
                    __import__("os").environ[env_k] = str(v)
            try:
                result = app.llm_manager.test_connection()
            finally:
                import os as _os
                for env_k, old in saved.items():
                    if old is None:
                        _os.environ.pop(env_k, None)
                    else:
                        _os.environ[env_k] = old
        else:
            result = app.llm_manager.test_connection()
        return {"code": 0 if result.get("ok") else -1, "data": result,
                "message": "连接成功" if result.get("ok") else result.get("error", "连接失败")}

    @router.post("/llm/switch")
    async def llm_switch(request: Request):
        body = await request.json()
        result = app.llm_manager.switch_config(body)
        if result.get("errors"):
            return {"code": -1, "message": "；".join(result["errors"]), "data": result}
        return {"code": 0, "message": "LLM 配置已更新并生效", "data": result}

    # ===================== 配置管理 =====================

    @router.get("/config")
    async def get_config():
        return {"code": 0, "data": get_config_snapshot()}

    @router.post("/config/update")
    async def set_config(request: Request):
        body = await request.json()
        key = body.get("key", "")
        value = body.get("value", "")
        if not update_config(key, value):
            return {"code": -1, "message": "不可修改的配置项"}
        return {"code": 0, "message": f"{key} 已更新并持久化到 .env"}

    # ===================== TTS 语音合成管理 =====================

    @router.get("/tts/status")
    async def tts_status():
        """返回 TTS 当前状态与配置（开关/地址/参照音频等）。动态读取，无需重启。"""
        try:
            from core.tts_client import status as tts_status_fn
            return {"code": 0, "data": tts_status_fn()}
        except Exception as e:
            log_error("admin.tts.status", e)
            return {"code": -1, "message": f"读取 TTS 状态失败: {e}"}

    @router.post("/tts/save")
    async def tts_save(request: Request):
        """保存 TTS 配置到 .env（含开关），即时生效（动态读取）。"""
        body = await request.json()
        mapping = {
            "TTS_ENABLED": "bool", "TTS_HOST": "str", "TTS_PORT": "str",
            "TTS_REF_AUDIO_PATH": "str", "TTS_PROMPT_TEXT": "str",
            "TTS_TEXT_LANG": "str", "TTS_PROMPT_LANG": "str",
            "TTS_MEDIA_TYPE": "str", "TTS_SPEED_FACTOR": "str",
        }
        updated = []
        for key, dtype in mapping.items():
            if key in body:
                val = body[key]
                if dtype == "bool":
                    val = "true" if str(val).strip().lower() in ("1", "true", "yes", "on") else "false"
                elif val is None:
                    val = ""
                if not update_config(key, str(val)):
                    return {"code": -1, "message": f"不可修改的配置项 {key}"}
                updated.append(key)
        return {"code": 0, "message": f"TTS 配置已保存并生效（{len(updated)} 项）"}

    @router.post("/tts/test")
    async def tts_test(request: Request):
        """测试 GPT-SoVITS 服务连通性（读 /tts 存活）+ 可选合成一小段。"""
        body = {}
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            text = (body.get("text") or "").strip() or "你好呀，我是你的语音助手。"
            from core.tts_client import TTSClient
            client = TTSClient()
            # 1) 连通性
            alive = client.health()
            if not alive:
                return {"code": -1, "data": {"ok": False, "synth": False},
                        "message": "无法连接 GPT-SoVITS 服务（请先启动 api_v2.py）"}
            # 2) 综合
            audio = client.synthesize(text, timeout=30.0)
            if not audio:
                return {"code": -1, "data": {"ok": True, "synth": False},
                        "message": "服务在线，但合成失败（检查参照音频路径/文本语言）"}
            return {"code": 0, "data": {"ok": True, "synth": True, "bytes": len(audio)},
                    "message": f"服务在线，合成成功（{len(audio)} 字节）"}
        except Exception as e:
            log_error("admin.tts.test", e)
            return {"code": -1, "message": f"测试失败: {e}"}

    # ===================== 星露谷 MCP 管理 =====================

    @router.get("/stardew/status")
    async def stardew_status():
        """返回星露谷运行时状态：开关、轮询器状态、当前游戏状态、最近记忆。"""
        data = {"enabled": False, "tools_count": 0}
        # 是否开启（以 config/mcp.json 未加载时为准）
        try:
            from core.config import STARDEW_MCP_ENABLED
            data["enabled"] = bool(STARDEW_MCP_ENABLED)
        except Exception:
            pass
        # 已注册的 mcp_stardew_* 工具数
        try:
            from core.tools import TOOL_REGISTRY
            data["tools_count"] = len([k for k in TOOL_REGISTRY if k.startswith("mcp_") and "stardew" in k])
        except Exception:
            pass
        # 轮询器状态
        try:
            poller = getattr(app, "stardew_poller", None)
            if poller is not None:
                data["poller"] = poller.status()
        except Exception as e:
            log_error("admin.stardew.status", e)
        # 最近游戏记忆（L1 带 [星露谷] 标记）
        try:
            from stardew.game_memory import GameMemoryBridge
            bridge = GameMemoryBridge(app.memory_manager, role_id="kasumi")
            data["memories"] = bridge.remember("default_user", "星露谷", top_k=8)
        except Exception:
            data["memories"] = ""
        return {"code": 0, "data": data}

    @router.post("/stardew/test")
    async def stardew_test():
        """测试 MCP 星露谷链路：尝试读取一次游戏状态。"""
        result = {"ok": False}
        # 先检查扩展是否开启、工具是否注册
        try:
            from core.tools import TOOL_REGISTRY
            from stardew.runtime import is_state_tool_name
            has_tools = any(is_state_tool_name(k) for k in TOOL_REGISTRY)
            state_tool = next((k for k in TOOL_REGISTRY if is_state_tool_name(k)), None)
        except Exception:
            has_tools, state_tool = False, None
        if not has_tools or not state_tool:
            return {"code": -1, "data": {"ok": False},
                    "message": "扩展开关或 MCP 服务器未就绪（请先在配置管理开启 STARDEW_MCP_ENABLED 并重启后端）"}
        try:
            from core.mcp_client import get_mcp_manager
            mgr = get_mcp_manager()
            raw = None
            try:
                raw = mgr.call(state_tool, {})
            except Exception as e:
                return {"code": -1, "data": {"ok": False, "error": f"调用失败: {e}"}}
            text = (raw or "").strip()
            result["ok"] = bool(text) and not text.startswith("Error") and "工具错误" not in text
            result["raw"] = text[:800]
            if not result["ok"]:
                result["error"] = text[:200] or "无返回"
            return {"code": 0 if result["ok"] else -1, "data": result,
                    "message": "链路正常，已读到游戏状态" if result["ok"] else "未读到游戏状态（游戏未开或 bridge 未就绪）"}
        except Exception as e:
            log_error("admin.stardew.test", e)
            return {"code": -1, "data": {"ok": False, "error": str(e)}, "message": f"测试失败: {e}"}

    @router.post("/stardew/refresh")
    async def stardew_refresh():
        """强制轮询器立即读取一次并（如有变化）沉淀记忆。"""
        try:
            poller = getattr(app, "stardew_poller", None)
            if poller is None:
                return {"code": -1, "message": "轮询器未初始化（需开启星露谷 MCP 后重启生效）"}
            poller.record_fingerprint()
            return {"code": 0, "data": poller.status(), "message": "已刷新"}
        except Exception as e:
            log_error("admin.stardew.refresh", e)
            return {"code": -1, "message": f"刷新失败: {e}"}

    # ===================== MCP 服务管理（插件化启停） =====================

    @router.get("/mcp/status")
    async def mcp_status():
        """列出所有配置的 MCP 服务状态（运行/启用/工具数/错误）。"""
        try:
            registry = getattr(app, "mcp_registry", None)
            if registry is None:
                return {"code": -1, "message": "MCP 注册中心未初始化"}
            items = registry.status_all()
            return {"code": 0, "data": items}
        except Exception as e:
            log_error("admin.mcp.status", e)
            return {"code": -1, "message": f"读取失败: {e}"}

    # ============ 星露谷自主游玩心跳（星露谷 MCP 扩展） ============
    # 注意：这些静态路径必须声明在 /mcp/{name}/... 通配之前，否则 FastAPI 会把
    # "stardew-autonomy" 当成 {name} 捕获，导致 "未找到 MCP server 配置"。

    @router.get("/mcp/stardew-autonomy/status")
    async def mcp_stardew_autonomy_status():
        try:
            registry = getattr(app, "mcp_registry", None)
            if registry is None:
                return {"code": -1, "message": "MCP 注册中心未初始化"}
            return {"code": 0, "data": registry.heartbeat_status()}
        except Exception as e:
            log_error("admin.mcp.autonomy", e)
            return {"code": -1, "message": f"读取失败: {e}"}

    @router.post("/mcp/stardew-autonomy/start")
    async def mcp_stardew_autonomy_start():
        try:
            registry = getattr(app, "mcp_registry", None)
            if registry is None:
                return {"code": -1, "message": "MCP 注册中心未初始化"}
            res = registry.start_heartbeat()
            return {"code": 0 if res.get("ok") else -1, "data": res, "message": res.get("message", "")}
        except Exception as e:
            log_error("admin.mcp.autonomy", e)
            return {"code": -1, "message": f"启动失败: {e}"}

    @router.post("/mcp/stardew-autonomy/stop")
    async def mcp_stardew_autonomy_stop():
        try:
            registry = getattr(app, "mcp_registry", None)
            if registry is None:
                return {"code": -1, "message": "MCP 注册中心未初始化"}
            res = registry.stop_heartbeat()
            return {"code": 0 if res.get("ok") else -1, "data": res, "message": res.get("message", "")}
        except Exception as e:
            log_error("admin.mcp.autonomy", e)
            return {"code": -1, "message": f"停止失败: {e}"}

    @router.post("/mcp/stardew-autonomy/llm")
    async def mcp_stardew_autonomy_llm(request: Request):
        """切换星露谷自主游玩心跳的 LLM 决策开关。"""
        try:
            body = await request.json()
            registry = getattr(app, "mcp_registry", None)
            if registry is None:
                return {"code": -1, "message": "MCP 注册中心未初始化"}
            res = registry.set_heartbeat_llm(bool(body.get("enabled", False)))
            return {"code": 0 if res.get("ok") else -1, "data": res, "message": res.get("message", "")}
        except Exception as e:
            log_error("admin.mcp.autonomy", e)
            return {"code": -1, "message": f"切换失败: {e}"}

    @router.post("/mcp/{name}/start")
    async def mcp_start(name: str):
        """启动单个 MCP 服务（插件化）。"""
        try:
            registry = getattr(app, "mcp_registry", None)
            if registry is None:
                return {"code": -1, "message": "MCP 注册中心未初始化"}
            res = registry.start(name)
            return {"code": 0 if res.get("ok") else -1, "data": res, "message": res.get("message", "")}
        except Exception as e:
            log_error("admin.mcp.start", e)
            return {"code": -1, "message": f"启动失败: {e}"}

    @router.post("/mcp/{name}/stop")
    async def mcp_stop(name: str):
        """停止单个 MCP 服务（插件化），并注销其工具。"""
        try:
            registry = getattr(app, "mcp_registry", None)
            if registry is None:
                return {"code": -1, "message": "MCP 注册中心未初始化"}
            res = registry.stop(name)
            return {"code": 0 if res.get("ok") else -1, "data": res, "message": res.get("message", "")}
        except Exception as e:
            log_error("admin.mcp.stop", e)
            return {"code": -1, "message": f"停止失败: {e}"}

    @router.post("/mcp/{name}/restart")
    async def mcp_restart(name: str):
        """重启单个 MCP 服务。"""
        try:
            registry = getattr(app, "mcp_registry", None)
            if registry is None:
                return {"code": -1, "message": "MCP 注册中心未初始化"}
            res = registry.restart(name)
            return {"code": 0 if res.get("ok") else -1, "data": res, "message": res.get("message", "")}
        except Exception as e:
            log_error("admin.mcp.restart", e)
            return {"code": -1, "message": f"重启失败: {e}"}

    @router.post("/mcp/{name}/test")
    async def mcp_test(name: str):
        """测试指定 MCP 服务链路（尝试 tools/list）。"""
        try:
            registry = getattr(app, "mcp_registry", None)
            if registry is None:
                return {"code": -1, "message": "MCP 注册中心未初始化"}
            res = registry.test(name)
            return {"code": 0 if res.get("ok") else -1, "data": res, "message": res.get("message", "")}
        except Exception as e:
            log_error("admin.mcp.test", e)
            return {"code": -1, "message": f"测试失败: {e}"}

    @router.post("/mcp/{name}/enabled")
    async def mcp_set_enabled(name: str, request: Request):
        """持久化 MCP 服务的 enabled 开关到 config/mcp.json（重启后端或手动 start 生效）。"""
        try:
            body = await request.json()
            registry = getattr(app, "mcp_registry", None)
            if registry is None:
                return {"code": -1, "message": "MCP 注册中心未初始化"}
            res = registry.set_enabled(name, bool(body.get("enabled", False)))
            return {"code": 0 if res.get("ok") else -1, "data": res,
                    "message": "已保存开关（启用后需点「启动」或重启后端生效）"}
        except Exception as e:
            log_error("admin.mcp.enabled", e)
            return {"code": -1, "message": f"保存失败: {e}"}

    return router
