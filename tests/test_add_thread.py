"""Tests for the Add Thread topic-id parsing (app/routes/dashboard.py).

The crawl itself is covered by test_crawler.py; here we just lock down the
id/URL extraction that feeds it.
"""
from app.routes.dashboard import _extract_topic_id


class TestExtractTopicId:
    def test_bare_numeric_id(self):
        assert _extract_topic_id("123") == "123"
        assert _extract_topic_id("  456  ") == "456"

    def test_showtopic_url(self):
        assert _extract_topic_id(
            "https://therewasanidea.jcink.net/index.php?showtopic=789"
        ) == "789"

    def test_showtopic_url_with_extra_params(self):
        assert _extract_topic_id(
            "https://therewasanidea.jcink.net/index.php?showtopic=789&view=getnewpost"
        ) == "789"

    def test_act_st_t_url(self):
        assert _extract_topic_id(
            "https://therewasanidea.jcink.net/index.php?act=ST&f=4&t=321"
        ) == "321"

    def test_garbage_returns_none(self):
        assert _extract_topic_id("not a thread") is None
        assert _extract_topic_id("") is None
        assert _extract_topic_id("https://therewasanidea.jcink.net/index.php?showforum=4") is None

    def test_does_not_match_showforum(self):
        # showforum has digits but must not be read as a topic id
        assert _extract_topic_id("index.php?showforum=12") is None
