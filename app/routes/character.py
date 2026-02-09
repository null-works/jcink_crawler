from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
import aiosqlite

from app.database import get_db
from app.config import settings
from app.models import (
    CharacterSummary,
    CharacterThreads,
    CharacterProfile,
    Quote,
    CrawlStatusResponse,
    CharacterRegister,
    CrawlTrigger,
    get_character,
    get_all_characters,
    get_character_threads,
    get_profile_fields,
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
from app.services.crawler import discover_characters
from app.services.scheduler import _crawl_all_threads, _crawl_all_profiles
from app.services.activity import get_activity

router = APIRouter()


# --- Character Endpoints ---

@router.get("/characters", response_model=list[CharacterSummary])
async def list_characters(db: aiosqlite.Connection = Depends(get_db)):
    """List all tracked characters with thread counts."""
    return await get_all_characters(db)


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


# --- Admin/Crawl Endpoints ---

@router.post("/crawl/trigger", response_model=dict)
async def trigger_crawl(
    data: CrawlTrigger,
    background_tasks: BackgroundTasks,
):
    """Manually trigger a crawl for a character."""
    if data.crawl_type == "discover":
        background_tasks.add_task(
            discover_characters, settings.database_path
        )
        return {"status": "crawl_queued", "character_id": None, "crawl_type": "discover"}

    if data.crawl_type == "all-threads":
        background_tasks.add_task(_crawl_all_threads)
        return {"status": "crawl_queued", "character_id": None, "crawl_type": "all-threads"}

    if data.crawl_type == "all-profiles":
        background_tasks.add_task(_crawl_all_profiles)
        return {"status": "crawl_queued", "character_id": None, "crawl_type": "all-profiles"}

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
        raise HTTPException(status_code=400, detail="Invalid crawl_type. Use 'threads', 'profile', or 'discover'")

    return {
        "status": "crawl_queued",
        "character_id": data.character_id,
        "crawl_type": data.crawl_type,
    }


@router.get("/status", response_model=CrawlStatusResponse)
async def get_service_status(db: aiosqlite.Connection = Depends(get_db)):
    """Get overall service status."""
    # Character count
    cursor = await db.execute("SELECT COUNT(*) as count FROM characters")
    row = await cursor.fetchone()
    char_count = row["count"] if row else 0

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
