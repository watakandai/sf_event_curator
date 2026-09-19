from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Event:
    source: str
    source_id: str  # stable dedupe key within a source (e.g. the item's link)
    title: str
    start: datetime | None
    end: datetime | None
    venue: str = ""
    address: str = ""
    cost: str = ""
    categories: list[str] = field(default_factory=list)
    url: str = ""
    description: str = ""

    @property
    def is_free(self) -> bool:
        return self.cost.strip() == "0"
