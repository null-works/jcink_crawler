import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import aiosqlite

from app.config import settings
from app.services.crawler import (
    crawl_character_threads,
    crawl_character_profile,
    check_profile_exists,
    sync_posts_from_acp,
    crawl_quotes_only,
    crawl_recent_threads,
)
from app.services.activity import set_activity, clear_activity, log_debug


_scheduler: AsyncIOScheduler | None = None

MAX_CONSECUTIVE_MISSES = 20


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
    """Crawl every account by iterating showuser=1, 2, 3...

    No discovery step — just walks user IDs sequentially.  For each ID:
    lightweight httpx check first, then full Playwright profile crawl +
    thread/quote crawl for valid accounts.  Stops after 20 consecutive
    misses (deleted/banned profiles).

    When ACP is available, runs a bulk sync first for post dates.
    """
    excluded = settings.excluded_name_set

    # ── Phase 1: ACP bulk sync (if available) ──
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

    # ── Phase 2: Walk user IDs sequentially ──
    consecutive_misses = 0
    processed = 0
    user_id = 0

    log_debug(f"Starting sequential ID crawl (stop after {MAX_CONSECUTIVE_MISSES} consecutive misses)")

    while consecutive_misses < MAX_CONSECUTIVE_MISSES:
        user_id += 1
        sid = str(user_id)

        set_activity(
            f"Checking ID {sid} ({consecutive_misses} misses)",
            character_id=sid,
        )

        # Quick httpx check — skips board-message / deleted accounts fast
        name = await check_profile_exists(sid)
        if name is None:
            consecutive_misses += 1
            log_debug(f"ID {sid}: no profile (miss {consecutive_misses}/{MAX_CONSECUTIVE_MISSES})")
            continue

        # Valid profile — reset miss counter
        consecutive_misses = 0

        if name.lower() in excluded:
            log_debug(f"ID {sid}: {name} (excluded)")
            continue

        processed += 1

        # ── Full profile crawl (Playwright for power grid) ──
        set_activity(
            f"({processed}) Profile: {name}",
            character_id=sid,
            character_name=name,
        )
        try:
            await crawl_character_profile(sid, settings.database_path)
        except Exception as e:
            log_debug(f"Error crawling profile for {name} ({sid}): {e}", level="error")

        # ── Threads + quotes ──
        set_activity(
            f"({processed}) Threads: {name}",
            character_id=sid,
            character_name=name,
        )
        try:
            await crawl_character_threads(sid, settings.database_path)
        except Exception as e:
            log_debug(f"Error crawling threads for {name} ({sid}): {e}", level="error")

        # Gap between characters — lets search cooldown expire
        await asyncio.sleep(15)

    clear_activity()
    log_debug(
        f"Full crawl complete: checked {user_id} IDs, {processed} characters processed",
        level="done",
    )


async def _sync_posts_acp():
    """Sync post data from JCink Admin CP SQL dump."""
    log_debug("Starting scheduled ACP post sync")
    try:
        result = await sync_posts_from_acp(settings.database_path)
        log_debug(f"ACP sync complete: {result}", level="done")
    except Exception as e:
        log_debug(f"Error during ACP sync: {e}", level="error")


def start_scheduler():
    """Start the APScheduler with configured intervals."""
    global _scheduler
    _scheduler = AsyncIOScheduler()

    _scheduler.add_job(
        _crawl_all_characters,
        trigger=IntervalTrigger(minutes=settings.crawl_threads_interval_minutes),
        id="crawl_all_characters",
        name="Full crawl: iterate all user IDs",
        replace_existing=True,
        next_run_time=None,  # Startup task handles the first run
    )

    _scheduler.start()

    # Kick off full crawl on startup
    asyncio.get_running_loop().create_task(_crawl_all_characters())

    log_debug(f"Scheduler started - full crawl every {settings.crawl_threads_interval_minutes}min")


def stop_scheduler():
    """Stop the scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
        _scheduler = None
        log_debug("Scheduler stopped")
