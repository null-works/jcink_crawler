import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import aiosqlite

from app.config import settings
from app.services.crawler import (
    crawl_character_threads,
    crawl_character_profile,
    check_profile_exists,
)
from app.services.activity import set_activity, clear_activity, log_debug


_scheduler: AsyncIOScheduler | None = None

MAX_CONSECUTIVE_MISSES = 100


async def _clear_quote_crawl_log():
    """Wipe stale quote_crawl_log entries on startup.

    Previous ACP syncs may have marked (thread, character) pairs as
    quote-scraped even though no quotes were actually extracted from
    the raw BBCode.  Clearing the log lets the HTML crawl pass
    re-extract everything cleanly.
    """
    try:
        async with aiosqlite.connect(settings.database_path) as db:
            await db.execute("DELETE FROM quote_crawl_log")
            await db.commit()
        log_debug("Cleared quote_crawl_log for fresh extraction", level="done")
    except Exception as e:
        log_debug(f"Error clearing quote_crawl_log: {e}", level="error")


async def _crawl_all_characters():
    """Crawl every account by iterating showuser=1, 2, 3...

    Pure HTML crawling — no ACP dependency.  For each user ID:
    lightweight httpx check first, then full Playwright profile crawl +
    thread/quote crawl for valid accounts.  Stops after 100 consecutive
    misses (deleted/banned profiles).
    """
    excluded = settings.excluded_name_set
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

    # Clear stale quote log then start the full crawl
    async def _startup():
        await _clear_quote_crawl_log()
        await _crawl_all_characters()

    asyncio.get_running_loop().create_task(_startup())

    log_debug(f"Scheduler started - full crawl every {settings.crawl_threads_interval_minutes}min")


def stop_scheduler():
    """Stop the scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
        _scheduler = None
        log_debug("Scheduler stopped")
