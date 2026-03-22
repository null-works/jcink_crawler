from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, Response
import aiosqlite

from app.database import get_db
from app.config import settings
from app.services.activity import log_debug
from app.models import (
    CharacterSummary,
    ClaimsSummary,
    CharacterThreads,
    CharacterProfile,
    Quote,
    CrawlStatusResponse,
    CharacterRegister,
    CrawlTrigger,
    WebhookActivity,
    get_character,
    get_all_characters,
    get_all_claims,
    get_character_threads,
    get_profile_fields,
    get_characters_fields_batch,
    get_random_quote,
    get_all_quotes,
    get_quote_count,
    get_thread_counts,
)
from app.services import (
    crawl_character_threads,
    crawl_character_profile,
    register_character,
)
from app.models.operations import get_crawl_status, set_crawl_status, record_user_activity, get_recent_users
from app.services.crawler import crawl_single_thread, sync_posts_from_acp, crawl_quotes_only
from app.services.scheduler import _crawl_all_characters, _crawl_all_profiles
from app.services.activity import get_activity

# Default crawl intervals (minutes)
SCHEDULE_DEFAULTS = {
    "sync_interval": 30,
    "quote_interval": 30,
    "profile_interval": 120,
    "discovery_interval": 1440,
}

router = APIRouter()


# --- Character Endpoints ---

@router.get("/characters", response_model=list[CharacterSummary])
async def list_characters(db: aiosqlite.Connection = Depends(get_db)):
    """List all tracked characters with thread counts."""
    return await get_all_characters(db)


@router.get("/claims", response_model=list[ClaimsSummary])
async def list_claims(db: aiosqlite.Connection = Depends(get_db)):
    """Bulk endpoint for the claims page.

    Returns ALL characters with claims-specific profile fields
    (face_claim, species, codename, alias, affiliation, connections)
    and thread counts in a single response.
    """
    return await get_all_claims(db)


