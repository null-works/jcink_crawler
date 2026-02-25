import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import aiosqlite

from app.config import settings
from app.services.crawler import (
    crawl_character_threads,
    crawl_character_profile,
    crawl_quotes_only,
    check_profile_exists,
    sync_posts_from_acp,
)
from app.services.activity import set_activity, clear_activity, log_debug


_scheduler: AsyncIOScheduler | None = None
_startup_task: asyncio.Task | None = None

MAX_CONSECUTIVE_MISSES = 100


def _has_acp_credentials() -> bool:
    """Check if ACP admin credentials are configured."""
    return bool(settings.admin_username and settings.admin_password)


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


async def _acp_sync_cycle():
    """Primary data sync using ACP SQL dump.

    This is MUCH faster than HTML scraping for post counting and thread
    tracking because it gets ALL data in a single database dump rather
    than fetching hundreds of individual pages.

    Only dumps the specific tables we need (topics, posts, forums, members)
    matching the approach in databaseParser.js.

    After the ACP sync, runs a quote-only HTML crawl pass for threads
    that haven't been quote-scraped yet.
    """
    log_debug("Starting ACP sync cycle")

    try:
        result = await sync_posts_from_acp(settings.database_path)
        if "error" in result:
            log_debug(f"ACP sync failed: {result['error']}", level="error")
            return
        log_debug(
            f"ACP sync complete: {result.get('threads_upserted', 0)} threads, "
            f"{result.get('posts_stored', 0)} posts, "
            f"{result.get('character_links', 0)} character links",
            level="done",
        )
    except Exception as e:
        log_debug(f"ACP sync cycle error: {e}", level="error")
        return

    # Follow up with quote extraction for unscraped threads
    try:
        quote_result = await crawl_quotes_only(settings.database_path)
        quotes_added = quote_result.get("quotes_added", 0)
        if quotes_added:
            log_debug(f"Quote pass: {quotes_added} new quotes", level="done")
    except Exception as e:
        log_debug(f"Quote crawl error: {e}", level="error")


async def _discover_and_crawl_profiles():
    """Discover new characters and crawl all profiles.

    Iterates user IDs 1, 2, 3... to find characters, then does a
    Playwright profile crawl for each. Does NOT crawl threads — that's
    handled by _acp_sync_cycle when ACP is available, or by
    _crawl_all_characters_html as fallback.
    """
    excluded = settings.excluded_name_set
    consecutive_misses = 0
    processed = 0
    user_id = 0

    log_debug(f"Starting character discovery + profile crawl (stop after {MAX_CONSECUTIVE_MISSES} consecutive misses)")

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

        # Full profile crawl (Playwright for power grid)
        set_activity(
            f"({processed}) Profile: {name}",
            character_id=sid,
            character_name=name,
        )
        try:
            await crawl_character_profile(sid, settings.database_path)
        except Exception as e:
            log_debug(f"Error crawling profile for {name} ({sid}): {e}", level="error")

        await asyncio.sleep(2)

    clear_activity()
    log_debug(
        f"Discovery complete: checked {user_id} IDs, {processed} characters processed",
        level="done",
    )


