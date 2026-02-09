import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import aiosqlite

from app.config import settings
from app.services.crawler import crawl_character_threads, crawl_character_profile, discover_characters
from app.services.activity import set_activity, clear_activity


_scheduler: AsyncIOScheduler | None = None


async def _crawl_all_threads():
    """Crawl threads for all tracked characters."""
    print("[Scheduler] Starting scheduled thread crawl for all characters")
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id, name FROM characters ORDER BY last_thread_crawl ASC NULLS FIRST")
        characters = await cursor.fetchall()

    if not characters:
        print("[Scheduler] No characters to crawl")
        return

    total = len(characters)
    print(f"[Scheduler] Crawling threads for {total} characters")
    for i, char in enumerate(characters, 1):
        set_activity(
            f"Crawling threads ({i}/{total}): {char['name']}",
            character_id=char["id"],
            character_name=char["name"],
        )
        try:
            await crawl_character_threads(char["id"], settings.database_path)
        except Exception as e:
            print(f"[Scheduler] Error crawling threads for {char['name']} ({char['id']}): {e}")
        # Extra delay between characters to be polite
        await asyncio.sleep(settings.request_delay_seconds * 2)

    clear_activity()
    print("[Scheduler] Scheduled thread crawl complete")


async def _crawl_all_profiles():
    """Crawl profiles for all tracked characters."""
    print("[Scheduler] Starting scheduled profile crawl for all characters")
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id, name FROM characters ORDER BY last_profile_crawl ASC NULLS FIRST")
        characters = await cursor.fetchall()

    if not characters:
        print("[Scheduler] No characters to crawl")
        return

    total = len(characters)
    print(f"[Scheduler] Crawling profiles for {total} characters")
    for i, char in enumerate(characters, 1):
        set_activity(
            f"Crawling profiles ({i}/{total}): {char['name']}",
            character_id=char["id"],
            character_name=char["name"],
        )
        try:
            await crawl_character_profile(char["id"], settings.database_path)
        except Exception as e:
            print(f"[Scheduler] Error crawling profile for {char['name']} ({char['id']}): {e}")
        await asyncio.sleep(settings.request_delay_seconds)

    clear_activity()
    print("[Scheduler] Scheduled profile crawl complete")


async def _discover_all_characters():
    """Auto-discover new characters from the forum member list."""
    print("[Scheduler] Starting scheduled character discovery")
    try:
        result = await discover_characters(settings.database_path)
        print(f"[Scheduler] Discovery complete: {result}")
    except Exception as e:
        print(f"[Scheduler] Error during character discovery: {e}")


def start_scheduler():
    """Start the APScheduler with configured intervals."""
    global _scheduler
    _scheduler = AsyncIOScheduler()

    # Run discovery immediately on startup, then on interval
    _scheduler.add_job(
        _discover_all_characters,
        trigger=IntervalTrigger(minutes=settings.crawl_discovery_interval_minutes),
        id="discover_characters",
        name="Auto-discover characters from member list",
        replace_existing=True,
        next_run_time=None,  # Will be scheduled; immediate run is separate
    )

    _scheduler.add_job(
        _crawl_all_threads,
        trigger=IntervalTrigger(minutes=settings.crawl_threads_interval_minutes),
        id="crawl_threads",
        name="Crawl threads for all characters",
        replace_existing=True,
    )

    _scheduler.add_job(
        _crawl_all_profiles,
        trigger=IntervalTrigger(minutes=settings.crawl_profiles_interval_minutes),
        id="crawl_profiles",
        name="Crawl profiles for all characters",
        replace_existing=True,
    )

    _scheduler.start()

    # Trigger discovery immediately on startup
    asyncio.get_running_loop().create_task(_discover_all_characters())

    print(f"[Scheduler] Started - discovery every {settings.crawl_discovery_interval_minutes}min, threads every {settings.crawl_threads_interval_minutes}min, profiles every {settings.crawl_profiles_interval_minutes}min")


def stop_scheduler():
    """Stop the scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
        _scheduler = None
        print("[Scheduler] Stopped")
