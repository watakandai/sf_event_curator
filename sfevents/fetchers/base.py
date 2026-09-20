from __future__ import annotations
from typing import Protocol

from ..models import Event


class Fetcher(Protocol):
    name: str

    def fetch(self) -> list[Event]:
        """Fetch and return the current set of events from this source."""
        ...
