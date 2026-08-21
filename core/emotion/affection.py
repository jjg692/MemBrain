"""
好感度系统（6 维度，跨会话持久化，存 ChromaDB type=affection）
- liking     喜欢程度
- trust      信任程度
- familiarity 熟悉程度
- respect    尊重程度
- interest   兴趣程度
- attachment 依恋程度
所有值范围 0.0-1.0
"""
from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass
class AffectionState:
    liking: float = 0.5
    trust: float = 0.5
    familiarity: float = 0.5
    respect: float = 0.5
    interest: float = 0.5
    attachment: float = 0.3  # 初始较低，需时间积累
    updated_at: str = ""

    def __post_init__(self):
        if not self.updated_at:
            self.updated_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "AffectionState":
        def clamp(v, default):
            try:
                return max(0.0, min(1.0, float(v)))
            except (TypeError, ValueError):
                return default
        return cls(
            liking=clamp(data.get("liking"), 0.5),
            trust=clamp(data.get("trust"), 0.5),
            familiarity=clamp(data.get("familiarity"), 0.5),
            respect=clamp(data.get("respect"), 0.5),
            interest=clamp(data.get("interest"), 0.5),
            attachment=clamp(data.get("attachment"), 0.3),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
        )

    @classmethod
    def default(cls) -> "AffectionState":
        return cls()
