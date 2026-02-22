"""Tests for app/services/scheduler.py — APScheduler job management."""
import os
import pytest
from unittest.mock import patch, AsyncMock
import aiosqlite

from app.database import init_db, DATABASE_PATH
from app.services.scheduler import (
    start_scheduler,
    stop_scheduler,
    _crawl_all_characters,
)


@pytest.fixture(autouse=True)
async def fresh_db():
    await init_db()
    yield
    if os.path.exists(DATABASE_PATH):
        try:
            os.unlink(DATABASE_PATH)
        except OSError:
            pass


class TestStartStopScheduler:
    async def test_start_creates_scheduler(self):
        from app.services import scheduler
        scheduler._scheduler = None
        with patch("app.services.scheduler._crawl_all_characters", new_callable=AsyncMock):
            start_scheduler()
        assert scheduler._scheduler is not None
        stop_scheduler()
        assert scheduler._scheduler is None

    async def test_stop_when_not_started(self):
        from app.services import scheduler
        scheduler._scheduler = None
        # Should not raise
        stop_scheduler()

    async def test_start_registers_jobs(self):
        from app.services import scheduler
        scheduler._scheduler = None
        with patch("app.services.scheduler._crawl_all_characters", new_callable=AsyncMock):
            start_scheduler()
        jobs = scheduler._scheduler.get_jobs()
        job_ids = {j.id for j in jobs}
        assert "crawl_all_characters" in job_ids
        stop_scheduler()


class TestCrawlAllCharacters:
    async def test_stops_after_consecutive_misses(self):
        """Should stop after MAX_CONSECUTIVE_MISSES board-message responses."""
        with patch("app.services.scheduler.check_profile_exists", new_callable=AsyncMock, return_value=None) as mock_check, \
             patch("app.services.scheduler.crawl_character_profile", new_callable=AsyncMock) as mock_profile, \
             patch("app.services.scheduler.crawl_character_threads", new_callable=AsyncMock) as mock_threads:
            await _crawl_all_characters()
            # Should have checked 100 IDs then stopped
            assert mock_check.await_count == 100
            mock_profile.assert_not_awaited()
            mock_threads.assert_not_awaited()

    async def test_crawls_valid_profiles(self):
        """Should crawl profile + threads for valid user IDs."""
        # IDs 1-3 exist, then 20 consecutive misses
        side_effects = ["Alpha", "Beta", "Gamma"] + [None] * 100

        with patch("app.services.scheduler.check_profile_exists", new_callable=AsyncMock, side_effect=side_effects) as mock_check, \
             patch("app.services.scheduler.crawl_character_profile", new_callable=AsyncMock) as mock_profile, \
             patch("app.services.scheduler.crawl_character_threads", new_callable=AsyncMock) as mock_threads, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await _crawl_all_characters()
            assert mock_profile.await_count == 3
            assert mock_threads.await_count == 3

    async def test_resets_miss_counter_on_valid(self):
        """A valid profile resets the consecutive miss counter."""
        # 5 misses, 1 valid, then 20 misses → should stop
        side_effects = [None] * 5 + ["Alpha"] + [None] * 100

        with patch("app.services.scheduler.check_profile_exists", new_callable=AsyncMock, side_effect=side_effects) as mock_check, \
             patch("app.services.scheduler.crawl_character_profile", new_callable=AsyncMock), \
             patch("app.services.scheduler.crawl_character_threads", new_callable=AsyncMock), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await _crawl_all_characters()
            # 5 misses + 1 valid + 100 misses = 106 checks
            assert mock_check.await_count == 106

    async def test_skips_excluded_names(self):
        """Excluded names should be skipped without crawling."""
        # ID 1 = excluded, ID 2 = valid, then 20 misses
        side_effects = ["Watcher", "Alpha"] + [None] * 100

        with patch("app.services.scheduler.check_profile_exists", new_callable=AsyncMock, side_effect=side_effects), \
             patch("app.services.scheduler.crawl_character_profile", new_callable=AsyncMock) as mock_profile, \
             patch("app.services.scheduler.crawl_character_threads", new_callable=AsyncMock) as mock_threads, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await _crawl_all_characters()
            # Only Alpha should be crawled, Watcher is excluded
            assert mock_profile.await_count == 1
            assert mock_threads.await_count == 1

    async def test_continues_on_crawl_error(self):
        """If one character's crawl errors, should still continue to next."""
        side_effects = ["Alpha", "Beta"] + [None] * 100

        with patch("app.services.scheduler.check_profile_exists", new_callable=AsyncMock, side_effect=side_effects), \
             patch("app.services.scheduler.crawl_character_profile", new_callable=AsyncMock,
                   side_effect=[Exception("fail"), {"name": "Beta"}]) as mock_profile, \
             patch("app.services.scheduler.crawl_character_threads", new_callable=AsyncMock) as mock_threads, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await _crawl_all_characters()
            assert mock_profile.await_count == 2
            assert mock_threads.await_count == 2
