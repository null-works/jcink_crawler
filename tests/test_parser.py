"""Tests for the HTML parser service."""
import pytest
from app.services.parser import (
    parse_last_poster,
    extract_quotes_from_html,
    parse_avatar_from_profile,
    parse_application_url,
    parse_power_grid,
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
        <div class="pr-a">
            <div class="pr-j"><a href="/index.php?showuser=1">First Poster</a></div>
        </div>
        <div class="pr-a">
            <div class="pr-j"><a href="/index.php?showuser=42">Last Poster</a></div>
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
        <div class="pr-a">
            <div class="pr-j">Tony Stark</div>
            <div class="postcolor">
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
        <div class="pr-a">
            <div class="pr-j">Steve Rogers</div>
            <div class="postcolor">
                <b>"I can do this all day"</b>
            </div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Tony Stark")
        assert len(quotes) == 0

    def test_skips_short_quotes(self):
        html = """
        <div class="pr-a">
            <div class="pr-j">Tony Stark</div>
            <div class="postcolor">
                <b>"Hi"</b>
                <b>"Yes no"</b>
            </div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Tony Stark")
        assert len(quotes) == 0

    def test_handles_curly_quotes(self):
        html = """
        <div class="pr-a">
            <div class="pr-j">Tony Stark</div>
            <div class="postcolor">
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

    def test_pagination_excludes_first_page(self):
        """page_urls should not include st=0 since the first page is already parsed."""
        html = """
        <html>
        <div class="pagination">
            <a href="/index.php?act=Search&CODE=show&searchid=abc&st=0">1</a>
            <a href="/index.php?act=Search&CODE=show&searchid=abc&st=25">2</a>
            <a href="/index.php?act=Search&CODE=show&searchid=abc&st=50">3</a>
        </div>
        <div class="tableborder">
            <a href="/index.php?showtopic=100">Thread One</a>
        </div>
        </html>
        """
        threads, page_urls = parse_search_results(html)
        # page_urls should only contain st=25 and st=50, not st=0
        assert len(page_urls) == 2
        assert all("st=0" not in url for url in page_urls)
        assert any("st=25" in url for url in page_urls)
        assert any("st=50" in url for url in page_urls)

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
        profile = parse_profile_page(html, "42")
        assert profile.user_id == "42"
        assert profile.name == "Tony Stark"
        assert profile.group_name == "Red"
        assert profile.avatar_url == "https://img.com/tony.jpg"
        assert profile.fields["age"] == "45"
        assert profile.fields["affiliation"] == "Avengers"
        assert profile.fields["codename"] == "Iron Man"

    def test_defaults_when_missing(self):
        html = "<html><body>Bare page</body></html>"
        profile = parse_profile_page(html, "99")
        assert profile.name == "Unknown"
        assert profile.group_name is None
        assert profile.avatar_url is None
        assert profile.fields == {}

    def test_name_fallback_to_title(self):
        html = """
        <html>
        <title>Viewing Profile -> Steve Rogers</title>
        </html>
        """
        profile = parse_profile_page(html, "1")
        assert profile.name == "Steve Rogers"

    def test_fields_skip_no_information(self):
        html = """
        <html>
        <h1 class="profile-name">Test</h1>
        <dl class="profile-dossier">
          <dt>Face Claim</dt><dd>No Information</dd>
          <dt>Species</dt><dd>human</dd>
        </dl>
        </html>
        """
        profile = parse_profile_page(html, "1")
        assert "face claim" not in profile.fields
        assert profile.fields["species"] == "human"

    def test_group_from_class(self):
        html = """
        <html>
        <div class="profile-app group-11">
          <h1 class="profile-name">Test</h1>
        </div>
        </html>
        """
        profile = parse_profile_page(html, "5")
        assert profile.group_name == "Purple"

    def test_avatar_fallback_to_gif(self):
        html = """
        <html>
        <h1 class="profile-name">Test</h1>
        <div class="profile-gif" style="background-image: url('https://img.com/gif.gif');"></div>
        </html>
        """
        profile = parse_profile_page(html, "5")
        assert profile.avatar_url == "https://img.com/gif.gif"


class TestParseLastPosterExtended:
    def test_single_post_page(self):
        html = """
        <div class="pr-a">
            <div class="pr-j"><a href="/index.php?showuser=1">Only Poster</a></div>
        </div>
        """
        result = parse_last_poster(html)
        assert result.name == "Only Poster"
        assert result.user_id == "1"

    def test_poster_without_user_link(self):
        """Guest posters may not have a user link."""
        html = """
        <div class="pr-a">
            <div class="pr-j">Guest User</div>
        </div>
        """
        result = parse_last_poster(html)
        assert result.name == "Guest User"
        assert result.user_id is None

    def test_no_name_element(self):
        html = """
        <div class="pr-a">
            <div class="other-stuff">content</div>
        </div>
        """
        result = parse_last_poster(html)
        assert result is None


class TestExtractQuotesExtended:
    def test_case_insensitive_character_match(self):
        html = """
        <div class="pr-a">
            <div class="pr-j">TONY STARK</div>
            <div class="postcolor">
                <b>"This is a case test quote here"</b>
            </div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "tony stark")
        assert len(quotes) == 1

    def test_skips_non_quote_bold_text(self):
        html = """
        <div class="pr-a">
            <div class="pr-j">Tony Stark</div>
            <div class="postcolor">
                <b>This is bold but not a quote</b>
            </div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Tony Stark")
        assert len(quotes) == 0

    def test_truncates_long_quotes(self):
        long_text = "A " * 300  # 600 chars
        html = f"""
        <div class="pr-a">
            <div class="pr-j">Tony Stark</div>
            <div class="postcolor">
                <b>"{long_text}"</b>
            </div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Tony Stark")
        assert len(quotes) == 1
        assert len(quotes[0]["text"]) <= 503  # 500 + "..."

    def test_handles_single_quotes(self):
        html = """
        <div class="pr-a">
            <div class="pr-j">Tony Stark</div>
            <div class="postcolor">
                <b>'This uses single quotes around it'</b>
            </div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Tony Stark")
        assert len(quotes) == 1

    def test_multiple_posts_different_characters(self):
        html = """
        <div class="pr-a">
            <div class="pr-j">Tony Stark</div>
            <div class="postcolor"><b>"Tony says something really cool"</b></div>
        </div>
        <div class="pr-a">
            <div class="pr-j">Steve Rogers</div>
            <div class="postcolor"><b>"Steve says something really cool"</b></div>
        </div>
        <div class="pr-a">
            <div class="pr-j">Tony Stark</div>
            <div class="postcolor"><b>"Tony speaks again for real"</b></div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Tony Stark")
        assert len(quotes) == 2

    def test_no_body_in_post(self):
        html = """
        <div class="pr-a">
            <div class="pr-j">Tony Stark</div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Tony Stark")
        assert len(quotes) == 0

    def test_real_twai_theme_structure(self):
        """Test with the actual TWAI theme structure: .pr-a > ... > .pr-j + .postcolor."""
        html = """
        <div class="pr-a">
            <div class="pr-e"><table><tr><td>
                <div class="pr-g">
                    <div class="pr-h">
                        <div class="pr-i">
                            <div class="pr-j"><a href="/index.php?showuser=4">Jessica Jones</a></div>
                        </div>
                    </div>
                </div>
                <div class="pr-f">
                    <center>details</center>
                    <div class="postcolor" id="pid_123">
                        <b>\u201cI didn\u2019t ask for this but here we are\u201d</b>
                        <p>She crossed her arms and stared out the window.</p>
                        <b>\u201cJust another day in hell\u2019s kitchen honestly\u201d</b>
                    </div>
                </div>
            </td></tr></table></div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Jessica Jones")
        assert len(quotes) == 2
        assert "didn\u2019t ask for this" in quotes[0]["text"]
        assert "hell\u2019s kitchen" in quotes[1]["text"]

    def test_italic_not_extracted(self):
        """Italic/em tags are narrative text, not dialog — must NOT be extracted."""
        html = """
        <div class="pr-a">
            <div class="pr-j">Tony Stark</div>
            <div class="postcolor">
                <i>"This is italic narrative not dialog"</i>
                <em>"Emphasis is also narrative text here"</em>
            </div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Tony Stark")
        assert len(quotes) == 0

    def test_colored_span_dialog(self):
        """Colored spans without bold/italic should be extracted."""
        html = """
        <div class="pr-a">
            <div class="pr-j">Tony Stark</div>
            <div class="postcolor">
                <span style="color: #CE7E00;">"This is colored dialog without bold"</span>
            </div>
        </div>
        """
        quotes = extract_quotes_from_html(html, "Tony Stark")
        assert len(quotes) == 1
        assert "colored dialog without bold" in quotes[0]["text"]

    def test_colored_span_with_bold_child_not_double_counted(self):
        """Colored span wrapping a bold tag should not produce duplicates."""
        html = """
        <div class="pr-a">
            <div class="pr-j">Tony Stark</div>
            <div class="postcolor">
                <span style="color: #CE7E00;"><b>"Should only appear once in results"</b></span>
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


class TestParseProfilePowerGrid:
    """Test power grid extraction from profile-stat elements."""

    def test_extracts_power_grid_from_profile(self):
        html = """
        <html>
        <h1 class="profile-name">Test Character</h1>
        <div class="profile-card-content">
          <div class="profile-stats">
            <div class="profile-stat">
              <span class="profile-stat-label">INT</span>
              <div class="profile-stat-bar"><div class="profile-stat-fill" data-value="5"></div></div>
            </div>
            <div class="profile-stat">
              <span class="profile-stat-label">STR</span>
              <div class="profile-stat-bar"><div class="profile-stat-fill" data-value="3"></div></div>
            </div>
            <div class="profile-stat">
              <span class="profile-stat-label">SPD</span>
              <div class="profile-stat-bar"><div class="profile-stat-fill" data-value="4"></div></div>
            </div>
            <div class="profile-stat">
              <span class="profile-stat-label">DUR</span>
              <div class="profile-stat-bar"><div class="profile-stat-fill" data-value="6"></div></div>
            </div>
            <div class="profile-stat">
              <span class="profile-stat-label">PWR</span>
              <div class="profile-stat-bar"><div class="profile-stat-fill" data-value="2"></div></div>
            </div>
            <div class="profile-stat">
              <span class="profile-stat-label">CMB</span>
              <div class="profile-stat-bar"><div class="profile-stat-fill" data-value="7"></div></div>
            </div>
          </div>
        </div>
        </html>
        """
        profile = parse_profile_page(html, "42")
        assert profile.fields["power grid - int"] == "5"
        assert profile.fields["power grid - str"] == "3"
        assert profile.fields["power grid - spd"] == "4"
        assert profile.fields["power grid - dur"] == "6"
        assert profile.fields["power grid - pwr"] == "2"
        assert profile.fields["power grid - cmb"] == "7"

    def test_skips_empty_power_grid_values(self):
        html = """
        <html>
        <h1 class="profile-name">Test</h1>
        <div class="profile-stat">
          <span class="profile-stat-label">INT</span>
          <div class="profile-stat-bar"><div class="profile-stat-fill" data-value="5"></div></div>
        </div>
        <div class="profile-stat">
          <span class="profile-stat-label">STR</span>
          <div class="profile-stat-bar"><div class="profile-stat-fill" data-value=""></div></div>
        </div>
        <div class="profile-stat">
          <span class="profile-stat-label">SPD</span>
          <div class="profile-stat-bar"><div class="profile-stat-fill" data-value="No Information"></div></div>
        </div>
        </html>
        """
        profile = parse_profile_page(html, "1")
        assert profile.fields["power grid - int"] == "5"
        assert "power grid - str" not in profile.fields
        assert "power grid - spd" not in profile.fields

    def test_no_power_grid_on_page(self):
        html = """
        <html>
        <h1 class="profile-name">Test</h1>
        <div class="pf-k"><span class="pf-l">age</span>25</div>
        </html>
        """
        profile = parse_profile_page(html, "1")
        assert "power grid - int" not in profile.fields


class TestParseProfileHeroImages:
    """Test hero image extraction from background-image styles."""

    def test_extracts_all_hero_images(self):
        html = """
        <html>
        <h1 class="profile-name">Test</h1>
        <div class="hero-portrait" style="background-image: url('https://img.com/portrait.jpg');"></div>
        <div class="hero-sq-top" style="background-image: url('https://img.com/square.jpg');"></div>
        <div class="hero-sq-bot" style="background-image: url('https://img.com/secondary.jpg');"></div>
        <div class="hero-rect" style="background-image: url('https://img.com/rect.gif');"></div>
        </html>
        """
        profile = parse_profile_page(html, "42")
        assert profile.fields["portrait_image"] == "https://img.com/portrait.jpg"
        assert profile.fields["square_image"] == "https://img.com/square.jpg"
        assert profile.fields["secondary_square_image"] == "https://img.com/secondary.jpg"
        assert profile.fields["rectangle_gif"] == "https://img.com/rect.gif"

    def test_handles_missing_hero_images(self):
        html = """
        <html>
        <h1 class="profile-name">Test</h1>
        <div class="hero-sq-top" style="background-image: url('https://img.com/sq.jpg');"></div>
        </html>
        """
        profile = parse_profile_page(html, "42")
        assert profile.fields["square_image"] == "https://img.com/sq.jpg"
        assert "portrait_image" not in profile.fields
        assert "secondary_square_image" not in profile.fields
        assert "rectangle_gif" not in profile.fields

    def test_handles_unquoted_urls(self):
        html = """
        <html>
        <h1 class="profile-name">Test</h1>
        <div class="hero-portrait" style="background-image: url(https://img.com/portrait.jpg);"></div>
        </html>
        """
        profile = parse_profile_page(html, "42")
        assert profile.fields["portrait_image"] == "https://img.com/portrait.jpg"

    def test_real_twai_hero_structure(self):
        """Test with exact HTML structure from the TWAI theme: multi-class divs
        inside .profile-hero-images container."""
        html = """
        <html>
        <title>Viewing Profile -> Aaron Fischer</title>
        <div class="profile-app group-6">
          <header class="profile-hero">
            <div class="profile-hero-images">
              <div class="profile-hero-img hero-rect" style="background-image: url('https://i.imgur.com/rectgif.gif');"></div>
              <div class="profile-hero-img hero-sq-top" style="background-image: url('https://i.imgur.com/square.png');"></div>
              <div class="profile-hero-img hero-sq-bot" style="background-image: url('https://i.imgur.com/square2.png');"></div>
              <div class="profile-hero-img hero-portrait" style="background-image: url('https://i.imgur.com/portrait.png');"></div>
            </div>
            <div class="profile-hero-info">
              <h1 class="profile-name" data-text="Aaron Fischer">Aaron Fischer</h1>
              <h2 class="profile-codename">Captain America</h2>
            </div>
          </header>
          <aside class="profile-sidebar">
            <div class="profile-card glass profile-dossier-card">
              <dl class="profile-dossier">
                <dt>Face Claim</dt><dd>Some Actor</dd>
                <dt>Species</dt><dd>human</dd>
              </dl>
            </div>
          </aside>
          <div class="profile-short-quote">No more running.</div>
        </div>
        </html>
        """
        profile = parse_profile_page(html, "91")
        assert profile.name == "Aaron Fischer"
        assert profile.fields["portrait_image"] == "https://i.imgur.com/portrait.png"
        assert profile.fields["square_image"] == "https://i.imgur.com/square.png"
        assert profile.fields["secondary_square_image"] == "https://i.imgur.com/square2.png"
        assert profile.fields["rectangle_gif"] == "https://i.imgur.com/rectgif.gif"
        assert profile.fields["short_quote"] == "No more running."
        assert profile.fields["face claim"] == "Some Actor"
        assert profile.fields["codename"] == "Captain America"

    def test_empty_background_image_url_skipped(self):
        """Empty url('') should NOT produce a field entry."""
        html = """
        <html>
        <h1 class="profile-name">Test</h1>
        <div class="hero-portrait" style="background-image: url('');"></div>
        <div class="hero-rect" style="background-image: url('https://img.com/rect.gif');"></div>
        </html>
        """
        profile = parse_profile_page(html, "42")
        assert "portrait_image" not in profile.fields
        assert profile.fields["rectangle_gif"] == "https://img.com/rect.gif"


    def test_extracts_images_from_pf_static_skin(self):
        """The static (pf-*) skin uses pf-c for square, pf-p for secondary square,
        pf-w for rectangle, and #mp-e (inside .mp-c) for portrait."""
        html = """
        <html>
        <div class="pf-e">Jessica Jones</div>
        <div class="pf-b"><div class="pf-c" style="background: url(https://img.com/square.gif), url(https://fallback.com/img.jpg);"></div></div>
        <div class="pf-n"><div class="pf-o"><div class="pf-p" style="background: url(https://img.com/secondary.jpg), url(https://fallback.com/img.jpg);"></div></div></div>
        <div class="pf-v"><div class="pf-w" style="background: url(https://img.com/rect.gif);"></div></div>
        <div class="mp-c"><div class="mp-d"><div id="mp-e" style="background: url(https://img.com/portrait.jpg);"></div></div></div>
        </html>
        """
        profile = parse_profile_page(html, "3")
        assert profile.fields["square_image"] == "https://img.com/square.gif"
        assert profile.fields["secondary_square_image"] == "https://img.com/secondary.jpg"
        assert profile.fields["portrait_image"] == "https://img.com/portrait.jpg"
        assert profile.fields["rectangle_gif"] == "https://img.com/rect.gif"

    def test_hero_selectors_take_priority_over_pf(self):
        """If both hero-* and pf-* exist, hero-* should win (it appears first in selector list)."""
        html = """
        <html>
        <h1 class="profile-name">Test</h1>
        <div class="hero-portrait" style="background-image: url('https://img.com/hero-portrait.jpg');"></div>
        <div class="mp-c"><div class="mp-d"><div id="mp-e" style="background: url(https://img.com/mp-portrait.jpg);"></div></div></div>
        <div class="hero-sq-bot" style="background-image: url('https://img.com/hero-secondary.jpg');"></div>
        <div class="pf-p" style="background: url(https://img.com/pf-secondary.jpg);"></div>
        </html>
        """
        profile = parse_profile_page(html, "42")
        assert profile.fields["portrait_image"] == "https://img.com/hero-portrait.jpg"
        assert profile.fields["secondary_square_image"] == "https://img.com/hero-secondary.jpg"


class TestParseProfileOOCFields:
    """Test extraction of OOC alias, short quote, and connections."""

    def test_extracts_alias_from_ooc_footer(self):
        html = """
        <html>
        <h1 class="profile-name">Test</h1>
        <div class="profile-ooc-footer">Kim</div>
        </html>
        """
        profile = parse_profile_page(html, "42")
        assert profile.fields["alias"] == "Kim"

    def test_alias_from_pf_ab_takes_priority(self):
        """alias from div.pf-ab should not be overwritten by .profile-ooc-footer."""
        html = """
        <html>
        <h1 class="profile-name">Test</h1>
        <div class="pf-ab" title="alias"><span class="pf-ac">icon</span>Kim</div>
        <div class="profile-ooc-footer">AlternateAlias</div>
        </html>
        """
        profile = parse_profile_page(html, "42")
        assert profile.fields["alias"] == "Kim"

    def test_extracts_short_quote(self):
        html = """
        <html>
        <h1 class="profile-name">Test</h1>
        <div class="profile-short-quote">No more running.</div>
        </html>
        """
        profile = parse_profile_page(html, "42")
        assert profile.fields["short_quote"] == "No more running."

    def test_skips_no_information_short_quote(self):
        html = """
        <html>
        <h1 class="profile-name">Test</h1>
        <div class="profile-short-quote">No Information</div>
        </html>
        """
        profile = parse_profile_page(html, "42")
        assert "short_quote" not in profile.fields

    def test_extracts_connections(self):
        html = """
        <html>
        <h1 class="profile-name">Test</h1>
        <div class="profile-connections">Pietro (twin), Vision (partner)</div>
        </html>
        """
        profile = parse_profile_page(html, "42")
        assert profile.fields["connections"] == "Pietro (twin), Vision (partner)"

    def test_skips_no_information_connections(self):
        html = """
        <html>
        <h1 class="profile-name">Test</h1>
        <div class="profile-connections">No Information</div>
        </html>
        """
        profile = parse_profile_page(html, "42")
        assert "connections" not in profile.fields


class TestParseApplicationUrl:
    def test_extracts_application_link(self):
        html = """
        <div class="pf-ad">
          <a href="https://therewasanidea.jcink.net/index.php?showtopic=22" title="view application">
            <div class="pf-ae"><i class="las la-id-card"></i></div>
          </a>
        </div>
        """
        assert parse_application_url(html) == "https://therewasanidea.jcink.net/index.php?showtopic=22"

    def test_returns_none_for_no_information(self):
        html = """
        <a href="<i>No Information</i>" title="view application">link</a>
        """
        assert parse_application_url(html) is None

    def test_returns_none_when_missing(self):
        html = "<html><body>No links</body></html>"
        assert parse_application_url(html) is None

    def test_relative_url_gets_base(self):
        html = """
        <a href="/index.php?showtopic=100" title="view application">link</a>
        """
        result = parse_application_url(html)
        assert result.startswith("https://")
        assert "showtopic=100" in result


class TestParsePowerGrid:
    def test_extracts_stats_and_converts_to_scale(self):
        """Percentages should be converted to 1-7 integer scale."""
        html = """
        <div class="sa-n">
          <div class="sa-o">intelligence</div>
          <div class="sa-p"><div class="sa-q" style="width: 42.86%;"></div></div>
        </div>
        <div class="sa-n">
          <div class="sa-o">strength</div>
          <div class="sa-p"><div class="sa-q" style="width: 85.71%;"></div></div>
        </div>
        <div class="sa-n">
          <div class="sa-o">speed</div>
          <div class="sa-p"><div class="sa-q" style="width: 28.57%;"></div></div>
        </div>
        <div class="sa-n">
          <div class="sa-o">durability</div>
          <div class="sa-p"><div class="sa-q" style="width: 100%;"></div></div>
        </div>
        <div class="sa-n">
          <div class="sa-o">energy projection</div>
          <div class="sa-p"><div class="sa-q" style="width: 14.28%;"></div></div>
        </div>
        <div class="sa-n">
          <div class="sa-o">fighting skills</div>
          <div class="sa-p"><div class="sa-q" style="width: 71.43%;"></div></div>
        </div>
        """
        fields = parse_power_grid(html)
        assert fields["power grid - int"] == "3"
        assert fields["power grid - str"] == "6"
        assert fields["power grid - spd"] == "2"
        assert fields["power grid - dur"] == "7"
        assert fields["power grid - pwr"] == "1"
        assert fields["power grid - cmb"] == "5"

    def test_skips_zero_percent(self):
        html = """
        <div class="sa-n">
          <div class="sa-o">intelligence</div>
          <div class="sa-p"><div class="sa-q" style="width: 0%;"></div></div>
        </div>
        """
        fields = parse_power_grid(html)
        assert "power grid - int" not in fields

    def test_empty_page(self):
        html = "<html><body>No power grid</body></html>"
        fields = parse_power_grid(html)
        assert fields == {}

    def test_ignores_unknown_stat_names(self):
        html = """
        <div class="sa-n">
          <div class="sa-o">charisma</div>
          <div class="sa-p"><div class="sa-q" style="width: 50%;"></div></div>
        </div>
        """
        fields = parse_power_grid(html)
        assert fields == {}


