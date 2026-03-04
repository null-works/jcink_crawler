"""Tests for app/services/crawler.py — Crawl orchestration."""
import os
import pytest
from unittest.mock import AsyncMock, patch
import aiosqlite

from app.database import init_db, DATABASE_PATH
from app.models.operations import upsert_character, get_character, get_all_quotes, get_thread_counts, get_character_threads
from app.services.crawler import crawl_character_threads, crawl_character_profile, crawl_single_thread, register_character


@pytest.fixture(autouse=True)
async def fresh_db():
    await init_db()
    yield
    if os.path.exists(DATABASE_PATH):
        try:
            os.unlink(DATABASE_PATH)
        except OSError:
            pass


PROFILE_HTML = """
<html>
<title>Viewing Profile -> Tony Stark</title>
<div class="profile-app group-6">
  <header class="profile-hero">
    <div class="profile-hero-images">
      <div class="profile-hero-img hero-sq-top" style="background-image: url('https://img.com/tony.jpg');"></div>
    </div>
    <div class="profile-hero-info">
      <h1 class="profile-name" data-text="Tony Stark">Tony Stark</h1>
      <h2 class="profile-codename">Iron Man</h2>
    </div>
  </header>
  <aside class="profile-sidebar">
    <div class="profile-card glass profile-dossier-card">
      <dl class="profile-dossier">
        <dt>Age</dt><dd>45</dd>
        <dt>Affiliation</dt><dd>Avengers</dd>
      </dl>
    </div>
  </aside>
</div>
</html>
"""

SEARCH_HTML = """
<html>
<div class="tableborder">
    <a href="/index.php?showtopic=100">Test Thread One</a>
    <a href="/index.php?showforum=20">Some Forum</a>
</div>
<div class="tableborder">
    <a href="/index.php?showtopic=200">Test Thread Two</a>
    <a href="/index.php?showforum=49">Complete Forum</a>
</div>
</html>
"""

THREAD_HTML = """
<html>
<div class="pr-a">
    <div class="pr-j"><a href="/index.php?showuser=99">Steve Rogers</a></div>
    <div class="postcolor">
        <b>"I can do this all day and it is great"</b>
    </div>
</div>
</html>
"""

THREAD_HTML_TONY = """
<html>
<div class="pr-a">
    <div class="pr-j"><a href="/index.php?showuser=42">Tony Stark</a></div>
    <div class="postcolor">
        <b>"I am Iron Man and everyone knows it"</b>
    </div>
</div>
</html>
"""


class TestCrawlCharacterProfile:
    async def test_successful_profile_crawl(self):
        with patch("app.services.crawler.fetch_page_rendered", new_callable=AsyncMock, return_value=PROFILE_HTML):
            result = await crawl_character_profile("42", DATABASE_PATH)

        assert result["name"] == "Tony Stark"
        assert result["fields_count"] == 4  # age, affiliation, codename, square_image
        assert result["group"] == "Red"

        # Verify DB was updated
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            char = await get_character(db, "42")
            assert char is not None
            assert char.name == "Tony Stark"

    async def test_failed_fetch_returns_error(self):
        with patch("app.services.crawler.fetch_page_rendered", new_callable=AsyncMock, return_value=None):
            result = await crawl_character_profile("42", DATABASE_PATH)
        assert "error" in result


