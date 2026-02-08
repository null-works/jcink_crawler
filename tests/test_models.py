"""Tests for app/models/character.py — Pydantic models."""
import pytest
from datetime import datetime

from app.models.character import (
    ThreadCategory,
    CrawlStatus,
    CharacterSummary,
    ThreadInfo,
    CharacterThreads,
    Quote,
    CharacterProfile,
    CrawlStatusResponse,
    CharacterRegister,
    CrawlTrigger,
)


class TestThreadCategory:
    def test_enum_values(self):
        assert ThreadCategory.ONGOING == "ongoing"
        assert ThreadCategory.COMMS == "comms"
        assert ThreadCategory.COMPLETE == "complete"
        assert ThreadCategory.INCOMPLETE == "incomplete"

    def test_is_string_enum(self):
        assert isinstance(ThreadCategory.ONGOING, str)
        assert ThreadCategory.ONGOING == "ongoing"


class TestCrawlStatus:
    def test_enum_values(self):
        assert CrawlStatus.PENDING == "pending"
        assert CrawlStatus.CRAWLING == "crawling"
        assert CrawlStatus.COMPLETE == "complete"
        assert CrawlStatus.ERROR == "error"


class TestCharacterSummary:
    def test_required_fields(self):
        cs = CharacterSummary(id="42", name="Tony", profile_url="https://example.com/42")
        assert cs.id == "42"
        assert cs.name == "Tony"
        assert cs.profile_url == "https://example.com/42"

    def test_optional_fields_default(self):
        cs = CharacterSummary(id="1", name="X", profile_url="http://x")
        assert cs.group_name is None
        assert cs.avatar_url is None
        assert cs.thread_counts == {}
        assert cs.last_profile_crawl is None
        assert cs.last_thread_crawl is None

    def test_with_all_fields(self):
        now = datetime.now()
        cs = CharacterSummary(
            id="42", name="Tony", profile_url="http://x",
            group_name="Avengers", avatar_url="http://img.com/x.jpg",
            thread_counts={"ongoing": 5, "total": 5},
            last_profile_crawl=now, last_thread_crawl=now,
        )
        assert cs.group_name == "Avengers"
        assert cs.thread_counts["total"] == 5

    def test_serialization(self):
        cs = CharacterSummary(id="42", name="Tony", profile_url="http://x")
        data = cs.model_dump()
        assert data["id"] == "42"
        assert "name" in data


class TestThreadInfo:
    def test_defaults(self):
        ti = ThreadInfo(id="1", title="Thread", url="http://x")
        assert ti.category == ThreadCategory.ONGOING
        assert ti.is_user_last_poster is False
        assert ti.last_poster_id is None

    def test_with_category(self):
        ti = ThreadInfo(id="1", title="T", url="http://x", category="complete")
        assert ti.category == ThreadCategory.COMPLETE


class TestCharacterThreads:
    def test_empty_defaults(self):
        ct = CharacterThreads(character_id="42", character_name="Tony")
        assert ct.ongoing == []
        assert ct.comms == []
        assert ct.complete == []
        assert ct.incomplete == []
        assert ct.counts == {}

    def test_with_threads(self):
        t = ThreadInfo(id="1", title="T", url="http://x")
        ct = CharacterThreads(
            character_id="42", character_name="Tony",
            ongoing=[t], counts={"ongoing": 1, "total": 1},
        )
        assert len(ct.ongoing) == 1
        assert ct.counts["total"] == 1


class TestQuote:
    def test_minimal(self):
        q = Quote(character_id="42", quote_text="I am Iron Man")
        assert q.id is None
        assert q.source_thread_id is None
        assert q.created_at is None

    def test_full(self):
        q = Quote(
            id=1, character_id="42", quote_text="test",
            source_thread_id="100", source_thread_title="Thread",
            created_at=datetime.now(),
        )
        assert q.id == 1


class TestCharacterProfile:
    def test_structure(self):
        cs = CharacterSummary(id="42", name="Tony", profile_url="http://x")
        cp = CharacterProfile(character=cs)
        assert cp.fields == {}
        assert cp.threads is None


class TestCrawlStatusResponse:
    def test_defaults(self):
        r = CrawlStatusResponse()
        assert r.characters_tracked == 0
        assert r.total_threads == 0
        assert r.total_quotes == 0
        assert r.last_thread_crawl is None
        assert r.last_profile_crawl is None


class TestCharacterRegister:
    def test_requires_user_id(self):
        r = CharacterRegister(user_id="42")
        assert r.user_id == "42"


class TestCrawlTrigger:
    def test_defaults(self):
        ct = CrawlTrigger(character_id="42")
        assert ct.crawl_type == "threads"

    def test_custom_crawl_type(self):
        ct = CrawlTrigger(character_id="42", crawl_type="profile")
        assert ct.crawl_type == "profile"
