import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import aiosqlite

from app.config import settings
from app.services.crawler import crawl_character_threads, crawl_character_profile, discover_characters, sync_posts_from_acp, crawl_quotes_only, crawl_recent_threads
from app.services.activity import set_activity, clear_activity, log_debug


_scheduler: AsyncIOScheduler | None = None


async def _acp_available() -> bool:
    """Check if ACP credentials are configured (env or DB)."""
    if settings.admin_username and settings.admin_password:
        return True
    # Check DB for dashboard-saved credentials
    try:
        from app.models.operations import get_crawl_status
        async with aiosqlite.connect(settings.database_path) as db:
            db.row_factory = aiosqlite.Row
            user = await get_crawl_status(db, "acp_username")
            pwd = await get_crawl_status(db, "acp_password")
            return bool(user and pwd)
    except Exception:
        return False


async def _crawl_all_characters():
    """Crawl all characters sequentially in account ID order.

    For each character: profile first, then threads (which extracts quotes).
    The natural gap between characters (~15s) handles JCink search cooldown.

    When ACP is available, runs an ACP sync first for bulk data, then does
    a per-character HTML pass for quotes and any data the ACP missed.
    """
    excluded = settings.excluded_name_set

    # ── Phase 1: ACP bulk sync (if available) ──
    # Gets threads, posts, dates in one shot. Quote extraction from ACP post
    # bodies is attempted but the HTML pass below catches what ACP misses.
    if await _acp_available():
        log_debug("ACP configured — running bulk sync first")
        try:
            result = await sync_posts_from_acp(settings.database_path)
            if "error" not in result:
                log_debug(f"ACP sync succeeded: {result}", level="done")
            else:
                log_debug(f"ACP sync failed: {result.get('error')}", level="error")
        except Exception as e:
            log_debug(f"ACP sync error: {e}", level="error")

    # ── Phase 2: Per-character crawl in ID order ──
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id, name FROM characters ORDER BY CAST(id AS INTEGER) ASC")
        all_chars = await cursor.fetchall()

    characters = [c for c in all_chars if c["name"].lower() not in excluded]
    if not characters:
        log_debug("No characters to crawl")
        return

    total = len(characters)
    log_debug(f"Crawling {total} characters in ID order")

    for i, char in enumerate(characters, 1):
        cid = char["id"]
        cname = char["name"]

        # ── Profile ──
        set_activity(
            f"({i}/{total}) Profile: {cname}",
            character_id=cid,
            character_name=cname,
        )
        try:
            await crawl_character_profile(cid, settings.database_path)
        except Exception as e:
            log_debug(f"Error crawling profile for {cname} ({cid}): {e}", level="error")

        # ── Threads + quotes ──
        set_activity(
            f"({i}/{total}) Threads: {cname}",
            character_id=cid,
            character_name=cname,
        )
        try:
            await crawl_character_threads(cid, settings.database_path)
        except Exception as e:
            log_debug(f"Error crawling threads for {cname} ({cid}): {e}", level="error")

        # Gap between characters — lets search cooldown expire
        if i < total:
            await asyncio.sleep(15)

    clear_activity()
    log_debug(f"Full crawl complete: {total} characters processed", level="done")


async def _sync_posts_acp():
    """Sync post data from JCink Admin CP SQL dump."""
    log_debug("Starting scheduled ACP post sync")
    try:
        result = await sync_posts_from_acp(settings.database_path)
        log_debug(f"ACP sync complete: {result}", level="done")
    except Exception as e:
        log_debug(f"Error during ACP sync: {e}", level="error")


async def _discover_all_characters():
    """Auto-discover new characters from the forum member list."""
    log_debug("Starting scheduled character discovery")
    try:
        result = await discover_characters(settings.database_path)
        log_debug(f"Discovery complete: {result}", level="done")
    except Exception as e:
        log_debug(f"Error during character discovery: {e}", level="error")


async def _startup_sequence():
    """Run discovery then a full character crawl on startup.

    Sequenced to avoid concurrent forum requests that trigger flood control.
    """
    await _discover_all_characters()
    log_debug("Discovery done — starting full character crawl")
    await _crawl_all_characters()


def start_scheduler():
    """Start the APScheduler with configured intervals."""
    global _scheduler
    _scheduler = AsyncIOScheduler()

    _scheduler.add_job(
        _discover_all_characters,
        trigger=IntervalTrigger(minutes=settings.crawl_discovery_interval_minutes),
        id="discover_characters",
        name="Auto-discover characters from member list",
        replace_existing=True,
        next_run_time=None,  # Startup handled separately
    )

    _scheduler.add_job(
        _crawl_all_characters,
        trigger=IntervalTrigger(minutes=settings.crawl_threads_interval_minutes),
        id="crawl_all_characters",
        name="Full crawl: profile + threads + quotes per character",
        replace_existing=True,
        next_run_time=None,  # Startup handled separately
    )

    _scheduler.start()

    # Run discovery → full crawl sequentially on startup
    asyncio.get_running_loop().create_task(_startup_sequence())

    log_debug(f"Scheduler started - discovery every {settings.crawl_discovery_interval_minutes}min, full crawl every {settings.crawl_threads_interval_minutes}min")


def stop_scheduler():
    """Stop the scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
        _scheduler = None
        log_debug("Scheduler stopped")
