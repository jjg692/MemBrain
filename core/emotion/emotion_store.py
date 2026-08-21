"""
情感与好感度的持久化（存 ChromaDB）
"""
from typing import Optional

from core.memory.vector_store import SimpleMemory
from core.emotion.affection import AffectionState
from core.emotion.emotion import EmotionState


class EmotionStore:
    def __init__(self, memory: SimpleMemory):
        self.memory = memory

    def save_emotion(self, user_id: str, role_id: str, state: EmotionState):
        self._delete_type(user_id, role_id, "emotion")
        self.memory.add_with_title(
            title=f"emotion_{user_id}_{role_id}",
            content=state.primary,
            user_id=user_id,
            role_id=role_id,
            type_="emotion",
            meta=state.to_dict(),
        )

    def load_emotion(self, user_id: str, role_id: str) -> Optional[EmotionState]:
        res = self.memory.get(
            where=self.memory._build_where(
                {"user_id": user_id}, {"role_id": role_id}, {"type": "emotion"}
            ),
            limit=1,
        )
        if res["results"]:
            meta = res["results"][0]["metadata"]
            if meta.get("primary"):
                return EmotionState.from_dict(meta)
        return None

    def save_affection(self, user_id: str, role_id: str, state: AffectionState):
        self._delete_type(user_id, role_id, "affection")
        self.memory.add_with_title(
            title=f"affection_{user_id}_{role_id}",
            content=json_safe(state.to_dict()),
            user_id=user_id,
            role_id=role_id,
            type_="affection",
            meta=state.to_dict(),
        )

    def load_affection(self, user_id: str, role_id: str) -> Optional[AffectionState]:
        res = self.memory.get(
            where=self.memory._build_where(
                {"user_id": user_id}, {"role_id": role_id}, {"type": "affection"}
            ),
            limit=1,
        )
        if res["results"]:
            meta = res["results"][0]["metadata"]
            try:
                return AffectionState.from_dict(meta)
            except Exception:
                return None
        return None

    def _delete_type(self, user_id: str, role_id: str, type_: str):
        res = self.memory.get(
            where=self.memory._build_where(
                {"user_id": user_id}, {"role_id": role_id}, {"type": type_}
            ),
            limit=10,
        )
        for item in res["results"]:
            if item["id"]:
                self.memory.delete(item["id"])


def json_safe(d: dict) -> str:
    import json
    return json.dumps(d, ensure_ascii=False)
