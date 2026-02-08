"""Tests for app/database.py — Database initialization and connection."""
import os
import pytest
import aiosqlite

from app.database import init_db, get_db, DATABASE_PATH


@pytest.fixture(autouse=True)
async def clean_db():
    """Ensure DB tables exist before each test, using the same path init_db uses."""
    await init_db()
    yield
    if os.path.exists(DATABASE_PATH):
        try:
            os.unlink(DATABASE_PATH)
        except OSError:
            pass


class TestInitDb:
    async def test_creates_characters_table(self):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='characters'"
            )
            row = await cursor.fetchone()
            assert row is not None

    async def test_creates_profile_fields_table(self):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='profile_fields'"
            )
            assert await cursor.fetchone() is not None

    async def test_creates_threads_table(self):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='threads'"
            )
            assert await cursor.fetchone() is not None

    async def test_creates_character_threads_table(self):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='character_threads'"
            )
            assert await cursor.fetchone() is not None

    async def test_creates_quotes_table(self):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='quotes'"
            )
            assert await cursor.fetchone() is not None

    async def test_creates_quote_crawl_log_table(self):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='quote_crawl_log'"
            )
            assert await cursor.fetchone() is not None

    async def test_creates_crawl_status_table(self):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='crawl_status'"
            )
            assert await cursor.fetchone() is not None

    async def test_creates_indexes(self):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
            )
            rows = await cursor.fetchall()
            index_names = {r[0] for r in rows}
            assert "idx_profile_fields_character" in index_names
            assert "idx_character_threads_character" in index_names
            assert "idx_character_threads_thread" in index_names
            assert "idx_quotes_character" in index_names
            assert "idx_threads_category" in index_names

    async def test_init_db_is_idempotent(self):
        """Calling init_db twice should not raise."""
        await init_db()
        await init_db()
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            )
            row = await cursor.fetchone()
            assert row[0] >= 7


class TestGetDb:
    async def test_yields_connection(self):
        """get_db should yield a usable aiosqlite connection."""
        gen = get_db()
        db = await gen.__anext__()
        assert db is not None
        cursor = await db.execute("SELECT 1")
        row = await cursor.fetchone()
        assert row[0] == 1
        try:
            await gen.__anext__()
        except StopAsyncIteration:
            pass

    async def test_connection_has_row_factory(self):
        """get_db connections should use Row factory for dict-like access."""
        gen = get_db()
        db = await gen.__anext__()
        assert db.row_factory == aiosqlite.Row
        try:
            await gen.__anext__()
        except StopAsyncIteration:
            pass
