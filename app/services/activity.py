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

_debug_log: list[dict] = []
MAX_DEBUG_LOG = 500


def log_debug(message: str, level: str = "info") -> None:
    """Append a message to the in-memory debug log.

    Also prints to stdout for container logs.
    """
    now = datetime.now(timezone.utc)
    _debug_log.append({
        "time": now.strftime("%H:%M:%S"),
        "timestamp": now.isoformat(),
        "level": level,
        "message": message,
    })
    if len(_debug_log) > MAX_DEBUG_LOG:
        _debug_log[:] = _debug_log[-MAX_DEBUG_LOG:]
    print(f"[{level.upper()}] {message}")


def set_activity(activity: str, character_id: str | None = None, character_name: str | None = None) -> None:
    _state["active"] = True
    _state["activity"] = activity
    _state["character_id"] = character_id
    _state["character_name"] = character_name
    _state["started_at"] = datetime.now(timezone.utc).isoformat()
    log_debug(activity, level="activity")


def clear_activity() -> None:
    prev = _state["activity"]
    _state["active"] = False
    _state["activity"] = ""
    _state["character_id"] = None
    _state["character_name"] = None
    _state["started_at"] = None
    if prev:
        log_debug(f"Completed: {prev}", level="done")


def get_activity() -> dict:
    return dict(_state)


def get_debug_log() -> list[dict]:
    return list(_debug_log)


def clear_debug_log() -> None:
    _debug_log.clear()