class TestCrawlCharacterThreads:
    async def test_returns_error_when_search_fails(self):
        with patch("app.services.crawler.fetch_page", new_callable=AsyncMock, return_value=None):
            result = await crawl_character_threads("42", DATABASE_PATH)
        assert "error" in result

    async def test_handles_board_message_cooldown(self):
        board_msg = "<html><head><title>Board Message</title></head><body>Cooldown</body></html>"
        with patch("app.services.crawler.fetch_page", new_callable=AsyncMock, return_value=board_msg):
            result = await crawl_character_threads("42", DATABASE_PATH)
        assert "error" in result
        assert "cooldown" in result["error"].lower() or "retry" in result["error"].lower()

    async def test_handles_search_redirect(self):
        redirect_html = """
        <html><head>
        <meta http-equiv="refresh" content="0;url=/index.php?act=Search&CODE=show&searchid=abc">
        </head></html>
        """
        board_msg = "<html><head><title>Board Message</title></head><body>X</body></html>"

        # Retry loop tries up to 3 attempts: each attempt does search + redirect follow
        with patch("app.services.crawler.fetch_page", new_callable=AsyncMock,
                    side_effect=[redirect_html, board_msg,
                                 redirect_html, board_msg,
                                 redirect_html, board_msg]), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await crawl_character_threads("42", DATABASE_PATH)
        assert "error" in result

    async def test_successful_crawl_with_threads(self):
        """Full crawl flow: search -> parse threads -> fetch each -> save to DB."""
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            await upsert_character(db, "42", "Tony Stark", "https://example.com/42")

        async def mock_fetch_page(url):
            if "act=Search" in url or "searchid" in url:
                return SEARCH_HTML
            if "showtopic=100" in url:
                return THREAD_HTML_TONY
            if "showtopic=200" in url:
                return THREAD_HTML
            if "showuser=" in url:
                return PROFILE_HTML
            return "<html></html>"

        with patch("app.services.crawler.fetch_page", new_callable=AsyncMock, side_effect=mock_fetch_page), \
             patch("app.services.crawler.fetch_page_with_delay", new_callable=AsyncMock, side_effect=mock_fetch_page):
            result = await crawl_character_threads("42", DATABASE_PATH)

        assert "error" not in result
        assert result.get("ongoing", 0) + result.get("complete", 0) == 2

        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            counts = await get_thread_counts(db, "42")
            assert counts["total"] == 2

    async def test_extracts_quotes_during_crawl(self):
        """Crawler should extract quotes from thread pages by character name."""
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            await upsert_character(db, "42", "Tony Stark", "https://example.com/42")

        single_thread_html = """
        <html>
        <div class="tableborder">
            <a href="/index.php?showtopic=100">Tony's Thread</a>
            <a href="/index.php?showforum=20">RP Forum</a>
        </div>
        </html>
        """

        async def mock_fetch(url):
            if "act=Search" in url:
                return single_thread_html
            if "showtopic=100" in url:
                return THREAD_HTML_TONY
            if "showuser=" in url:
                return PROFILE_HTML
            return "<html></html>"

        with patch("app.services.crawler.fetch_page", new_callable=AsyncMock, side_effect=mock_fetch), \
             patch("app.services.crawler.fetch_page_with_delay", new_callable=AsyncMock, side_effect=mock_fetch):
            result = await crawl_character_threads("42", DATABASE_PATH)

        assert result.get("quotes_added", 0) >= 1

        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            quotes = await get_all_quotes(db, "42")
            assert len(quotes) >= 1
            assert "Iron Man" in quotes[0].quote_text


class TestRegisterCharacter:
    async def test_successful_registration(self):
        async def mock_fetch(url):
            if "showuser=" in url:
                return PROFILE_HTML
            if "act=Search" in url:
                return "<html></html>"
            return "<html></html>"

        with patch("app.services.crawler.fetch_page_rendered", new_callable=AsyncMock, side_effect=mock_fetch), \
             patch("app.services.crawler.fetch_page", new_callable=AsyncMock, side_effect=mock_fetch), \
             patch("app.services.crawler.fetch_page_with_delay", new_callable=AsyncMock, side_effect=mock_fetch):
            result = await register_character("42", DATABASE_PATH)

        assert result["character_id"] == "42"
        assert result["profile"]["name"] == "Tony Stark"

    async def test_profile_failure_stops_registration(self):
        with patch("app.services.crawler.fetch_page_rendered", new_callable=AsyncMock, return_value=None):
            result = await register_character("42", DATABASE_PATH)
        assert "error" in result


SINGLE_THREAD_PAGE = """
<html>
<head><title>The Forum -> A Great Adventure</title></head>
<body>
<a href="/index.php?showforum=20">Some Forum</a>
<div class="pr-a">
    <div class="pr-j"><a href="/index.php?showuser=42">Tony Stark</a></div>
    <div class="postcolor">
        <b>"I am Iron Man and everyone knows it"</b>
    </div>
</div>
<div class="pr-a">
    <div class="pr-j"><a href="/index.php?showuser=99">Steve Rogers</a></div>
    <div class="postcolor">
        <b>"I can do this all day and it is great"</b>
    </div>
</div>
</body>
</html>
"""


