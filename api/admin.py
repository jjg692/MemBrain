# api/admin.py
"""
后台管理 API
提供配置管理、记忆查看/编辑、情感状态、系统统计等功能
"""
import os
import json
from pathlib import Path
from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
import dotenv

# 全局依赖（由 web_app.py 注入）
memory = None
agent_factory = None
room_manager = None
role_pool = None
l3_manager = None

router = APIRouter(prefix="/admin", tags=["admin"])

# ==================== 配置管理 ====================
class ConfigUpdate(BaseModel):
    key: str
    value: str

@router.get("/config")
async def get_config():
    """获取关键环境变量"""
    keys = [
        "LLM_MODEL", "TOOL_LLM_MODEL",
        "MEMORY_SHORT_TERM_MAX_ROUNDS",
        "MEMORY_IMPORTANCE_THRESHOLD",
        "MEMORY_DEBUG",
        "L3_UPDATE_INTERVAL",
        "L3_PUSH_INTERVAL"
    ]
    return {k: os.getenv(k, "") for k in keys}

@router.post("/config")
async def update_config(update: ConfigUpdate):
    """更新环境变量并持久化到 .env"""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        raise HTTPException(status_code=404, detail=".env file not found")
    
    # 更新当前进程
    os.environ[update.key] = update.value
    # 更新 .env 文件
    dotenv.set_key(env_path, update.key, update.value)
    return {"status": "ok", "key": update.key, "value": update.value}

# ==================== 记忆查看 ====================
@router.get("/memory/{user_id}")
async def get_memory(
    user_id: str,
    role_id: str = Query("kasumi", description="角色ID"),
    layer: Optional[str] = Query(None, description="l1|l2|l4|l5")
):
    """获取指定用户+角色的记忆"""
    result = {
        "user_id": user_id,
        "role_id": role_id,
        "layers": {}
    }
    
    # L1: 内存上下文
    if layer in ("l1", None):
        result["layers"]["l1"] = _get_l1_memory(user_id, role_id)
    
    # L2: 短期记忆
    if layer in ("l2", None):
        result["layers"]["l2"] = _get_l2_memory(user_id, role_id)
    
    # L4: 用户事实
    if layer in ("l4", None):
        result["layers"]["l4"] = _get_l4_facts(user_id, role_id)
    
    # L5: 角色事实
    if layer in ("l5", None):
        result["layers"]["l5"] = _get_l5_facts(role_id)
    
    return result

def _get_l1_memory(user_id: str, role_id: str) -> list:
    """获取 L1 内存上下文"""
    agent = agent_factory.get_agent(user_id, role_id)
    history = agent.conversation_history.get(user_id, [])
    return [
        {
            "role": msg.get("role"),
            "content": msg.get("content"),
            "index": i
        }
        for i, msg in enumerate(history)
    ]

def _get_l2_memory(user_id: str, role_id: str) -> list:
    """获取 L2 短期记忆"""
    results = memory.search(
        query="",
        user_id=user_id,
        role_id=role_id,
        threshold=0.1,
        n_results=50,
        where={"type": "short_term"}
    )
    return [
        {
            "id": item.get("id", ""),
            "content": item["document"],
            "timestamp": item["metadata"].get("timestamp"),
            "score": item.get("score")
        }
        for item in results.get("results", [])
    ]

def _get_l4_facts(user_id: str, role_id: str) -> list:
    """获取 L4 用户事实"""
    facts = memory.get_facts(user_id, role_id, n=20)
    return [
        {
            "id": f.get("id", ""),
            "fact": f["document"],
            "importance": f["metadata"].get("importance"),
            "category": f["metadata"].get("category"),
            "timestamp": f["metadata"].get("timestamp")
        }
        for f in facts
    ]

def _get_l5_facts(role_id: str) -> list:
    """获取 L5 角色事实（系统级）"""
    facts = memory.get_facts("system", role_id, n=20)
    return [
        {
            "id": f.get("id", ""),
            "fact": f["document"],
            "category": f["metadata"].get("category"),
            "timestamp": f["metadata"].get("timestamp")
        }
        for f in facts
    ]

# ==================== 记忆编辑 ====================
class FactCreate(BaseModel):
    user_id: str
    role_id: str
    fact: str
    category: str = "general"
    importance: float = 0.5

class FactUpdate(BaseModel):
    fact_id: str
    new_content: str

