"""Tests for app/models/dashboard_queries.py — Dashboard search/filter queries."""
import os
import pytest
import aiosqlite

from app.database import init_db, DATABASE_PATH
from app.models.operations import (
    upsert_character,
    upsert_thread,
    link_character_thread,
    add_quote,
    upsert_profile_field,
)
from app.models.dashboard_queries import (
    search_characters,
    search_threads_global,
    search_quotes_global,
    get_unique_affiliations,
    get_unique_groups,
    get_dashboard_stats,
)


@pytest.fixture(autouse=True)
async def fresh_db():
    """Fresh database for each test."""
    await init_db()
    yield
    if os.path.exists(DATABASE_PATH):
        try:
            os.unlink(DATABASE_PATH)
        except OSError:
            pass


async def _get_db():
    db = await aiosqlite.connect(DATABASE_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def _seed_data(db):
    """Insert sample data for testing."""
    await upsert_character(db, "1", "Tony Stark", "https://example.com/1", "Red", "https://img.com/tony.jpg")
    await upsert_character(db, "2", "Steve Rogers", "https://example.com/2", "Blue", "https://img.com/steve.jpg")
    await upsert_character(db, "3", "Natasha Romanoff", "https://example.com/3", "Red", None)
    await upsert_profile_field(db, "1", "affiliation", "Avengers")
    await upsert_profile_field(db, "2", "affiliation", "Avengers")
    await upsert_profile_field(db, "3", "affiliation", "SHIELD")
    await db.commit()

    await upsert_thread(db, "t1", "Team Meeting", "https://forum.com/t1", "1", "General", "ongoing", "2", "Steve Rogers")
    await upsert_thread(db, "t2", "Mission Report", "https://forum.com/t2", "2", "Missions", "complete", "1", "Tony Stark")
    await upsert_thread(db, "t3", "Training Day", "https://forum.com/t3", "3", "Comms", "comms", "3", "Natasha Romanoff")
    await link_character_thread(db, "1", "t1", "ongoing", False)
    await link_character_thread(db, "1", "t2", "complete", True)
    await link_character_thread(db, "2", "t1", "ongoing", True)
    await link_character_thread(db, "3", "t3", "comms", True)
    await db.commit()

    await add_quote(db, "1", '"I am Iron Man."', "t1", "Team Meeting")
    await add_quote(db, "1", '"Genius, billionaire, playboy, philanthropist."', "t2", "Mission Report")
    await add_quote(db, "2", '"I can do this all day."', "t1", "Team Meeting")
    await db.commit()


class TestSearchCharacters:
    async def test_empty_db(self):
        db = await _get_db()
        try:
            chars, total = await search_characters(db)
            assert chars == []
            assert total == 0
        finally:
            await db.close()

    async def test_returns_all_characters(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            chars, total = await search_characters(db)
            assert total == 3
            assert len(chars) == 3
        finally:
            await db.close()

    async def test_search_by_name(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            chars, total = await search_characters(db, query="Tony")
            assert total == 1
            assert chars[0]["name"] == "Tony Stark"
        finally:
            await db.close()

    async def test_filter_by_affiliation(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            chars, total = await search_characters(db, affiliations=["Avengers"])
            assert total == 2
        finally:
            await db.close()

    async def test_filter_by_group(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            chars, total = await search_characters(db, group_name="Red")
            assert total == 2
        finally:
            await db.close()

    async def test_sort_by_name_desc(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            chars, _ = await search_characters(db, sort_dir="desc")
            names = [c["name"] for c in chars]
            assert names == sorted(names, reverse=True)
        finally:
            await db.close()

    async def test_pagination(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            chars, total = await search_characters(db, per_page=2, page=1)
            assert len(chars) == 2
            assert total == 3

            chars2, _ = await search_characters(db, per_page=2, page=2)
            assert len(chars2) == 1
        finally:
            await db.close()

    async def test_thread_counts_included(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            chars, _ = await search_characters(db, query="Tony")
            assert "thread_counts" in chars[0]
            assert chars[0]["thread_counts"]["total"] == 2
        finally:
            await db.close()

    async def test_invalid_sort_defaults_to_name(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            chars, _ = await search_characters(db, sort_by="invalid_column")
            assert len(chars) == 3
        finally:
            await db.close()


class TestSearchThreadsGlobal:
    async def test_empty_db(self):
        db = await _get_db()
        try:
            threads, total = await search_threads_global(db)
            assert threads == []
            assert total == 0
        finally:
            await db.close()

    async def test_returns_all_threads(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            threads, total = await search_threads_global(db)
            assert total == 4  # 4 character-thread links
        finally:
            await db.close()

    async def test_search_by_title(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            threads, total = await search_threads_global(db, query="Meeting")
            assert total == 2  # t1 linked to char 1 and 2
        finally:
            await db.close()

    async def test_filter_by_category(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            threads, total = await search_threads_global(db, category="ongoing")
            assert total == 2
        finally:
            await db.close()

    async def test_filter_by_status_awaiting(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            threads, total = await search_threads_global(db, status="awaiting")
            assert all(not t["is_user_last_poster"] for t in threads)
        finally:
            await db.close()

    async def test_filter_by_status_replied(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            threads, total = await search_threads_global(db, status="replied")
            assert all(t["is_user_last_poster"] for t in threads)
        finally:
            await db.close()

    async def test_filter_by_character(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            threads, total = await search_threads_global(db, character_id="1")
            assert total == 2
        finally:
            await db.close()

    async def test_includes_character_info(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            threads, _ = await search_threads_global(db, character_id="1")
            assert all("char_name" in t for t in threads)
            assert all("char_id" in t for t in threads)
        finally:
            await db.close()


class TestSearchQuotesGlobal:
    async def test_empty_db(self):
        db = await _get_db()
        try:
            quotes, total = await search_quotes_global(db)
            assert quotes == []
            assert total == 0
        finally:
            await db.close()

    async def test_returns_all_quotes(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            quotes, total = await search_quotes_global(db)
            assert total == 3
        finally:
            await db.close()

    async def test_search_by_text(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            quotes, total = await search_quotes_global(db, query="Iron Man")
            assert total == 1
            assert "Iron Man" in quotes[0]["quote_text"]
        finally:
            await db.close()

    async def test_filter_by_character(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            quotes, total = await search_quotes_global(db, character_id="1")
            assert total == 2
        finally:
            await db.close()

    async def test_includes_character_name(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            quotes, _ = await search_quotes_global(db)
            assert all("character_name" in q for q in quotes)
        finally:
            await db.close()

    async def test_pagination(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            quotes, total = await search_quotes_global(db, per_page=2, page=1)
            assert len(quotes) == 2
            assert total == 3
        finally:
            await db.close()


class TestGetUniqueAffiliations:
    async def test_empty_db(self):
        db = await _get_db()
        try:
            affs = await get_unique_affiliations(db)
            assert affs == []
        finally:
            await db.close()

    async def test_returns_unique(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            affs = await get_unique_affiliations(db)
            assert set(affs) == {"Avengers", "SHIELD"}
        finally:
            await db.close()


class TestGetUniqueGroups:
    async def test_returns_unique(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            groups = await get_unique_groups(db)
            assert set(groups) == {"Red", "Blue"}
        finally:
            await db.close()


class TestGetDashboardStats:
    async def test_empty_db(self):
        db = await _get_db()
        try:
            stats = await get_dashboard_stats(db)
            assert stats["characters_tracked"] == 0
            assert stats["total_threads"] == 0
            assert stats["total_quotes"] == 0
            assert stats["threads_awaiting"] == 0
        finally:
            await db.close()

    async def test_with_data(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            stats = await get_dashboard_stats(db)
            assert stats["characters_tracked"] == 3
            assert stats["total_threads"] == 3
            assert stats["total_quotes"] == 3
        finally:
            await db.close()

    async def test_threads_awaiting(self):
        db = await _get_db()
        try:
            await _seed_data(db)
            stats = await get_dashboard_stats(db)
            # Tony has 1 ongoing thread where is_user_last_poster=0
            assert stats["threads_awaiting"] == 1
        finally:
            await db.close()
