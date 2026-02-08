"""Tests for the HTML parser service."""
import pytest
from app.services.parser import (
    parse_last_poster,
    extract_quotes_from_html,
    parse_avatar_from_profile,
    categorize_thread,
    is_board_message,
    parse_search_redirect,
)


class TestCategorizeThread:
    def test_complete_forum(self):
        assert categorize_thread("49") == "complete"

    def test_incomplete_forum(self):
        assert categorize_thread("59") == "incomplete"

    def test_comms_forum(self):
        assert categorize_thread("31") == "comms"

    def test_ongoing_default(self):
        assert categorize_thread("99") == "ongoing"
        assert categorize_thread(None) == "ongoing"


class TestParseLastPoster:
    def test_extracts_last_poster(self):
        html = """
        <div class="pr-wrap">
            <div class="pr-name"><a href="/index.php?showuser=1">First Poster</a></div>
        </div>
        <div class="pr-wrap">
            <div class="pr-name"><a href="/index.php?showuser=42">Last Poster</a></div>
        </div>
        """
        result = parse_last_poster(html)
        assert result is not None
        assert result.name == "Last Poster"
        assert result.user_id == "42"

    def test_no_posts(self):
        html = "<div>No posts here</div>"
        assert parse_last_poster(html) is None


class TestExtractQuotes:
    def test_extracts_bold_dialog(self):
        html = """
        <div class="pr-wrap">
            <div class="pr-name">Tony Stark</div>
            <div class="pr-body">
                <b>"I am Iron Man and this is my quote"</b>
                <p>Some narrative text</p>
                <strong>"Another great dialog line here"</strong>
            </div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Tony Stark")
        assert len(quotes) == 2
        assert quotes[0]["text"] == "I am Iron Man and this is my quote"
        assert quotes[1]["text"] == "Another great dialog line here"

    def test_skips_other_characters(self):
        html = """
        <div class="pr-wrap">
            <div class="pr-name">Steve Rogers</div>
            <div class="pr-body">
                <b>"I can do this all day"</b>
            </div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Tony Stark")
        assert len(quotes) == 0

    def test_skips_short_quotes(self):
        html = """
        <div class="pr-wrap">
            <div class="pr-name">Tony Stark</div>
            <div class="pr-body">
                <b>"Hi"</b>
                <b>"Yes no"</b>
            </div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Tony Stark")
        assert len(quotes) == 0

    def test_handles_curly_quotes(self):
        html = """
        <div class="pr-wrap">
            <div class="pr-name">Tony Stark</div>
            <div class="pr-body">
                <b>\u201cThis uses fancy curly quotes here\u201d</b>
            </div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Tony Stark")
        assert len(quotes) == 1
        assert "fancy curly" in quotes[0]["text"]


class TestParseAvatar:
    def test_extracts_from_hero(self):
        html = """
        <div class="hero-sq-top" style="background-image: url('https://example.com/avatar.jpg')"></div>
        """
        assert parse_avatar_from_profile(html) == "https://example.com/avatar.jpg"

    def test_extracts_from_profile_gif(self):
        html = """
        <div class="profile-gif" style="background-image: url(https://example.com/gif.gif)"></div>
        """
        assert parse_avatar_from_profile(html) == "https://example.com/gif.gif"

    def test_no_avatar(self):
        html = "<div>No avatar</div>"
        assert parse_avatar_from_profile(html) is None


class TestBoardMessage:
    def test_detects_board_message(self):
        html = "<html><head><title>Board Message</title></head><body>Cooldown</body></html>"
        assert is_board_message(html) is True

    def test_normal_page(self):
        html = "<html><head><title>Search Results</title></head><body>Results</body></html>"
        assert is_board_message(html) is False


class TestSearchRedirect:
    def test_detects_redirect(self):
        html = """
        <html><head>
        <meta http-equiv="refresh" content="0;url=/index.php?act=Search&CODE=show&searchid=abc123">
        </head></html>
        """
        result = parse_search_redirect(html)
        assert result is not None
        assert "searchid=abc123" in result

    def test_no_redirect(self):
        html = "<html><head><title>Results</title></head></html>"
        assert parse_search_redirect(html) is None
