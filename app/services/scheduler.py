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


async def _crawl_all_threads():
    """Crawl threads for all tracked characters.

    When ACP credentials are configured, uses the ACP SQL dump as the primary
    data source (threads, posts, last poster, counts) and follows up with a
    quote-only HTML pass. Falls back to full HTML crawling when ACP is not
    available.
    """
    # Try ACP-primary path first
    if await _acp_available():
        log_debug("ACP configured — using SQL dump as primary source")
        try:
            result = await sync_posts_from_acp(settings.database_path)
            if "error" not in result:
                log_debug(f"ACP sync succeeded: {result}", level="done")
                # Follow up with quote-only HTML pass
                log_debug("Starting quote-only HTML pass")
                quote_result = await crawl_quotes_only(settings.database_path)
                log_debug(f"Quote pass: {quote_result}", level="done")
                return
            else:
                log_debug(f"ACP sync failed: {result.get('error')} — falling back to HTML crawl", level="error")
        except Exception as e:
            log_debug(f"ACP sync error: {e} — falling back to HTML crawl", level="error")

    # Quick pass: crawl today's active topics first (one search, no per-character cooldown)
    try:
        recent = await crawl_recent_threads(settings.database_path)
        log_debug(f"Recent threads pass: {recent}", level="done")
    except Exception as e:
        log_debug(f"Recent threads error: {e}", level="error")

    # Full HTML crawl per character (slower, may hit search cooldown)
    log_debug("Starting scheduled HTML thread crawl for all characters")
    excluded = settings.excluded_name_set
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id, name FROM characters ORDER BY last_thread_crawl ASC NULLS FIRST")
        all_chars = await cursor.fetchall()

    characters = [c for c in all_chars if c["name"].lower() not in excluded]
    if not characters:
        log_debug("No characters to crawl")
        return

    total = len(characters)
    log_debug(f"Crawling threads for {total} characters")
    for i, char in enumerate(characters, 1):
        set_activity(
            f"Crawling threads ({i}/{total}): {char['name']}",
            character_id=char["id"],
            character_name=char["name"],
        )
        try:
            await crawl_character_threads(char["id"], settings.database_path)
        except Exception as e:
            log_debug(f"Error crawling threads for {char['name']} ({char['id']}): {e}", level="error")
        # Longer delay between characters to avoid JCink search flood control
        await asyncio.sleep(15)

    clear_activity()
    log_debug("Scheduled thread crawl complete", level="done")


async def _crawl_all_profiles():
    """Crawl profiles for all tracked characters."""
    log_debug("Starting scheduled profile crawl for all characters")
    excluded = settings.excluded_name_set
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id, name FROM characters ORDER BY last_profile_crawl ASC NULLS FIRST")
        all_chars = await cursor.fetchall()

    characters = [c for c in all_chars if c["name"].lower() not in excluded]
    if not characters:
        log_debug("No characters to crawl")
        return

    total = len(characters)
    log_debug(f"Crawling profiles for {total} characters")
    for i, char in enumerate(characters, 1):
        set_activity(
            f"Crawling profiles ({i}/{total}): {char['name']}",
            character_id=char["id"],
            character_name=char["name"],
        )
        try:
            await crawl_character_profile(char["id"], settings.database_path)
        except Exception as e:
            log_debug(f"Error crawling profile for {char['name']} ({char['id']}): {e}", level="error")
        await asyncio.sleep(settings.request_delay_seconds)

    clear_activity()
    log_debug("Scheduled profile crawl complete", level="done")


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
    """Run discovery then a full thread crawl on startup.

    Sequenced to avoid concurrent forum requests that trigger flood control.
    """
    await _discover_all_characters()
    log_debug("Discovery done — starting initial thread crawl")
    await _crawl_all_threads()


def start_scheduler():
    """Start the APScheduler with configured intervals."""
    global _scheduler
    _scheduler = AsyncIOScheduler()

    # All jobs run on interval only — startup is handled by _startup_sequence
    _scheduler.add_job(
        _discover_all_characters,
        trigger=IntervalTrigger(minutes=settings.crawl_discovery_interval_minutes),
        id="discover_characters",
        name="Auto-discover characters from member list",
        replace_existing=True,
        next_run_time=None,  # Startup handled separately
    )

    _scheduler.add_job(
        _crawl_all_threads,
        trigger=IntervalTrigger(minutes=settings.crawl_threads_interval_minutes),
        id="crawl_threads",
        name="Crawl threads for all characters",
        replace_existing=True,
        next_run_time=None,  # Startup handled separately
    )

    _scheduler.add_job(
        _crawl_all_profiles,
        trigger=IntervalTrigger(minutes=settings.crawl_profiles_interval_minutes),
        id="crawl_profiles",
        name="Crawl profiles for all characters",
        replace_existing=True,
    )

    _scheduler.start()

    # Run discovery → thread crawl sequentially on startup
    asyncio.get_running_loop().create_task(_startup_sequence())

    log_debug(f"Scheduler started - discovery every {settings.crawl_discovery_interval_minutes}min, threads every {settings.crawl_threads_interval_minutes}min, profiles every {settings.crawl_profiles_interval_minutes}min")


def stop_scheduler():
    """Stop the scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
        _scheduler = None
        log_debug("Scheduler stopped")