@router.get("/characters/fields", response_model=dict[str, dict[str, str]])
async def get_batch_fields(
    ids: str = Query(..., description="Comma-separated character IDs"),
    fields: str | None = Query(None, description="Comma-separated field keys to return"),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Batch-fetch profile fields for multiple characters.

    Returns {character_id: {field_key: field_value}} for each requested ID.
    If `fields` is omitted, returns all fields for each character.
    """
    character_ids = [cid.strip() for cid in ids.split(",") if cid.strip()]
    if not character_ids:
        return {}
    field_keys = (
        [f.strip() for f in fields.split(",") if f.strip()]
        if fields
        else None
    )
    return await get_characters_fields_batch(db, character_ids, field_keys)


@router.get("/character/{character_id}", response_model=CharacterProfile)
async def get_character_detail(
    character_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get full character profile including fields and thread data."""
    char = await get_character(db, character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    fields = await get_profile_fields(db, character_id)
    threads = await get_character_threads(db, character_id)

    return CharacterProfile(
        character=char,
        fields=fields,
        threads=threads,
    )


@router.post("/character/register", response_model=dict)
async def register_new_character(
    data: CharacterRegister,
    background_tasks: BackgroundTasks,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Register a character for tracking.

    Fetches their profile and kicks off initial thread crawl in background.
    """
    # Check if already registered
    existing = await get_character(db, data.user_id)
    if existing:
        return {
            "status": "already_registered",
            "character": existing.model_dump(),
        }

    # Queue full registration (profile + threads) as background task
    background_tasks.add_task(register_character, data.user_id, settings.database_path)

    return {
        "status": "registering",
        "character_id": data.user_id,
        "message": "Profile and thread crawl started in background",
    }


# --- Thread Endpoints ---

@router.get("/character/{character_id}/threads", response_model=CharacterThreads)
async def get_threads(
    character_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get all threads for a character, categorized."""
    char = await get_character(db, character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    return await get_character_threads(db, character_id)


@router.get("/character/{character_id}/thread-counts", response_model=dict)
async def get_counts(
    character_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get just the thread counts for a character (lightweight endpoint)."""
    char = await get_character(db, character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    return await get_thread_counts(db, character_id)


# --- Quote Endpoints ---

@router.get("/character/{character_id}/quote", response_model=Quote | None)
async def get_character_random_quote(
    character_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get a random quote for a character."""
    return await get_random_quote(db, character_id)


@router.get("/character/{character_id}/quotes", response_model=list[Quote])
async def get_character_all_quotes(
    character_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get all quotes for a character."""
    return await get_all_quotes(db, character_id)


@router.get("/character/{character_id}/quote-count", response_model=dict)
async def get_character_quote_count(
    character_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get total quote count for a character."""
    count = await get_quote_count(db, character_id)
    return {"character_id": character_id, "count": count}


# --- Webhook Endpoint ---

@router.post("/webhook/activity", status_code=202)
async def webhook_activity(
    data: WebhookActivity,
    background_tasks: BackgroundTasks,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Receive activity webhooks from the theme for targeted re-crawls.

    Accepts new_post, new_topic, and profile_edit events.
    Acknowledges immediately (202) and processes asynchronously.
    """
    log_debug(
        f"Webhook received: event={data.event} thread_id={data.thread_id} "
        f"forum_id={data.forum_id} user_id={data.user_id}",
        level="webhook",
    )

    # Record user activity for the "online recently" feature
    if data.user_id:
        cursor = await db.execute(
            "SELECT name FROM characters WHERE id = ?", (data.user_id,)
        )
        row = await cursor.fetchone()
        user_name = row["name"] if row else f"User {data.user_id}"
        await record_user_activity(db, data.user_id, user_name, source="webhook")

    if data.event == "profile_edit" and data.user_id:
        background_tasks.add_task(
            crawl_character_profile, data.user_id, settings.database_path
        )
        return {"status": "accepted", "action": "profile_recrawl", "user_id": data.user_id}

    if data.event in ("new_post", "new_topic"):
        if data.user_id:
            # Full character crawl — refreshes entire thread tracker
            # (last poster, avatars, excerpts) instead of targeting one thread.
            background_tasks.add_task(
                crawl_character_threads, data.user_id, settings.database_path
            )
            return {"status": "accepted", "action": "character_recrawl", "user_id": data.user_id}
        elif data.thread_id:
            # Fallback: crawl just this thread if no user_id provided
            background_tasks.add_task(
                crawl_single_thread,
                data.thread_id,
                settings.database_path,
                forum_id=data.forum_id,
            )
            return {"status": "accepted", "action": "thread_recrawl", "thread_id": data.thread_id}

    log_debug(
        f"Webhook dropped: event={data.event} — no actionable data",
        level="warn",
    )
    return {"status": "accepted", "action": "none"}


# --- Online/Recent Endpoint ---

@router.get("/online/recent")
async def get_online_recent(
    hours: int = Query(default=6, ge=1, le=48, description="How far back to look (max 48)"),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Return users active within a rolling window, most recent first."""
    return await get_recent_users(db, hours)


# --- Admin/Crawl Endpoints ---

@router.post("/crawl/trigger", response_model=dict)
async def trigger_crawl(
    data: CrawlTrigger,
    background_tasks: BackgroundTasks,
):
    """Manually trigger a crawl for a character."""
    if data.crawl_type in ("discover", "all-threads"):
        background_tasks.add_task(_crawl_all_characters)
        return {"status": "crawl_queued", "character_id": None, "crawl_type": "all-characters"}

    if data.crawl_type in ("all-profiles", "profiles"):
        background_tasks.add_task(_crawl_all_profiles)
        return {"status": "crawl_queued", "character_id": None, "crawl_type": "all-profiles"}

    if data.crawl_type == "sync-posts":
        background_tasks.add_task(sync_posts_from_acp, settings.database_path)
        return {"status": "crawl_queued", "character_id": None, "crawl_type": "sync-posts"}

    if data.crawl_type == "crawl-quotes":
        background_tasks.add_task(crawl_quotes_only, settings.database_path)
        return {"status": "crawl_queued", "character_id": None, "crawl_type": "crawl-quotes"}

    if not data.character_id:
        raise HTTPException(status_code=422, detail="character_id is required for threads/profile crawls")

    if data.crawl_type == "threads":
        background_tasks.add_task(
            crawl_character_threads, data.character_id, settings.database_path
        )
    elif data.crawl_type == "profile":
        background_tasks.add_task(
            crawl_character_profile, data.character_id, settings.database_path
        )
    else:
        raise HTTPException(status_code=400, detail="Invalid crawl_type. Use 'threads', 'profile', 'discover', 'all-profiles', 'sync-posts', or 'crawl-quotes'")

    return {
        "status": "crawl_queued",
        "character_id": data.character_id,
        "crawl_type": data.crawl_type,
    }


@router.get("/crawl/schedule")
async def get_crawl_schedule(db: aiosqlite.Connection = Depends(get_db)):
    """Get current crawl schedule intervals (in minutes)."""
    result = {}
    for key, default in SCHEDULE_DEFAULTS.items():
        val = await get_crawl_status(db, f"schedule_{key}")
        result[key] = int(val) if val else default
    return result


@router.post("/crawl/schedule")
async def set_crawl_schedule(
    data: dict,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Set crawl schedule intervals (in minutes)."""
    updated = {}
    for key in SCHEDULE_DEFAULTS:
        if key in data:
            val = max(1, int(data[key]))  # minimum 1 minute
            await set_crawl_status(db, f"schedule_{key}", str(val))
            updated[key] = val
    return {"status": "ok", "updated": updated}


@router.get("/status", response_model=CrawlStatusResponse)
async def get_service_status(db: aiosqlite.Connection = Depends(get_db)):
    """Get overall service status."""
    # Character count (excluding filtered names)
    excluded = settings.excluded_name_set
    cursor = await db.execute("SELECT name FROM characters")
    all_names = await cursor.fetchall()
    char_count = sum(1 for r in all_names if r["name"].lower() not in excluded)

    # Thread count
    cursor = await db.execute("SELECT COUNT(*) as count FROM threads")
    row = await cursor.fetchone()
    thread_count = row["count"] if row else 0

    # Quote count
    cursor = await db.execute("SELECT COUNT(*) as count FROM quotes")
    row = await cursor.fetchone()
    quote_count = row["count"] if row else 0

    # Last crawl times
    cursor = await db.execute(
        "SELECT MAX(last_thread_crawl) as last FROM characters"
    )
    row = await cursor.fetchone()
    last_thread = row["last"] if row else None

    cursor = await db.execute(
        "SELECT MAX(last_profile_crawl) as last FROM characters"
    )
    row = await cursor.fetchone()
    last_profile = row["last"] if row else None

    activity = get_activity()

    return CrawlStatusResponse(
        characters_tracked=char_count,
        total_threads=thread_count,
        total_quotes=quote_count,
        last_thread_crawl=last_thread,
        last_profile_crawl=last_profile,
        current_activity=activity if activity["active"] else None,
    )
