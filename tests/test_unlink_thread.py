"""Tests for unlink_character_thread (app/models/operations.py).

Covers the "wrong character" cleanup: removing a thread from one character's
tracker must delete that character's link, posts, and quotes for the thread,
while leaving the shared thread row and every other character's data intact.
"""
import os

import pytest
import aiosqlite

from app.database import init_db, DATABASE_PATH
from app.models.operations import (
    upsert_character,
    upsert_thread,
    link_character_thread,
    add_quote,
    get_character_threads,
    unlink_character_thread,
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


async def _get_db():
    db = await aiosqlite.connect(DATABASE_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def _count(db, sql, params):
    cur = await db.execute(sql, params)
    row = await cur.fetchone()
    return row[0]


async def _seed(db):
    # Two characters the same player runs; one shared thread T1, plus T2 (right only).
    await upsert_character(db, "wrong", "Gamora", "https://x/wrong", None, None)
    await upsert_character(db, "right", "Lilith", "https://x/right", None, None)
    await upsert_thread(db, "t1", "Mistaken Tag", "https://f/t1", "1", "RP", "ongoing", "right", "Lilith")
    await upsert_thread(db, "t2", "Other Thread", "https://f/t2", "1", "RP", "ongoing", "right", "Lilith")
    # Both characters got linked to T1 (the bug); only 'right' is in T2.
    await link_character_thread(db, "wrong", "t1", "ongoing", False)
    await link_character_thread(db, "right", "t1", "ongoing", True)
    await link_character_thread(db, "right", "t2", "ongoing", True)
    # Posts (wrong: 2 in t1; right: 3 in t1, 1 in t2)
    for d in ("2026-05-01T10:00:00-04:00", "2026-05-02T10:00:00-04:00"):
        await db.execute("INSERT INTO posts (character_id, thread_id, post_date) VALUES ('wrong','t1',?)", (d,))
    for d in ("2026-05-01T11:00:00-04:00", "2026-05-03T11:00:00-04:00", "2026-05-04T11:00:00-04:00"):
        await db.execute("INSERT INTO posts (character_id, thread_id, post_date) VALUES ('right','t1',?)", (d,))
    await db.execute("INSERT INTO posts (character_id, thread_id, post_date) VALUES ('right','t2','2026-05-05T11:00:00-04:00')")
    # Quotes
    await add_quote(db, "wrong", '"wrong quote in t1"', "t1", "Mistaken Tag")
    await add_quote(db, "right", '"right quote in t1"', "t1", "Mistaken Tag")
    await add_quote(db, "right", '"right quote in t2"', "t2", "Other Thread")
    # quote_crawl_log rows
    await db.execute("INSERT INTO quote_crawl_log (thread_id, character_id) VALUES ('t1','wrong')")
    await db.execute("INSERT INTO quote_crawl_log (thread_id, character_id) VALUES ('t1','right')")
    await db.commit()


class TestUnlinkCharacterThread:
    async def test_removes_only_wrong_characters_data(self):
        db = await _get_db()
        try:
            await _seed(db)
            result = await unlink_character_thread(db, "wrong", "t1")
            assert result is True

            # Wrong character: link, posts, quotes, crawl-log for t1 all gone
            assert await _count(db, "SELECT COUNT(*) FROM character_threads WHERE character_id='wrong' AND thread_id='t1'", ()) == 0
            assert await _count(db, "SELECT COUNT(*) FROM posts WHERE character_id='wrong' AND thread_id='t1'", ()) == 0
            assert await _count(db, "SELECT COUNT(*) FROM quotes WHERE character_id='wrong' AND source_thread_id='t1'", ()) == 0
            assert await _count(db, "SELECT COUNT(*) FROM quote_crawl_log WHERE character_id='wrong' AND thread_id='t1'", ()) == 0

            # Right character: everything in t1 intact
            assert await _count(db, "SELECT COUNT(*) FROM character_threads WHERE character_id='right' AND thread_id='t1'", ()) == 1
            assert await _count(db, "SELECT COUNT(*) FROM posts WHERE character_id='right' AND thread_id='t1'", ()) == 3
            assert await _count(db, "SELECT COUNT(*) FROM quotes WHERE character_id='right' AND source_thread_id='t1'", ()) == 1

            # Shared thread row survives
            assert await _count(db, "SELECT COUNT(*) FROM threads WHERE id='t1'", ()) == 1
        finally:
            await db.close()

    async def test_does_not_touch_other_threads(self):
        db = await _get_db()
        try:
            await _seed(db)
            await unlink_character_thread(db, "wrong", "t1")
            # right's T2 data untouched
            assert await _count(db, "SELECT COUNT(*) FROM character_threads WHERE character_id='right' AND thread_id='t2'", ()) == 1
            assert await _count(db, "SELECT COUNT(*) FROM posts WHERE character_id='right' AND thread_id='t2'", ()) == 1
            assert await _count(db, "SELECT COUNT(*) FROM quotes WHERE character_id='right' AND source_thread_id='t2'", ()) == 1
        finally:
            await db.close()

    async def test_thread_drops_from_character_view(self):
        db = await _get_db()
        try:
            await _seed(db)
            await unlink_character_thread(db, "wrong", "t1")
            ct = await get_character_threads(db, "wrong")
            assert ct.counts.get("total", 0) == 0
        finally:
            await db.close()

    async def test_returns_false_when_no_link(self):
        db = await _get_db()
        try:
            await _seed(db)
            # 'wrong' was never linked to t2
            result = await unlink_character_thread(db, "wrong", "t2")
            assert result is False
        finally:
            await db.close()