@router.delete("/memory/fact")
async def delete_fact(fact_id: str, user_id: str = Query(...), role_id: str = Query("kasumi")):
    """删除一条事实（L4 或 L5）"""
    # 根据 user_id 判断是 L4 还是 L5
    try:
        memory.collection.delete(ids=[fact_id])
        return {"status": "ok", "message": f"已删除事实 {fact_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/memory/fact")
async def update_fact(update: FactUpdate):
    """修改事实内容（仅 L4/L5）"""
    try:
        # 获取当前元数据
        results = memory.collection.get(ids=[update.fact_id])
        if not results or not results["documents"]:
            raise HTTPException(status_code=404, detail="事实不存在")
        metadata = results["metadatas"][0]
        # 更新文档内容，保留元数据
        memory.collection.update(
            ids=[update.fact_id],
            documents=[update.new_content],
            metadatas=[metadata]
        )
        return {"status": "ok", "message": "事实已更新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/memory/fact")
async def create_fact(fact: FactCreate):
    """手动新增一条事实（L4）"""
    try:
        from core.memory import SimpleMemory
        result = memory.add_with_title(
            title=fact.fact[:20],
            content=fact.fact,
            user_id=fact.user_id,
            meta={
                "type": "fact",
                "role_id": fact.role_id,
                "category": fact.category,
                "importance": fact.importance,
                "timestamp": datetime.now().isoformat()
            }
        )
        return {"status": "ok", "message": "事实已添加", "id": result.get("id")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== 情感状态 ====================
@router.get("/emotion/{user_id}")
async def get_emotion(user_id: str, role_id: str = Query("kasumi")):
    """获取用户当前情感状态"""
    try:
        results = memory.search(
            query="情感状态",
            user_id=user_id,
            role_id=role_id,
            threshold=0.3,
            n_results=1,
            where={"type": "emotion"}
        )
        if results and results["results"]:
            item = results["results"][0]
            meta = item.get("metadata", {})
            return {
                "user_id": user_id,
                "role_id": role_id,
                "emotion": {
                    "primary": meta.get("primary", "neutral"),
                    "intensity": float(meta.get("intensity", 0.5)),
                    "valence": float(meta.get("valence", 0.0)),
                    "description": meta.get("description", ""),
                    "timestamp": meta.get("timestamp")
                }
            }
        return {"user_id": user_id, "role_id": role_id, "emotion": None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== 系统统计 ====================
@router.get("/stats")
async def get_stats():
    """获取系统统计信息"""
    try:
        # 总记忆数
        all_data = memory.collection.get()
        total_memories = len(all_data["ids"]) if all_data else 0
        
        # 各层数量
        short_term = memory.collection.get(where={"type": "short_term"})
        facts = memory.collection.get(where={"type": "fact"})
        role_facts = memory.collection.get(where={"type": "role_fact"})
        emotions = memory.collection.get(where={"type": "emotion"})
        
        return {
            "total_memories": total_memories,
            "short_term_count": len(short_term["ids"]) if short_term else 0,
            "fact_count": len(facts["ids"]) if facts else 0,
            "role_fact_count": len(role_facts["ids"]) if role_facts else 0,
            "emotion_count": len(emotions["ids"]) if emotions else 0,
            "online_users": len(agent_factory._agents) if agent_factory else 0,
            "rooms": len(room_manager.list_rooms()) if room_manager else 0,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== 角色列表 ====================
@router.get("/roles")
async def list_roles():
    """列出所有已注册角色"""
    if role_pool:
        return [
            {"role_id": rid, "display_name": cfg.display_name}
            for rid, cfg in role_pool._role_configs.items()
        ]
    return [{"role_id": "kasumi", "display_name": "户山香澄"}]


@router.get("/affection/{user_id}")
async def get_affection(user_id: str, role_id: str = Query("kasumi")):
    """获取用户好感度"""
    try:
        results = memory.search(
            query="",
            user_id=user_id,
            role_id=role_id,
            threshold=0.3,
            n_results=1,
            where={"type": "affection"}
        )
        if results and results["results"]:
            item = results["results"][0]
            meta = item.get("metadata", {})
            return {
                "user_id": user_id,
                "role_id": role_id,
                "liking": float(meta.get("liking", 0.5)),
                "trust": float(meta.get("trust", 0.5)),
                "familiarity": float(meta.get("familiarity", 0.5)),
                "respect": float(meta.get("respect", 0.5)),
                "interest": float(meta.get("interest", 0.5)),
                "attachment": float(meta.get("attachment", 0.3)),
                "reason": meta.get("reason", ""),
                "timestamp": meta.get("timestamp")
            }
        return {"user_id": user_id, "role_id": role_id, "affection": None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))