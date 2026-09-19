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
    # Source-provided popularity prior, 0-100. Only the curated annual source
    # sets this (a parade that draws 100k is knowably bigger than a bar show);
    # scraped sources leave it at 0 and let the ranker infer from signals.
    notability: int = 0
    # True when the date comes from a typical-timing rule rather than an
    # announced date - the UI flags these so nobody books travel on one.
    date_approx: bool = False
    # Event artwork, largest first. Only sources that publish images per
    # event fill this (Funcheap enclosures, DoTheBay cover images); the rest
    # leave it empty rather than substituting a stock picture.
    images: list[str] = field(default_factory=list)

    @property
    def is_free(self) -> bool:
        return self.cost.strip() == "0"