class TestCrawlSingleThread:
    async def test_successful_single_thread_crawl(self):
        """Crawl a single thread and verify DB updates.

        The webhook says user 42 posted, but the HTML still shows user 99 as
        last poster (stale page). The crawler should trust the webhook user_id.
        """
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            await upsert_character(db, "42", "Tony Stark", "https://example.com/42")
            await upsert_character(db, "99", "Steve Rogers", "https://example.com/99")

        async def mock_fetch(url):
            if "showtopic=100" in url:
                return SINGLE_THREAD_PAGE
            if "showuser=" in url:
                return PROFILE_HTML
            return "<html></html>"

        with patch("app.services.crawler.fetch_page", new_callable=AsyncMock, side_effect=mock_fetch), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await crawl_single_thread("100", DATABASE_PATH, user_id="42")

        assert "error" not in result
        assert result["thread_id"] == "100"
        assert result["title"] == "A Great Adventure"
        # Webhook user_id is trusted as last poster even when HTML is stale
        assert result["last_poster"] == "Tony Stark"

        # Verify thread was linked to requesting user
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            threads = await get_character_threads(db, "42")
            assert threads.counts["total"] >= 1

    async def test_single_thread_with_forum_id(self):
        """Forum ID should determine thread category."""
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            await upsert_character(db, "42", "Tony Stark", "https://example.com/42")

        async def mock_fetch(url):
            if "showtopic=" in url:
                return SINGLE_THREAD_PAGE
            if "showuser=" in url:
                return PROFILE_HTML
            return "<html></html>"

        with patch("app.services.crawler.fetch_page", new_callable=AsyncMock, side_effect=mock_fetch), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await crawl_single_thread("100", DATABASE_PATH, user_id="42", forum_id="49")

        assert result["category"] == "complete"

    async def test_single_thread_extracts_quotes(self):
        """Quotes should be extracted for known characters."""
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            await upsert_character(db, "42", "Tony Stark", "https://example.com/42")

        async def mock_fetch(url):
            if "showtopic=" in url:
                return SINGLE_THREAD_PAGE
            if "showuser=" in url:
                return PROFILE_HTML
            return "<html></html>"

        with patch("app.services.crawler.fetch_page", new_callable=AsyncMock, side_effect=mock_fetch), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await crawl_single_thread("100", DATABASE_PATH, user_id="42")

        assert result["quotes_added"] >= 1
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            quotes = await get_all_quotes(db, "42")
            assert any("Iron Man" in q.quote_text for q in quotes)

    async def test_single_thread_links_other_authors(self):
        """Thread should be linked to other known characters who posted in it."""
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            await upsert_character(db, "42", "Tony Stark", "https://example.com/42")
            await upsert_character(db, "99", "Steve Rogers", "https://example.com/99")

        async def mock_fetch(url):
            if "showtopic=" in url:
                return SINGLE_THREAD_PAGE
            if "showuser=" in url:
                return PROFILE_HTML
            return "<html></html>"

        with patch("app.services.crawler.fetch_page", new_callable=AsyncMock, side_effect=mock_fetch), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await crawl_single_thread("100", DATABASE_PATH, user_id="42")

        # Steve (user 99) also posted, so should be linked
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            threads = await get_character_threads(db, "99")
            assert threads.counts["total"] >= 1

    async def test_single_thread_failed_fetch(self):
        with patch("app.services.crawler.fetch_page", new_callable=AsyncMock, return_value=None), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await crawl_single_thread("100", DATABASE_PATH)
        assert "error" in result

    async def test_single_thread_board_message(self):
        board_msg = "<html><head><title>Board Message</title></head><body>X</body></html>"
        with patch("app.services.crawler.fetch_page", new_callable=AsyncMock, return_value=board_msg), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await crawl_single_thread("100", DATABASE_PATH)
        assert "error" in result