async def _crawl_all_characters():
    """Crawl every account by iterating showuser=1, 2, 3...

    Pure HTML crawling — no ACP dependency.  For each user ID:
    lightweight httpx check first, then full Playwright profile crawl +
    thread/quote crawl for valid accounts.  Stops after 100 consecutive
    misses (deleted/banned profiles).

    This is the FALLBACK path used when ACP credentials are not available.
    When ACP is configured, _acp_sync_cycle handles threads/posts and
    _discover_and_crawl_profiles handles profile discovery.
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


async def _crawl_all_profiles():
    """Re-crawl profiles for all tracked characters.

    Lightweight alternative to _crawl_all_characters — only re-fetches
    profile pages and updates profile fields (hero images, dossier data,
    power grid, etc.).  Skips threads entirely.

    Iterates over characters already in the database rather than brute-
    forcing user IDs, so it's fast even on boards with sparse ID ranges.
    """
    log_debug("Starting profile-only re-crawl for all tracked characters")
    set_activity("Re-crawling all profiles")

    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id, name FROM characters ORDER BY id")
        characters = [(row["id"], row["name"]) for row in await cursor.fetchall()]

    if not characters:
        clear_activity()
        log_debug("No characters to re-crawl")
        return

    total = len(characters)
    processed = 0
    errors = 0

    for cid, name in characters:
        processed += 1
        set_activity(
            f"Re-crawling profile {processed}/{total}: {name}",
            character_id=cid,
            character_name=name,
        )
        try:
            result = await crawl_character_profile(cid, settings.database_path)
            fields_count = result.get("fields_count", 0)
            log_debug(f"Profile re-crawl {processed}/{total}: {name} ({cid}) — {fields_count} fields")
        except Exception as e:
            errors += 1
            log_debug(f"Error re-crawling profile for {name} ({cid}): {e}", level="error")

    clear_activity()
    log_debug(
        f"Profile re-crawl complete: {processed} characters, {errors} errors",
        level="done",
    )


def start_scheduler():
    """Start the APScheduler with configured intervals.

    Two modes:
    1. ACP mode (admin credentials configured): Uses targeted SQL dump for
       thread/post data (fast, reliable), HTML only for profiles and quotes.
    2. HTML-only mode (no admin credentials): Brute-force HTML crawl for
       everything (slow, fragile, but works without ACP access).
    """
    global _scheduler, _startup_task
    _scheduler = AsyncIOScheduler()

    use_acp = _has_acp_credentials()

    if use_acp:
        # ACP mode: fast targeted SQL dump for threads + posts
        acp_interval = settings.acp_sync_interval_minutes or settings.crawl_threads_interval_minutes
        _scheduler.add_job(
            _acp_sync_cycle,
            trigger=IntervalTrigger(minutes=acp_interval),
            id="acp_sync_cycle",
            name="ACP sync: threads + posts + quotes",
            replace_existing=True,
            next_run_time=None,
        )

        # Profile discovery + crawl on a separate schedule
        _scheduler.add_job(
            _discover_and_crawl_profiles,
            trigger=IntervalTrigger(minutes=settings.crawl_discovery_interval_minutes),
            id="discover_profiles",
            name="Discover + crawl character profiles",
            replace_existing=True,
            next_run_time=None,
        )

        log_debug(
            f"Scheduler started (ACP mode) — "
            f"ACP sync every {acp_interval}min, "
            f"profile discovery every {settings.crawl_discovery_interval_minutes}min"
        )

        async def _startup():
            await _clear_quote_crawl_log()
            # ACP sync first (fast) — gets all thread/post data in one dump
            await _acp_sync_cycle()
            # Then discover + crawl profiles (slower, uses Playwright)
            await _discover_and_crawl_profiles()

    else:
        # HTML-only fallback
        _scheduler.add_job(
            _crawl_all_characters,
            trigger=IntervalTrigger(minutes=settings.crawl_threads_interval_minutes),
            id="crawl_all_characters",
            name="Full crawl: iterate all user IDs",
            replace_existing=True,
            next_run_time=None,
        )

        log_debug(
            f"Scheduler started (HTML-only mode, no ACP credentials) — "
            f"full crawl every {settings.crawl_threads_interval_minutes}min"
        )

        async def _startup():
            await _clear_quote_crawl_log()
            await _crawl_all_characters()

    _scheduler.start()
    _startup_task = asyncio.get_running_loop().create_task(_startup())


def stop_scheduler():
    """Stop the scheduler."""
    global _scheduler, _startup_task
    if _startup_task and not _startup_task.done():
        _startup_task.cancel()
        _startup_task = None
    if _scheduler:
        _scheduler.shutdown()
        _scheduler = None
        log_debug("Scheduler stopped")
