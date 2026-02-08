import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import aiosqlite

from app.config import settings
from app.services.crawler import crawl_character_threads, crawl_character_profile


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

    print(f"[Scheduler] Crawling threads for {len(characters)} characters")
    for char in characters:
        try:
            await crawl_character_threads(char["id"], settings.database_path)
        except Exception as e:
            print(f"[Scheduler] Error crawling threads for {char['name']} ({char['id']}): {e}")
        # Extra delay between characters to be polite
        await asyncio.sleep(settings.request_delay_seconds * 2)

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

    print(f"[Scheduler] Crawling profiles for {len(characters)} characters")
    for char in characters:
        try:
            await crawl_character_profile(char["id"], settings.database_path)
        except Exception as e:
            print(f"[Scheduler] Error crawling profile for {char['name']} ({char['id']}): {e}")
        await asyncio.sleep(settings.request_delay_seconds)

    print("[Scheduler] Scheduled profile crawl complete")


def start_scheduler():
    """Start the APScheduler with configured intervals."""
    global _scheduler
    _scheduler = AsyncIOScheduler()

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
    print(f"[Scheduler] Started - threads every {settings.crawl_threads_interval_minutes}min, profiles every {settings.crawl_profiles_interval_minutes}min")


def stop_scheduler():
    """Stop the scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
        _scheduler = None
        print("[Scheduler] Stopped")
