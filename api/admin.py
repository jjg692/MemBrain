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
        # L3
        if not level or level == "l3":
            res = mem.get(where({"user_id": user_id}, {"role_id": role_id}, {"type": "l3_info"}), limit=n)
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

    # ===================== 系统统计 =====================

    @router.get("/stats")
    async def stats():
        mem_stats = app.memory_manager.stats()
        online = app.get_online_counts()
        return {"code": 0, "data": {**mem_stats, "online": online, "roles": len(app.role_manager.all_roles())}}

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

    return router
