"""Tests for the HTML parser service."""
import pytest
from app.services.parser import (
    parse_last_poster,
    extract_quotes_from_html,
    parse_avatar_from_profile,
    categorize_thread,
    is_board_message,
    parse_search_redirect,
    parse_search_results,
    parse_thread_pagination,
    parse_profile_page,
    ParsedThread,
    ParsedLastPoster,
    ParsedProfile,
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

    def test_redirect_with_full_url(self):
        html = """
        <html><head>
        <meta http-equiv="refresh" content="0;url=https://therewasanidea.jcink.net/index.php?act=Search&searchid=xyz">
        </head></html>
        """
        result = parse_search_redirect(html)
        assert result == "https://therewasanidea.jcink.net/index.php?act=Search&searchid=xyz"


# --- Additional Parser Tests ---

class TestParseSearchResults:
    def test_parses_threads_from_search(self):
        html = """
        <html>
        <div class="tableborder">
            <a href="/index.php?showtopic=100">Thread Alpha</a>
            <a href="/index.php?showforum=20">RP Forum</a>
        </div>
        <div class="tableborder">
            <a href="/index.php?showtopic=200">Thread Beta</a>
            <a href="/index.php?showforum=49">Complete Forum</a>
        </div>
        </html>
        """
        threads, page_urls = parse_search_results(html)
        assert len(threads) == 2
        assert threads[0].thread_id == "100"
        assert threads[0].title == "Thread Alpha"
        assert threads[0].category == "ongoing"
        assert threads[1].thread_id == "200"
        assert threads[1].category == "complete"

    def test_deduplicates_threads(self):
        html = """
        <html>
        <div class="tableborder">
            <a href="/index.php?showtopic=100">Thread Dup</a>
        </div>
        <div class="tableborder">
            <a href="/index.php?showtopic=100">Thread Dup Again</a>
        </div>
        </html>
        """
        threads, _ = parse_search_results(html)
        assert len(threads) == 1

    def test_skips_excluded_forums(self):
        html = """
        <html>
        <div class="tableborder">
            <a href="/index.php?showtopic=100">Should Skip</a>
            <a href="/index.php?showforum=4">Excluded Forum</a>
        </div>
        <div class="tableborder">
            <a href="/index.php?showtopic=200">Should Include</a>
            <a href="/index.php?showforum=20">Good Forum</a>
        </div>
        </html>
        """
        threads, _ = parse_search_results(html)
        assert len(threads) == 1
        assert threads[0].thread_id == "200"

    def test_skips_excluded_forum_names(self):
        html = """
        <html>
        <div class="tableborder">
            <a href="/index.php?showtopic=100">Guidebook Thread</a>
            <a href="/index.php?showforum=999">Guidebook</a>
        </div>
        </html>
        """
        threads, _ = parse_search_results(html)
        assert len(threads) == 0

    def test_skips_auto_claims(self):
        html = """
        <html>
        <div class="tableborder">
            <a href="/index.php?showtopic=100">From: Auto Claims — Claim Title</a>
            <a href="/index.php?showforum=20">Some Forum</a>
        </div>
        </html>
        """
        threads, _ = parse_search_results(html)
        assert len(threads) == 0

    def test_empty_search_results(self):
        html = "<html><body>No results</body></html>"
        threads, page_urls = parse_search_results(html)
        assert threads == []
        assert page_urls == []

    def test_no_topic_link_skips_div(self):
        html = """
        <html>
        <div class="tableborder">
            <span>No links here</span>
        </div>
        </html>
        """
        threads, _ = parse_search_results(html)
        assert len(threads) == 0

    def test_builds_full_url_for_relative_links(self):
        html = """
        <html>
        <div class="tableborder">
            <a href="/index.php?showtopic=100">Test</a>
        </div>
        </html>
        """
        threads, _ = parse_search_results(html)
        assert threads[0].url.startswith("https://")

    def test_preserves_absolute_urls(self):
        html = """
        <html>
        <div class="tableborder">
            <a href="https://therewasanidea.jcink.net/index.php?showtopic=100">Test</a>
        </div>
        </html>
        """
        threads, _ = parse_search_results(html)
        assert threads[0].url == "https://therewasanidea.jcink.net/index.php?showtopic=100"


class TestParseThreadPagination:
    def test_single_page_returns_zero(self):
        html = "<html><body>No pagination</body></html>"
        assert parse_thread_pagination(html) == 0

    def test_finds_max_st(self):
        html = """
        <html>
        <div class="pagination">
            <a href="/index.php?showtopic=1&st=0">1</a>
            <a href="/index.php?showtopic=1&st=25">2</a>
            <a href="/index.php?showtopic=1&st=50">3</a>
        </div>
        </html>
        """
        assert parse_thread_pagination(html) == 50

    def test_handles_single_pagination_link(self):
        html = """
        <html>
        <div class="pagination">
            <a href="/index.php?showtopic=1&st=25">Next</a>
        </div>
        </html>
        """
        assert parse_thread_pagination(html) == 25


class TestParseProfilePage:
    def test_extracts_full_profile(self):
        html = """
        <html>
        <div class="profile-name">Tony Stark</div>
        <div class="profile-group">Avengers</div>
        <div class="hero-sq-top" style="background-image: url('https://img.com/tony.jpg')"></div>
        <div class="pf-alias">Iron Man</div>
        <div class="pf-age">45</div>
        </html>
        """
        profile = parse_profile_page(html, "42")
        assert profile.user_id == "42"
        assert profile.name == "Tony Stark"
        assert profile.group_name == "Avengers"
        assert profile.avatar_url == "https://img.com/tony.jpg"
        assert profile.fields["pf-alias"] == "Iron Man"
        assert profile.fields["pf-age"] == "45"

    def test_defaults_when_missing(self):
        html = "<html><body>Bare page</body></html>"
        profile = parse_profile_page(html, "99")
        assert profile.name == "Unknown"
        assert profile.group_name is None
        assert profile.avatar_url is None
        assert profile.fields == {}

    def test_extracts_data_field_attributes(self):
        html = """
        <html>
        <div class="profile-name">Test User</div>
        <div data-field="fav_color">Blue</div>
        <div data-field="motto">Live free</div>
        </html>
        """
        profile = parse_profile_page(html, "1")
        assert profile.fields["fav_color"] == "Blue"
        assert profile.fields["motto"] == "Live free"

    def test_group_name_from_alternative_class(self):
        html = """
        <html>
        <div class="profile-name">Test</div>
        <div class="group-name">X-Men</div>
        </html>
        """
        profile = parse_profile_page(html, "5")
        assert profile.group_name == "X-Men"


class TestParseLastPosterExtended:
    def test_single_post_page(self):
        html = """
        <div class="pr-wrap">
            <div class="pr-name"><a href="/index.php?showuser=1">Only Poster</a></div>
        </div>
        """
        result = parse_last_poster(html)
        assert result.name == "Only Poster"
        assert result.user_id == "1"

    def test_poster_without_user_link(self):
        """Guest posters may not have a user link."""
        html = """
        <div class="pr-wrap">
            <div class="pr-name">Guest User</div>
        </div>
        """
        result = parse_last_poster(html)
        assert result.name == "Guest User"
        assert result.user_id is None

    def test_no_name_element(self):
        html = """
        <div class="pr-wrap">
            <div class="other-stuff">content</div>
        </div>
        """
        result = parse_last_poster(html)
        assert result is None


class TestExtractQuotesExtended:
    def test_case_insensitive_character_match(self):
        html = """
        <div class="pr-wrap">
            <div class="pr-name">TONY STARK</div>
            <div class="pr-body">
                <b>"This is a case test quote here"</b>
            </div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "tony stark")
        assert len(quotes) == 1

    def test_skips_non_quote_bold_text(self):
        html = """
        <div class="pr-wrap">
            <div class="pr-name">Tony Stark</div>
            <div class="pr-body">
                <b>This is bold but not a quote</b>
            </div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Tony Stark")
        assert len(quotes) == 0

    def test_truncates_long_quotes(self):
        long_text = "A " * 300  # 600 chars
        html = f"""
        <div class="pr-wrap">
            <div class="pr-name">Tony Stark</div>
            <div class="pr-body">
                <b>"{long_text}"</b>
            </div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Tony Stark")
        assert len(quotes) == 1
        assert len(quotes[0]["text"]) <= 503  # 500 + "..."

    def test_handles_single_quotes(self):
        html = """
        <div class="pr-wrap">
            <div class="pr-name">Tony Stark</div>
            <div class="pr-body">
                <b>'This uses single quotes around it'</b>
            </div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Tony Stark")
        assert len(quotes) == 1

    def test_multiple_posts_different_characters(self):
        html = """
        <div class="pr-wrap">
            <div class="pr-name">Tony Stark</div>
            <div class="pr-body"><b>"Tony says something really cool"</b></div>
        </div>
        <div class="pr-wrap">
            <div class="pr-name">Steve Rogers</div>
            <div class="pr-body"><b>"Steve says something really cool"</b></div>
        </div>
        <div class="pr-wrap">
            <div class="pr-name">Tony Stark</div>
            <div class="pr-body"><b>"Tony speaks again for real"</b></div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Tony Stark")
        assert len(quotes) == 2

    def test_no_body_in_post(self):
        html = """
        <div class="pr-wrap">
            <div class="pr-name">Tony Stark</div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Tony Stark")
        assert len(quotes) == 0

    def test_uses_postcolor_class(self):
        html = """
        <div class="pr-wrap">
            <div class="pr-name">Tony Stark</div>
            <div class="postcolor">
                <b>"Quote from postcolor div here"</b>
            </div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Tony Stark")
        assert len(quotes) == 1


class TestBoardMessageExtended:
    def test_no_title_tag(self):
        html = "<html><body>No title</body></html>"
        assert is_board_message(html) is False

    def test_partial_match_in_title(self):
        html = "<html><head><title>Some Board Message Here</title></head></html>"
        assert is_board_message(html) is True


class TestParseAvatarExtended:
    def test_double_quoted_url(self):
        html = """
        <div class="hero-sq-top" style='background-image: url("https://example.com/av.png")'></div>
        """
        assert parse_avatar_from_profile(html) == "https://example.com/av.png"

    def test_fallback_to_generic_background_image(self):
        html = """
        <div class="some-other-class" style="background-image: url(https://example.com/fallback.jpg)"></div>
        """
        assert parse_avatar_from_profile(html) == "https://example.com/fallback.jpg"
