from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class InfoItem:
    id: str
    user_id: str
    title: str
    content: str
    source: str  # "rss" | "search"
    url: Optional[str] = None
    interest_score: float = 0.0
    timestamp: datetime = None
    expires_at: datetime = None
    shared: bool = False

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()
        if self.expires_at is None:
            self.expires_at = self.timestamp.replace(day=self.timestamp.day + 7)