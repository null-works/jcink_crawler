"""Tests for app/models/operations.py — Database CRUD operations."""
import os
import pytest
import aiosqlite

from app.database import init_db, DATABASE_PATH
from app.models.operations import (
    get_character,
    get_all_characters,
    upsert_character,
    update_character_crawl_time,
    upsert_thread,
    link_character_thread,
    get_character_threads,
    get_thread_counts,
    add_quote,
    get_random_quote,
    get_all_quotes,
    get_quote_count,
    is_thread_quote_scraped,
    mark_thread_quote_scraped,
    upsert_profile_field,
    get_profile_fields,
    set_crawl_status,
    get_crawl_status,
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


# --- Character Operations ---

class TestUpsertCharacter:
    async def test_insert_new_character(self):
        db = await _get_db()
        try:
            await upsert_character(db, "42", "Tony Stark", "https://example.com/42", "Avengers", "https://img.com/tony.jpg")
            char = await get_character(db, "42")
            assert char is not None
            assert char.id == "42"
            assert char.name == "Tony Stark"
            assert char.profile_url == "https://example.com/42"
            assert char.group_name == "Avengers"
            assert char.avatar_url == "https://img.com/tony.jpg"
        finally:
            await db.close()

    async def test_update_existing_character(self):
        db = await _get_db()
        try:
            await upsert_character(db, "42", "Tony Stark", "https://example.com/42")
            await upsert_character(db, "42", "Iron Man", "https://example.com/42", "Avengers")
            char = await get_character(db, "42")
            assert char.name == "Iron Man"
            assert char.group_name == "Avengers"
        finally:
            await db.close()

    async def test_optional_fields_default_none(self):
        db = await _get_db()
        try:
            await upsert_character(db, "1", "Test", "https://example.com/1")
            char = await get_character(db, "1")
            assert char.group_name is None
            assert char.avatar_url is None
        finally:
            await db.close()


class TestGetCharacter:
    async def test_returns_none_for_missing(self):
        db = await _get_db()
        try:
            result = await get_character(db, "nonexistent")
            assert result is None
        finally:
            await db.close()

    async def test_includes_thread_counts(self):
        db = await _get_db()
        try:
            await upsert_character(db, "42", "Tony", "https://example.com/42")
            char = await get_character(db, "42")
            assert "total" in char.thread_counts
            assert char.thread_counts["total"] == 0
        finally:
            await db.close()


class TestGetAllCharacters:
    async def test_empty_database(self):
        db = await _get_db()
        try:
            result = await get_all_characters(db)
            assert result == []
        finally:
            await db.close()

    async def test_returns_all_ordered_by_name(self):
        db = await _get_db()
        try:
            await upsert_character(db, "2", "Zeta", "https://example.com/2")
            await upsert_character(db, "1", "Alpha", "https://example.com/1")
            result = await get_all_characters(db)
            assert len(result) == 2
            assert result[0].name == "Alpha"
            assert result[1].name == "Zeta"
        finally:
            await db.close()


class TestUpdateCharacterCrawlTime:
    async def test_updates_thread_crawl_time(self):
        db = await _get_db()
        try:
            await upsert_character(db, "42", "Tony", "https://example.com/42")
            char_before = await get_character(db, "42")
            assert char_before.last_thread_crawl is None

            await update_character_crawl_time(db, "42", "threads")
            char_after = await get_character(db, "42")
            assert char_after.last_thread_crawl is not None
        finally:
            await db.close()

    async def test_updates_profile_crawl_time(self):
        db = await _get_db()
        try:
            await upsert_character(db, "42", "Tony", "https://example.com/42")
            await update_character_crawl_time(db, "42", "profile")
            char = await get_character(db, "42")
            assert char.last_profile_crawl is not None
        finally:
            await db.close()


# --- Thread Operations ---

class TestUpsertThread:
    async def test_insert_thread(self):
        db = await _get_db()
        try:
            await upsert_character(db, "42", "Tony", "https://example.com/42")
            await upsert_thread(db, "100", "Test Thread", "https://example.com/t/100",
                                "49", "Complete", "complete", "1", "Steve", "https://img.com/steve.jpg")
            await link_character_thread(db, "42", "100", "complete", False)
            await db.commit()

            threads = await get_character_threads(db, "42")
            assert len(threads.complete) == 1
            assert threads.complete[0].title == "Test Thread"
            assert threads.complete[0].last_poster_name == "Steve"
        finally:
            await db.close()

    async def test_upsert_updates_existing(self):
        db = await _get_db()
        try:
            await upsert_thread(db, "100", "Old Title", "https://example.com/t/100",
                                None, None, "ongoing")
            await db.commit()
            await upsert_thread(db, "100", "New Title", "https://example.com/t/100",
                                None, None, "ongoing")
            await db.commit()
            cursor = await db.execute("SELECT COUNT(*) as c FROM threads WHERE id = '100'")
            row = await cursor.fetchone()
            assert row["c"] == 1
            cursor = await db.execute("SELECT title FROM threads WHERE id = '100'")
            row = await cursor.fetchone()
            assert row["title"] == "New Title"
        finally:
            await db.close()


class TestGetCharacterThreads:
    async def test_categorizes_threads(self):
        db = await _get_db()
        try:
            await upsert_character(db, "42", "Tony", "https://example.com/42")
            for tid, cat in [("1", "ongoing"), ("2", "comms"), ("3", "complete"), ("4", "incomplete")]:
                await upsert_thread(db, tid, f"Thread {tid}", f"https://example.com/t/{tid}",
                                    None, None, cat)
                await link_character_thread(db, "42", tid, cat)
            await db.commit()

            threads = await get_character_threads(db, "42")
            assert len(threads.ongoing) == 1
            assert len(threads.comms) == 1
            assert len(threads.complete) == 1
            assert len(threads.incomplete) == 1
            assert threads.counts["total"] == 4
        finally:
            await db.close()

    async def test_unknown_character_returns_unknown_name(self):
        db = await _get_db()
        try:
            threads = await get_character_threads(db, "nonexistent")
            assert threads.character_name == "Unknown"
            assert threads.counts["total"] == 0
        finally:
            await db.close()


class TestGetThreadCounts:
    async def test_empty_counts(self):
        db = await _get_db()
        try:
            counts = await get_thread_counts(db, "42")
            assert counts["total"] == 0
        finally:
            await db.close()

    async def test_counts_by_category(self):
        db = await _get_db()
        try:
            await upsert_character(db, "42", "Tony", "https://example.com/42")
            for i in range(3):
                await upsert_thread(db, str(i), f"Thread {i}", f"https://example.com/t/{i}",
                                    None, None, "ongoing")
                await link_character_thread(db, "42", str(i), "ongoing")
            await upsert_thread(db, "99", "Done Thread", "https://example.com/t/99",
                                None, None, "complete")
            await link_character_thread(db, "42", "99", "complete")
            await db.commit()

            counts = await get_thread_counts(db, "42")
            assert counts["ongoing"] == 3
            assert counts["complete"] == 1
            assert counts["total"] == 4
        finally:
            await db.close()


class TestLinkCharacterThread:
    async def test_is_user_last_poster_stored(self):
        """Verify the flag is stored in character_threads table."""
        db = await _get_db()
        try:
            await upsert_character(db, "42", "Tony", "https://example.com/42")
            await upsert_thread(db, "100", "Thread", "https://example.com/t/100",
                                None, None, "ongoing")
            await link_character_thread(db, "42", "100", "ongoing", is_user_last_poster=True)
            await db.commit()

            # Verify it's stored in character_threads directly
            cursor = await db.execute(
                "SELECT is_user_last_poster FROM character_threads WHERE character_id = '42' AND thread_id = '100'"
            )
            row = await cursor.fetchone()
            assert row["is_user_last_poster"] == 1
        finally:
            await db.close()

    async def test_upsert_updates_link_category(self):
        """Updating a character-thread link should change its category."""
        db = await _get_db()
        try:
            await upsert_character(db, "42", "Tony", "https://example.com/42")
            await upsert_thread(db, "100", "Thread", "https://example.com/t/100",
                                None, None, "ongoing")
            await link_character_thread(db, "42", "100", "ongoing", is_user_last_poster=False)
            await link_character_thread(db, "42", "100", "complete", is_user_last_poster=True)
            await db.commit()

            threads = await get_character_threads(db, "42")
            # Should be in complete now, not ongoing
            assert len(threads.ongoing) == 0
            assert len(threads.complete) == 1

            # Verify the flag in the raw table
            cursor = await db.execute(
                "SELECT is_user_last_poster FROM character_threads WHERE character_id = '42' AND thread_id = '100'"
            )
            row = await cursor.fetchone()
            assert row["is_user_last_poster"] == 1
        finally:
            await db.close()


# --- Quote Operations ---

class TestAddQuote:
    async def test_insert_new_quote(self):
        db = await _get_db()
        try:
            await upsert_character(db, "42", "Tony", "https://example.com/42")
            result = await add_quote(db, "42", "I am Iron Man", "100", "Avengers Thread")
            await db.commit()
            assert result is True

            quotes = await get_all_quotes(db, "42")
            assert len(quotes) == 1
            assert quotes[0].quote_text == "I am Iron Man"
            assert quotes[0].source_thread_title == "Avengers Thread"
        finally:
            await db.close()

    async def test_ignores_duplicate_quote(self):
        db = await _get_db()
        try:
            await upsert_character(db, "42", "Tony", "https://example.com/42")
            await add_quote(db, "42", "I am Iron Man")
            await db.commit()
            result = await add_quote(db, "42", "I am Iron Man")
            await db.commit()
            quotes = await get_all_quotes(db, "42")
            assert len(quotes) == 1
        finally:
            await db.close()


class TestGetRandomQuote:
    async def test_returns_none_when_empty(self):
        db = await _get_db()
        try:
            result = await get_random_quote(db, "42")
            assert result is None
        finally:
            await db.close()

    async def test_returns_a_quote(self):
        db = await _get_db()
        try:
            await upsert_character(db, "42", "Tony", "https://example.com/42")
            await add_quote(db, "42", "Quote one text here")
            await add_quote(db, "42", "Quote two text here")
            await db.commit()

            result = await get_random_quote(db, "42")
            assert result is not None
            assert result.quote_text in ("Quote one text here", "Quote two text here")
        finally:
            await db.close()


class TestGetAllQuotes:
    async def test_ordered_by_created_desc(self):
        db = await _get_db()
        try:
            await upsert_character(db, "42", "Tony", "https://example.com/42")
            await add_quote(db, "42", "First quote added")
            await db.commit()
            await add_quote(db, "42", "Second quote added")
            await db.commit()

            quotes = await get_all_quotes(db, "42")
            assert len(quotes) == 2
        finally:
            await db.close()


class TestGetQuoteCount:
    async def test_zero_when_empty(self):
        db = await _get_db()
        try:
            count = await get_quote_count(db, "42")
            assert count == 0
        finally:
            await db.close()

    async def test_counts_correctly(self):
        db = await _get_db()
        try:
            await upsert_character(db, "42", "Tony", "https://example.com/42")
            for i in range(5):
                await add_quote(db, "42", f"Quote number {i}")
            await db.commit()

            count = await get_quote_count(db, "42")
            assert count == 5
        finally:
            await db.close()


# --- Quote Crawl Log ---

class TestQuoteCrawlLog:
    async def test_not_scraped_initially(self):
        db = await _get_db()
        try:
            result = await is_thread_quote_scraped(db, "100", "42")
            assert result is False
        finally:
            await db.close()

    async def test_mark_and_check_scraped(self):
        db = await _get_db()
        try:
            await mark_thread_quote_scraped(db, "100", "42")
            await db.commit()
            result = await is_thread_quote_scraped(db, "100", "42")
            assert result is True
        finally:
            await db.close()

    async def test_scraped_is_per_character(self):
        db = await _get_db()
        try:
            await mark_thread_quote_scraped(db, "100", "42")
            await db.commit()
            assert await is_thread_quote_scraped(db, "100", "99") is False
        finally:
            await db.close()


# --- Profile Field Operations ---

class TestProfileFields:
    async def test_insert_and_get(self):
        db = await _get_db()
        try:
            await upsert_character(db, "42", "Tony", "https://example.com/42")
            await upsert_profile_field(db, "42", "pf-alias", "Iron Man")
            await upsert_profile_field(db, "42", "pf-age", "45")
            await db.commit()

            fields = await get_profile_fields(db, "42")
            assert fields["pf-alias"] == "Iron Man"
            assert fields["pf-age"] == "45"
        finally:
            await db.close()

    async def test_upsert_updates_value(self):
        db = await _get_db()
        try:
            await upsert_character(db, "42", "Tony", "https://example.com/42")
            await upsert_profile_field(db, "42", "pf-alias", "Tony")
            await db.commit()
            await upsert_profile_field(db, "42", "pf-alias", "Iron Man")
            await db.commit()

            fields = await get_profile_fields(db, "42")
            assert fields["pf-alias"] == "Iron Man"
        finally:
            await db.close()

    async def test_empty_fields(self):
        db = await _get_db()
        try:
            fields = await get_profile_fields(db, "nonexistent")
            assert fields == {}
        finally:
            await db.close()


# --- Crawl Status Operations ---

class TestCrawlStatus:
    async def test_set_and_get(self):
        db = await _get_db()
        try:
            await set_crawl_status(db, "last_thread_crawl", "2024-01-01T00:00:00")
            result = await get_crawl_status(db, "last_thread_crawl")
            assert result == "2024-01-01T00:00:00"
        finally:
            await db.close()

    async def test_returns_none_for_missing_key(self):
        db = await _get_db()
        try:
            result = await get_crawl_status(db, "nonexistent_key")
            assert result is None
        finally:
            await db.close()

    async def test_upsert_updates_value(self):
        db = await _get_db()
        try:
            await set_crawl_status(db, "status", "idle")
            await set_crawl_status(db, "status", "crawling")
            result = await get_crawl_status(db, "status")
            assert result == "crawling"
        finally:
            await db.close()
