"""Tests for cli.py — CLI client and helper functions."""
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from cli import cli, CrawlerClient, _format_time


class TestFormatTime:
    def test_none_returns_never(self):
        assert _format_time(None) == "Never"

    def test_empty_string_returns_never(self):
        assert _format_time("") == "Never"

    def test_recent_timestamp(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        result = _format_time(now)
        assert result == "Just now"

    def test_minutes_ago(self):
        from datetime import datetime, timezone, timedelta
        ts = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        result = _format_time(ts)
        assert "m ago" in result

    def test_hours_ago(self):
        from datetime import datetime, timezone, timedelta
        ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        result = _format_time(ts)
        assert "h ago" in result

    def test_days_ago(self):
        from datetime import datetime, timezone, timedelta
        ts = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        result = _format_time(ts)
        assert "d ago" in result

    def test_malformed_timestamp_returns_truncated(self):
        result = _format_time("not-a-real-timestamp-value-here")
        assert len(result) <= 19

    def test_z_suffix_handled(self):
        """Timestamps ending in 'Z' should be parsed correctly."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = _format_time(now)
        # Should parse without error and return a relative time
        assert result in ("Just now",) or "ago" in result


class TestCrawlerClient:
    def test_url_building(self):
        client = CrawlerClient("http://localhost:8943/")
        assert client._url("/health") == "http://localhost:8943/health"

    def test_url_no_trailing_slash(self):
        client = CrawlerClient("http://localhost:8943")
        assert client._url("/api/status") == "http://localhost:8943/api/status"


class TestCliCommands:
    """Test CLI commands using Click's test runner."""

    def test_status_service_unavailable(self):
        runner = CliRunner()
        with patch.object(CrawlerClient, "health", return_value=False), \
             patch.object(CrawlerClient, "status", return_value=None):
            result = runner.invoke(cli, ["status"])
            assert "unavailable" in result.output.lower() or result.exit_code != 0

    def test_status_shows_online(self):
        runner = CliRunner()
        mock_data = {
            "characters_tracked": 5,
            "total_threads": 100,
            "total_quotes": 50,
            "last_thread_crawl": "2024-01-01T00:00:00",
            "last_profile_crawl": "2024-01-01T00:00:00",
        }
        with patch.object(CrawlerClient, "health", return_value=True), \
             patch.object(CrawlerClient, "status", return_value=mock_data):
            result = runner.invoke(cli, ["status"])
            assert result.exit_code == 0
            assert "Online" in result.output

    def test_characters_empty(self):
        runner = CliRunner()
        with patch.object(CrawlerClient, "characters", return_value=None):
            result = runner.invoke(cli, ["characters"])
            assert result.exit_code == 0
            assert "No characters" in result.output

    def test_characters_list(self):
        runner = CliRunner()
        mock_chars = [
            {
                "id": "42",
                "name": "Tony Stark",
                "group_name": "Avengers",
                "thread_counts": {"ongoing": 5, "comms": 2, "complete": 3, "incomplete": 1, "total": 11},
                "last_thread_crawl": None,
            }
        ]
        with patch.object(CrawlerClient, "characters", return_value=mock_chars):
            result = runner.invoke(cli, ["characters"])
            assert result.exit_code == 0
            # Rich tables may wrap "Tony Stark" across lines
            assert "Tony" in result.output
            assert "42" in result.output

    def test_character_not_found(self):
        runner = CliRunner()
        with patch.object(CrawlerClient, "character", return_value=None):
            result = runner.invoke(cli, ["character", "999"])
            assert "not found" in result.output.lower()

    def test_character_detail(self):
        runner = CliRunner()
        mock_data = {
            "character": {
                "id": "42",
                "name": "Tony Stark",
                "group_name": "Avengers",
                "profile_url": "https://example.com/42",
            },
            "fields": {"pf-alias": "Iron Man"},
            "threads": {
                "counts": {"ongoing": 2, "total": 2},
            },
        }
        with patch.object(CrawlerClient, "character", return_value=mock_data), \
             patch.object(CrawlerClient, "quote_count", return_value={"count": 10}):
            result = runner.invoke(cli, ["character", "42"])
            assert result.exit_code == 0
            assert "Tony Stark" in result.output

    def test_threads_not_found(self):
        runner = CliRunner()
        with patch.object(CrawlerClient, "threads", return_value=None):
            result = runner.invoke(cli, ["threads", "999"])
            assert "not found" in result.output.lower()

    def test_threads_shows_data(self):
        runner = CliRunner()
        mock_data = {
            "character_name": "Tony Stark",
            "ongoing": [
                {"title": "Avengers Assemble", "forum_name": "RP", "last_poster_name": "Steve", "is_user_last_poster": False}
            ],
            "comms": [],
            "complete": [],
            "incomplete": [],
        }
        with patch.object(CrawlerClient, "threads", return_value=mock_data):
            result = runner.invoke(cli, ["threads", "42"])
            assert result.exit_code == 0
            assert "Tony Stark" in result.output

    def test_quotes_empty(self):
        runner = CliRunner()
        with patch.object(CrawlerClient, "quotes", return_value=None):
            result = runner.invoke(cli, ["quotes", "42"])
            assert "No quotes" in result.output

    def test_quotes_random(self):
        runner = CliRunner()
        mock_quote = {
            "quote_text": "I am Iron Man",
            "source_thread_title": "Battle Thread",
        }
        with patch.object(CrawlerClient, "random_quote", return_value=mock_quote):
            result = runner.invoke(cli, ["quotes", "42", "--random"])
            assert result.exit_code == 0
            assert "I am Iron Man" in result.output

    def test_quotes_random_empty(self):
        runner = CliRunner()
        with patch.object(CrawlerClient, "random_quote", return_value=None):
            result = runner.invoke(cli, ["quotes", "42", "--random"])
            assert "No quotes" in result.output

    def test_quotes_list(self):
        runner = CliRunner()
        mock_quotes = [
            {"quote_text": "First quote text here", "source_thread_title": "Thread 1"},
            {"quote_text": "Second quote text here", "source_thread_title": "Thread 2"},
        ]
        with patch.object(CrawlerClient, "quotes", return_value=mock_quotes):
            result = runner.invoke(cli, ["quotes", "42"])
            assert result.exit_code == 0
            assert "2 total" in result.output

    def test_register_success(self):
        runner = CliRunner()
        with patch.object(CrawlerClient, "register", return_value={"status": "registering"}):
            result = runner.invoke(cli, ["register", "42"])
            assert result.exit_code == 0
            assert "Registration started" in result.output

    def test_register_already_exists(self):
        runner = CliRunner()
        with patch.object(CrawlerClient, "register", return_value={
            "status": "already_registered",
            "character": {"name": "Tony Stark"},
        }):
            result = runner.invoke(cli, ["register", "42"])
            assert "Already registered" in result.output

    def test_register_failed(self):
        runner = CliRunner()
        with patch.object(CrawlerClient, "register", return_value=None):
            result = runner.invoke(cli, ["register", "42"])
            assert "failed" in result.output.lower()

    def test_crawl_success(self):
        runner = CliRunner()
        with patch.object(CrawlerClient, "trigger_crawl", return_value={"status": "crawl_queued"}):
            result = runner.invoke(cli, ["crawl", "42", "--type", "threads"])
            assert result.exit_code == 0
            assert "Crawl queued" in result.output

    def test_crawl_failed(self):
        runner = CliRunner()
        with patch.object(CrawlerClient, "trigger_crawl", return_value=None):
            result = runner.invoke(cli, ["crawl", "42"])
            assert "failed" in result.output.lower()

    def test_custom_url_option(self):
        runner = CliRunner()
        with patch.object(CrawlerClient, "health", return_value=False), \
             patch.object(CrawlerClient, "status", return_value=None):
            result = runner.invoke(cli, ["--url", "http://custom:9999", "status"])
            # Should still attempt to connect (and fail gracefully)
            assert result.exit_code == 0 or "unavailable" in result.output.lower()
