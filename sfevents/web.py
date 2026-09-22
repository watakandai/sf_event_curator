from __future__ import annotations
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .models import Event

DB_PATH = Path(os.environ.get("SFEVENTS_DB", Path.home() / ".sfevents" / "events.db"))
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db.init_db(DB_PATH)
    yield


app = FastAPI(title="SF Event Curator", lifespan=lifespan)


class EventIn(BaseModel):
    title: str
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    venue: str = ""
    address: str = ""
    cost: str = ""
    categories: list[str] = Field(default_factory=list)
    url: str = ""
    description: str = ""


class EventUpdate(BaseModel):
    title: Optional[str] = None
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    venue: Optional[str] = None
    address: Optional[str] = None
    cost: Optional[str] = None
    categories: Optional[list[str]] = None
    url: Optional[str] = None
    description: Optional[str] = None


@app.get("/api/events")
def list_events(
    start_after: Optional[datetime] = None,
    start_before: Optional[datetime] = None,
    free_only: bool = False,
    category: Optional[str] = None,
):
    rows = db.query_events(
        DB_PATH, start_after=start_after, start_before=start_before,
        free_only=free_only, category=category,
    )
    return [db.row_to_dict(r) for r in rows]


@app.get("/api/events/{event_id}")
def get_event(event_id: int):
    row = db.get_event(DB_PATH, event_id)
    if row is None:
        raise HTTPException(404, "event not found")
    return db.row_to_dict(row)


@app.post("/api/events", status_code=201)
def create_event(payload: EventIn):
    event = Event(
        source="manual",
        source_id=str(uuid.uuid4()),
        title=payload.title,
        start=payload.start,
        end=payload.end,
        venue=payload.venue,
        address=payload.address,
        cost=payload.cost,
        categories=payload.categories,
        url=payload.url,
        description=payload.description,
    )
    row = db.insert_manual_event(DB_PATH, event)
    return db.row_to_dict(row)


@app.patch("/api/events/{event_id}")
def patch_event(event_id: int, payload: EventUpdate):
    if db.get_event(DB_PATH, event_id) is None:
        raise HTTPException(404, "event not found")
    fields = payload.model_dump(exclude_unset=True)
    if "start" in fields:
        fields["start_ts"] = fields.pop("start").isoformat() if fields["start"] else None
    if "end" in fields:
        fields["end_ts"] = fields.pop("end").isoformat() if fields["end"] else None
    if "categories" in fields:
        fields["categories"] = ",".join(fields["categories"])
    row = db.update_event(DB_PATH, event_id, **fields)
    return db.row_to_dict(row)


@app.delete("/api/events/{event_id}", status_code=204)
def remove_event(event_id: int):
    if db.get_event(DB_PATH, event_id) is None:
        raise HTTPException(404, "event not found")
    db.delete_event(DB_PATH, event_id)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
