"""Tests for ACP client SQL parsing and record extraction."""

import pytest
from app.services.acp_client import (
    parse_sql_dump,
    extract_post_records,
    extract_topic_records,
    extract_member_records,
    extract_forum_records,
    _parse_sql_values,
    _unix_to_iso,
    DEFAULT_TABLE_PARTS,
    ACP_PART_TOPICS,
    ACP_PART_POSTS,
    ACP_PART_FORUMS,
    ACP_PART_MEMBERS,
)


# ---------------------------------------------------------------------------
# _parse_sql_values
# ---------------------------------------------------------------------------

class TestParseSqlValues:
    def test_simple_numeric_values(self):
        result = _parse_sql_values("1, 2, 3")
        assert result == [1, 2, 3]

    def test_mixed_types(self):
        result = _parse_sql_values('1, "hello", 3')
        assert result == [1, "hello", 3]

    def test_null_values(self):
        result = _parse_sql_values("1, NULL, 3")
        assert result == [1, None, 3]

    def test_escaped_quotes(self):
        # Fallback parser handles single quotes
        result = _parse_sql_values("1, 'it\\'s a test', 3")
        assert len(result) == 3
        assert result[0] == 1
        assert result[2] == 3


# ---------------------------------------------------------------------------
# _unix_to_iso
# ---------------------------------------------------------------------------

class TestUnixToIso:
    def test_valid_timestamp(self):
        assert _unix_to_iso(1700000000) == "2023-11-14"

    def test_zero_returns_none(self):
        assert _unix_to_iso(0) is None

    def test_none_returns_none(self):
        assert _unix_to_iso(None) is None

    def test_string_timestamp(self):
        assert _unix_to_iso("1700000000") == "2023-11-14"


# ---------------------------------------------------------------------------
# parse_sql_dump
# ---------------------------------------------------------------------------

SAMPLE_SQL = """\
CREATE TABLE `ibf_posts` (
  `pid` int(10) NOT NULL,
  `author_id` int(10) NOT NULL
);
REPLACE INTO `ibf_posts` VALUES (1, 0, 0, 42, "Tony Stark", 0, 0, 0, 1700000000, 0, "Hello world", 0, 100, 5, 0);
REPLACE INTO `ibf_posts` VALUES (2, 0, 0, 55, "Steve Rogers", 0, 0, 0, 1700100000, 0, "Hey Tony", 0, 100, 5, 0);
REPLACE INTO `ibf_topics` VALUES (100, "Avengers Assemble", "", "open", 0, 0, 0, 42, 1700100000, 0, 0, "Tony Stark", 0, 0, 0, 5);
REPLACE INTO `ibf_forums` VALUES (5, 0, 0, 0, 0, 0, "IC Roleplay", 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0);
REPLACE INTO `ibf_members` VALUES (42, "Tony Stark", 3, 0, 0, 0, 0, "", 0, 150, 0, 0, 0, 0, 0, 0, 0, "", 0, 0, 0, 0);
"""


class TestParseSqlDump:
    def test_parses_posts(self):
        raw = parse_sql_dump(SAMPLE_SQL)
        assert "posts" in raw
        assert len(raw["posts"]) == 2

    def test_parses_topics(self):
        raw = parse_sql_dump(SAMPLE_SQL)
        assert "topics" in raw
        assert len(raw["topics"]) == 1

    def test_parses_forums(self):
        raw = parse_sql_dump(SAMPLE_SQL)
        assert "forums" in raw
        assert len(raw["forums"]) == 1

    def test_parses_members(self):
        raw = parse_sql_dump(SAMPLE_SQL)
        assert "members" in raw
        assert len(raw["members"]) == 1

    def test_ignores_create_table(self):
        raw = parse_sql_dump(SAMPLE_SQL)
        # CREATE TABLE lines should not create entries
        assert all(len(rows) > 0 for rows in raw.values())

    def test_empty_input(self):
        raw = parse_sql_dump("")
        assert raw == {}


# ---------------------------------------------------------------------------
# extract_post_records
# ---------------------------------------------------------------------------

class TestExtractPostRecords:
    def test_extracts_post_fields(self):
        raw = parse_sql_dump(SAMPLE_SQL)
        posts = extract_post_records(raw)
        assert len(posts) == 2

        post = posts[0]
        assert post["character_id"] == "42"
        assert post["author_name"] == "Tony Stark"
        assert post["thread_id"] == "100"
        assert post["forum_id"] == "5"
        assert post["post_date"] == "2023-11-14"

    def test_includes_body_when_requested(self):
        raw = parse_sql_dump(SAMPLE_SQL)
        posts = extract_post_records(raw, include_body=True)
        assert posts[0]["post_body"] == "Hello world"

    def test_no_body_by_default(self):
        raw = parse_sql_dump(SAMPLE_SQL)
        posts = extract_post_records(raw, include_body=False)
        assert "post_body" not in posts[0]


# ---------------------------------------------------------------------------
# extract_topic_records
# ---------------------------------------------------------------------------

class TestExtractTopicRecords:
    def test_extracts_topic_fields(self):
        raw = parse_sql_dump(SAMPLE_SQL)
        topics = extract_topic_records(raw)
        assert len(topics) == 1

        topic = topics[0]
        assert topic["thread_id"] == "100"
        assert topic["title"] == "Avengers Assemble"
        assert topic["forum_id"] == "5"
        assert topic["state"] == "open"
        assert topic["last_poster_id"] == "42"
        assert topic["last_poster_name"] == "Tony Stark"
        assert topic["last_post_date"] == "2023-11-16"


# ---------------------------------------------------------------------------
# extract_forum_records
# ---------------------------------------------------------------------------

class TestExtractForumRecords:
    def test_extracts_forum_fields(self):
        raw = parse_sql_dump(SAMPLE_SQL)
        forums = extract_forum_records(raw)
        assert len(forums) == 1

        forum = forums[0]
        assert forum["forum_id"] == "5"
        assert forum["name"] == "IC Roleplay"

    def test_empty_forums(self):
        raw = parse_sql_dump("")
        forums = extract_forum_records(raw)
        assert forums == []


# ---------------------------------------------------------------------------
# extract_member_records
# ---------------------------------------------------------------------------

class TestExtractMemberRecords:
    def test_extracts_member_fields(self):
        raw = parse_sql_dump(SAMPLE_SQL)
        members = extract_member_records(raw)
        assert len(members) == 1

        member = members[0]
        assert member["member_id"] == "42"
        assert member["name"] == "Tony Stark"
        assert member["post_count"] == 150


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestTablePartConstants:
    def test_default_parts_include_core_tables(self):
        assert ACP_PART_TOPICS in DEFAULT_TABLE_PARTS
        assert ACP_PART_POSTS in DEFAULT_TABLE_PARTS
        assert ACP_PART_FORUMS in DEFAULT_TABLE_PARTS
        assert ACP_PART_MEMBERS in DEFAULT_TABLE_PARTS

    def test_default_parts_has_4_entries(self):
        assert len(DEFAULT_TABLE_PARTS) == 4
