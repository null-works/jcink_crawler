"""Tests for app/config.py — Settings and configuration."""
import os
import pytest


class TestSettings:
    def test_default_values(self):
        """Settings should have sensible defaults (database_path may be overridden by test env)."""
        from app.config import Settings

        s = Settings()
        assert s.forum_base_url == "https://therewasanidea.jcink.net"
        assert s.forum_complete_id == "49"
        assert s.forum_incomplete_id == "59"
        assert s.forum_comms_id == "31"
        assert s.crawl_threads_interval_minutes == 60
        assert s.crawl_profiles_interval_minutes == 1440
        assert s.crawl_quotes_batch_size == 0  # 0 = unlimited
        assert s.quote_min_words == 3
        assert s.request_delay_seconds == 2.0
        # database_path may be overridden by DATABASE_PATH env var from conftest
        assert isinstance(s.database_path, str)
        assert s.database_path.endswith(".db")

    def test_excluded_forum_ids_property(self):
        """excluded_forum_ids should return a set parsed from comma-separated string."""
        from app.config import Settings

        s = Settings(forums_excluded="1,2,3")
        result = s.excluded_forum_ids
        assert isinstance(result, set)
        assert result == {"1", "2", "3"}

    def test_excluded_forum_ids_full_default(self):
        """Default exclusion list should contain all expected IDs."""
        from app.config import Settings

        s = Settings()
        excluded = s.excluded_forum_ids
        # Spot-check a few
        assert "4" in excluded
        assert "95" in excluded
        assert "52" in excluded
        # Should not contain forum IDs used for categorization
        assert "49" not in excluded
        assert "59" not in excluded
        assert "31" not in excluded

    def test_settings_reads_env_vars(self):
        """Settings should pick up environment variables."""
        from app.config import Settings

        os.environ["QUOTE_MIN_WORDS"] = "10"
        try:
            s = Settings()
            assert s.quote_min_words == 10
        finally:
            del os.environ["QUOTE_MIN_WORDS"]

    def test_singleton_settings_importable(self):
        """The module-level `settings` singleton should be importable."""
        from app.config import settings

        assert settings is not None
        assert hasattr(settings, "forum_base_url")
