"""Lightweight in-memory crawl activity state.

Shared between the crawler (writes) and the API (reads).
Ephemeral — resets on restart, which is fine since the TUI polls frequently.
"""

from datetime import datetime, timezone

_state: dict = {
    "active": False,
    "activity": "",
    "character_id": None,
    "character_name": None,
    "started_at": None,
}


def set_activity(activity: str, character_id: str | None = None, character_name: str | None = None) -> None:
    _state["active"] = True
    _state["activity"] = activity
    _state["character_id"] = character_id
    _state["character_name"] = character_name
    _state["started_at"] = datetime.now(timezone.utc).isoformat()


def clear_activity() -> None:
    _state["active"] = False
    _state["activity"] = ""
    _state["character_id"] = None
    _state["character_name"] = None
    _state["started_at"] = None


def get_activity() -> dict:
    return dict(_state)
