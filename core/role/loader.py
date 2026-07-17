"""
L5 角色加载器
启动时：用 role_fact_extractor 分析 role_prompt.txt → 存入 ChromaDB
"""

import hashlib
from pathlib import Path
from typing import Optional

from .role_fact_extractor import extract_role_facts


def init_role_to_memory(role_prompt: str, role_id: str, tool_adapter, memory) -> bool:
    """
    初始化角色数据到 ChromaDB
    用 hash 判断是否需要重新提取
    
    Args:
        role_prompt: role_prompt.txt 完整内容
        role_id: 角色唯一标识
        tool_adapter: LLM 适配器（用于分析 prompt）
        memory: SimpleMemory 实例
    
    Returns:
        是否成功
    """
    # 计算 prompt hash
    prompt_hash = hashlib.md5(role_prompt.encode()).hexdigest()
    
    # 检查是否已存在（通过 hash 判断 prompt 是否变更）
    existing = memory.collection.get(
        where={"$and": [{"role_id": role_id}, {"type": "role_fact"}]}
    )
    if existing and existing["ids"] and existing["metadatas"]:
        stored_hash = existing["metadatas"][0].get("prompt_hash", "")
        if stored_hash == prompt_hash:
            print(f"[RoleLoader] 角色事实已是最新: {role_id}，跳过")
            return True
    
    # 提取角色事实
    facts = extract_role_facts(role_prompt, tool_adapter)
    if not facts:
        print(f"[RoleLoader] 角色事实提取失败: {role_id}")
        return False
    
    # 存储到 ChromaDB
    for i, fact in enumerate(facts):
        fact_id = f"role_fact_{role_id}_{i}"
        memory.collection.add(
            ids=[fact_id],
            documents=[fact["fact"]],
            metadatas=[{
                "type": "role_fact",
                "role_id": role_id,
                "category": fact.get("category", "other"),
                "prompt_hash": prompt_hash,
            }]
        )
    
    print(f"[RoleLoader] 角色初始化完成: {role_id}, {len(facts)} 条事实")
    return True