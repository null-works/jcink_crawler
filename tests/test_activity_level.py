"""Tests for the computed activity level (app/models/dashboard_queries.py).

Covers the pure tier-bucketing helper and the DB-backed averaging query
that powers the /api/character/{id}/activity-level endpoint and the
dashboard badge.
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import aiosqlite

from app.database import init_db, DATABASE_PATH
from app.models.operations import upsert_character
from app.models.dashboard_queries import (
    activity_tier,
    get_character_activity_level,
)

# Fixed "now" so the 3-month window is deterministic: the window is the
# three complete calendar months before June 2026 -> March, April, May 2026
# (half-open [2026-03-01, 2026-06-01)). June is the in-progress month and
# must be excluded.
NOW = datetime(2026, 6, 4, 12, 0, tzinfo=ZoneInfo("America/New_York"))


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


async def _add_posts(db, character_id, dates):
    """Insert posts for a character at the given ISO date strings."""
    for d in dates:
        await db.execute(
            "INSERT INTO posts (character_id, thread_id, post_date) VALUES (?, ?, ?)",
            (character_id, "t1", d),
        )
    await db.commit()


class TestActivityTier:
    def test_zero_is_inactive(self):
        assert activity_tier(0) == "inactive"
        assert activity_tier(0.0) == "inactive"

    def test_fractional_nonzero_rounds_up_to_low(self):
        # 1 post across 3 months averages ~0.33 — present, not inactive.
        assert activity_tier(0.33) == "low"

    def test_low_band(self):
        assert activity_tier(1) == "low"
        assert activity_tier(5) == "low"
        assert activity_tier(5.4) == "low"  # rounds to 5

    def test_medium_band(self):
        assert activity_tier(5.5) == "medium"  # rounds to 6
        assert activity_tier(6) == "medium"
        assert activity_tier(10) == "medium"

    def test_high_band(self):
        assert activity_tier(10.5) == "high"  # rounds to 11
        assert activity_tier(11) == "high"
        assert activity_tier(99) == "high"


class TestGetCharacterActivityLevel:
    async def test_no_posts_is_inactive(self):
        db = await _get_db()
        try:
            await upsert_character(db, "1", "Gamora", "https://x/1", None, None)
            al = await get_character_activity_level(db, "1", now=NOW)
            assert al["tier"] == "inactive"
            assert al["avg_posts_per_month"] == 0
            assert al["window_posts"] == 0
            assert al["window_start"] == "2026-03-01"
            assert al["window_end"] == "2026-06-01"
        finally:
            await db.close()

    async def test_medium_band_average(self):
        db = await _get_db()
        try:
            await upsert_character(db, "1", "Gamora", "https://x/1", None, None)
            # 21 posts across the window -> avg 7.0 -> medium.
            # post_date carries a tz offset, matching the ACP pipeline's
            # _unix_to_iso_datetime() output (-05:00 EST pre-DST, -04:00 EDT after).
            dates = (
                [f"2026-03-{d:02d}T10:00:00-05:00" for d in range(1, 8)]
                + [f"2026-04-{d:02d}T10:00:00-04:00" for d in range(1, 8)]
                + [f"2026-05-{d:02d}T10:00:00-04:00" for d in range(1, 8)]
            )
            await _add_posts(db, "1", dates)
            al = await get_character_activity_level(db, "1", now=NOW)
            assert al["window_posts"] == 21
            assert al["avg_posts_per_month"] == 7.0
            assert al["tier"] == "medium"
            assert al["label"] == "Medium Activity"
            assert al["css"] == "badge-active"
        finally:
            await db.close()

    async def test_excludes_current_and_pre_window_months(self):
        db = await _get_db()
        try:
            await upsert_character(db, "1", "Lilith", "https://x/1", None, None)
            await _add_posts(db, "1", [
                "2026-02-15T10:00:00-05:00",  # before window — excluded
                "2026-03-10T10:00:00-04:00",  # in window
                "2026-05-20T10:00:00-04:00",  # in window
                "2026-06-01T10:00:00-04:00",  # current (partial) month — excluded
                "2026-06-03T10:00:00-04:00",  # current (partial) month — excluded
            ])
            al = await get_character_activity_level(db, "1", now=NOW)
            assert al["window_posts"] == 2  # only the two in-window posts
            assert al["tier"] == "low"
        finally:
            await db.close()

    async def test_high_band(self):
        db = await _get_db()
        try:
            await upsert_character(db, "1", "Novi", "https://x/1", None, None)
            # 36 posts -> avg 12 -> high
            dates = [f"2026-04-{(i % 28) + 1:02d}T{(i % 12) + 1:02d}:00:00-04:00" for i in range(36)]
            await _add_posts(db, "1", dates)
            al = await get_character_activity_level(db, "1", now=NOW)
            assert al["window_posts"] == 36
            assert al["avg_posts_per_month"] == 12.0
            assert al["tier"] == "high"
            assert al["css"] == "badge-very-active"
        finally:
            await db.close()

    async def test_offset_bearing_dates_at_window_boundary(self):
        """The lexicographic YYYY-MM-01 bound must handle offset-bearing
        timestamps: the last instant of the window is included, the first
        instant of the current (excluded) month is not — regardless of the
        DST offset suffix sitting past the date prefix."""
        db = await _get_db()
        try:
            await upsert_character(db, "1", "Beja", "https://x/1", None, None)
            await _add_posts(db, "1", [
                "2026-05-31T23:59:59-04:00",  # last instant in window — included
                "2026-06-01T00:00:00-04:00",  # first instant of current month — excluded
                "2026-06-01T00:00:00-05:00",  # same, different offset — still excluded
            ])
            al = await get_character_activity_level(db, "1", now=NOW)
            assert al["window_posts"] == 1
        finally:
            await db.close()
