"""Tests for app/services/scheduler.py — APScheduler job management."""
import os
import pytest
from unittest.mock import patch, AsyncMock
import aiosqlite

from app.database import init_db, DATABASE_PATH
from app.models.operations import upsert_character
from app.services.scheduler import (
    start_scheduler,
    stop_scheduler,
    _crawl_all_threads,
    _crawl_all_profiles,
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
        with patch("app.services.scheduler._discover_all_characters", new_callable=AsyncMock):
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
        with patch("app.services.scheduler._discover_all_characters", new_callable=AsyncMock):
            start_scheduler()
        jobs = scheduler._scheduler.get_jobs()
        job_ids = {j.id for j in jobs}
        assert "crawl_threads" in job_ids
        assert "crawl_profiles" in job_ids
        stop_scheduler()


class TestCrawlAllThreads:
    async def test_no_characters(self):
        """Should complete without error when no characters registered."""
        with patch("app.services.scheduler.crawl_character_threads", new_callable=AsyncMock) as mock_crawl, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await _crawl_all_threads()
            mock_crawl.assert_not_awaited()

    async def test_crawls_each_character(self):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            await upsert_character(db, "1", "Alpha", "https://example.com/1")
            await upsert_character(db, "2", "Beta", "https://example.com/2")

        with patch("app.services.scheduler.crawl_character_threads", new_callable=AsyncMock) as mock_crawl, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await _crawl_all_threads()
            assert mock_crawl.await_count == 2

    async def test_continues_on_error(self):
        """If one character fails, should still crawl the next."""
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            await upsert_character(db, "1", "Alpha", "https://example.com/1")
            await upsert_character(db, "2", "Beta", "https://example.com/2")

        with patch("app.services.scheduler.crawl_character_threads", new_callable=AsyncMock,
                    side_effect=[Exception("fail"), {"ongoing": 1}]) as mock_crawl, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await _crawl_all_threads()
            assert mock_crawl.await_count == 2


class TestCrawlAllProfiles:
    async def test_no_characters(self):
        with patch("app.services.scheduler.crawl_character_profile", new_callable=AsyncMock) as mock_crawl, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await _crawl_all_profiles()
            mock_crawl.assert_not_awaited()

    async def test_crawls_each_character(self):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            await upsert_character(db, "1", "Alpha", "https://example.com/1")
            await upsert_character(db, "2", "Beta", "https://example.com/2")

        with patch("app.services.scheduler.crawl_character_profile", new_callable=AsyncMock) as mock_crawl, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await _crawl_all_profiles()
            assert mock_crawl.await_count == 2

    async def test_continues_on_error(self):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            await upsert_character(db, "1", "Alpha", "https://example.com/1")
            await upsert_character(db, "2", "Beta", "https://example.com/2")

        with patch("app.services.scheduler.crawl_character_profile", new_callable=AsyncMock,
                    side_effect=[Exception("fail"), {"name": "Beta"}]) as mock_crawl, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await _crawl_all_profiles()
            assert mock_crawl.await_count == 2
