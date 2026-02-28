"""JCink Admin Control Panel client for database dumps.

Replicates the login + MySQL dump flow from databaseParser.js:
1. Login to ACP → get adsess token
2. Clear previous backup
3. Follow full dump pagination through all tables
4. Fetch generated SQL file
5. Parse REPLACE INTO statements into structured data

This gives us accurate post dates (Unix timestamps from ibf_posts)
instead of scraping dates from HTML which is fragile.

Column indices are AUTO-DETECTED at parse time by cross-referencing
data between tables (e.g. finding which column in topics contains
known forum IDs).  This handles JCink schema variations that differ
from standard IPB 1.3.x.
"""

import asyncio
import json
import re
from datetime import datetime, timezone
from urllib.parse import quote as url_quote

import httpx

from app.config import settings
from app.services.activity import log_debug


# ── Default column indices (IPB 1.3.x baseline) ──
# These are used as starting guesses; auto-detection overrides them
# when cross-referencing between tables succeeds.

# ibf_forums — column 0 is always the ID
_FORUM_COL_ID = 0

# ibf_topics — column 0 is always the topic ID
_TOPIC_COL_ID = 0

# ibf_members — column 0 is always the member ID
_MEMBER_COL_ID = 0

# JCink ACP table part numbers — these are the internal IDs JCink uses
# in its MySQL dump pagination. databaseParser.js uses the same numbers.
# Only dump the tables we actually need instead of the entire database.
ACP_PART_MEMBERS = "21"
ACP_PART_TOPICS = "23"
ACP_PART_POSTS = "32"
ACP_PART_FORUMS = "36"

# Default set: topics + posts + forums + members (for post counting & thread tracking)
DEFAULT_TABLE_PARTS = [ACP_PART_TOPICS, ACP_PART_POSTS, ACP_PART_FORUMS, ACP_PART_MEMBERS]

# Regex to find "next page" link in ACP dump pagination.
# Uses lookaheads so parameter order doesn't matter — JCink may put
# adsess first, last, or anywhere in the query string.
_NEXT_LINK_RE = re.compile(
    r"admin\.php\?"
    r"(?=[^'\"]*\bact=mysql\b)"
    r"(?=[^'\"]*\bcode=dump\b)"
    r"(?=[^'\"]*\bline=(\d+))"
    r"(?=[^'\"]*\bpart=(\d+))"
    r"(?=[^'\"]*\badsess=([a-f0-9]+))",
    re.IGNORECASE,
)

# Regex to parse REPLACE INTO statements
_REPLACE_RE = re.compile(
    r"^REPLACE INTO `\w+?_(\w+)` VALUES\s*\((.+)\);?\s*$"
)


def _parse_sql_values(values_str: str) -> list | None:
    """Parse the VALUES portion of a REPLACE INTO statement.

    Tries JSON parsing first (fast path), falls back to manual
    parsing for edge cases with escaped quotes.
    """
    # Try JSON array parse: wrap in brackets
    json_str = f"[{values_str}]"
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: manual CSV-style parse handling quoted strings
    result = []
    current = ""
    in_quote = False
    escape_next = False

    for char in values_str:
        if escape_next:
            current += char
            escape_next = False
            continue
        if char == "\\":
            escape_next = True
            current += char
            continue
        if char == "'" and not in_quote:
            in_quote = True
            continue
        if char == "'" and in_quote:
            in_quote = False
            continue
        if char == "," and not in_quote:
            val = current.strip()
            if val == "NULL":
                result.append(None)
            else:
                try:
                    result.append(int(val))
                except ValueError:
                    try:
                        result.append(float(val))
                    except ValueError:
                        result.append(val)
            current = ""
            continue
        current += char

    # Last value
    val = current.strip()
    if val == "NULL":
        result.append(None)
    else:
        try:
            result.append(int(val))
        except ValueError:
            try:
                result.append(float(val))
            except ValueError:
                result.append(val)

    return result


def _unix_to_iso(ts) -> str | None:
    """Convert Unix timestamp to ISO date string (YYYY-MM-DD)."""
    if ts is None:
        return None
    try:
        ts_int = int(ts)
        if ts_int <= 0:
            return None
        return datetime.fromtimestamp(ts_int, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        return None


def parse_sql_dump(sql_text: str) -> dict[str, list[list]]:
    """Parse a JCink SQL dump file into structured data.

    Returns:
        Dict mapping table names to lists of row arrays.
        e.g. {"posts": [[col0, col1, ...], ...], "topics": [...]}
    """
    raw: dict[str, list[list]] = {}

    for line in sql_text.split("\n"):
        line = line.strip()
        if not line.startswith("REPLACE"):
            continue

        match = _REPLACE_RE.match(line)
        if not match:
            continue

        table_name = match.group(1)
        values_str = match.group(2)

        # Clean JCink encoding quirks
        values_str = values_str.replace("\\'", "'")
        values_str = values_str.replace("&amp;", "&")
        values_str = values_str.replace("&lt;", "<")
        values_str = values_str.replace("&gt;", ">")
        values_str = values_str.replace("&quot;", '"')

        row = _parse_sql_values(values_str)
        if row is not None:
            raw.setdefault(table_name, []).append(row)

    return raw


def _detect_column(rows: list[list], valid_values: set, start_col: int = 2,
                    require_int: bool = True,
                    exclude_cols: set[int] | None = None) -> int | None:
    """Auto-detect which column in rows contains values from valid_values.

    Checks a sample of rows and returns the column index with the best
    combination of match rate and value diversity.  This prevents false
    positives on boolean columns (e.g. state=1 matching forum_id=1).

    Args:
        exclude_cols: Column indices to skip (already assigned to other fields).

    Returns None if no column matches well enough (> 50% of sampled rows).
    """
    if not rows or not valid_values:
        return None

    sample = rows[:200]
    max_cols = min(len(r) for r in sample) if sample else 0
    best_col = None
    best_score = 0.0
    _exclude = exclude_cols or set()

    for col_idx in range(start_col, max_cols):
        if col_idx in _exclude:
            continue
        matches = 0
        distinct_matched: set[str] = set()
        for row in sample:
            val = row[col_idx]
            if val is None:
                continue
            if require_int:
                try:
                    val = int(val)
                except (ValueError, TypeError):
                    continue
            sval = str(val)
            if sval in valid_values:
                matches += 1
                distinct_matched.add(sval)
        rate = matches / len(sample) if sample else 0
        if rate < 0.5:
            continue
        # Score: match rate * diversity bonus (more distinct values = better)
        # A column with diverse values matching many different forum IDs is
        # much more likely to be the real foreign key than one stuck on "1".
        diversity = min(len(distinct_matched) / max(len(valid_values), 1), 1.0)
        score = rate * (0.5 + 0.5 * diversity)
        if score > best_score:
            best_score = score
            best_col = col_idx

    return best_col


def _detect_name_column(rows: list[list], start_col: int = 1) -> int | None:
    """Auto-detect which column contains human-readable names.

    Looks for a column where most values are non-empty strings of
    reasonable length (2-200 chars) that aren't purely numeric.
    """
    if not rows:
        return None

    sample = rows[:100]
    max_cols = min(len(r) for r in sample) if sample else 0
    best_col = None
    best_rate = 0.0

    for col_idx in range(start_col, max_cols):
        matches = 0
        for row in sample:
            val = row[col_idx]
            if isinstance(val, str) and 2 <= len(val) <= 200:
                # Must not be purely numeric or look like a timestamp
                try:
                    int(val)
                    continue  # skip purely numeric strings
                except ValueError:
                    pass
                matches += 1
        rate = matches / len(sample) if sample else 0
        if rate > best_rate:
            best_rate = rate
            best_col = col_idx

    return best_col if best_rate > 0.5 else None


def _detect_timestamp_column(rows: list[list], start_col: int = 2) -> int | None:
    """Auto-detect which column contains Unix timestamps.

    Looks for integer values in a plausible range (year 2000 to 2030).
    """
    if not rows:
        return None

    sample = rows[:100]
    max_cols = min(len(r) for r in sample) if sample else 0
    best_col = None
    best_rate = 0.0
    ts_min = 946684800   # 2000-01-01
    ts_max = 1893456000  # 2030-01-01

    for col_idx in range(start_col, max_cols):
        matches = 0
        for row in sample:
            val = row[col_idx]
            try:
                v = int(val)
                if ts_min < v < ts_max:
                    matches += 1
            except (ValueError, TypeError):
                continue
        rate = matches / len(sample) if sample else 0
        if rate > best_rate:
            best_rate = rate
            best_col = col_idx

    return best_col if best_rate > 0.5 else None


def _detect_topic_id_column(topic_rows: list[list], post_rows: list[list]) -> int | None:
    """Auto-detect which column in posts contains topic IDs.

    Cross-references post values against known topic IDs (col 0).
    """
    if not topic_rows or not post_rows:
        return None
    topic_ids = {str(row[0]) for row in topic_rows if row and row[0] is not None}
    return _detect_column(post_rows, topic_ids, start_col=2)


def detect_schema(raw: dict[str, list[list]]) -> dict:
    """Auto-detect column indices for all tables by cross-referencing data.

    Returns a dict of detected column indices for topics, posts, forums, members.
    Falls back to IPB 1.3.x defaults when detection fails.
    """
    forums_rows = raw.get("forums", [])
    topics_rows = raw.get("topics", [])
    posts_rows = raw.get("posts", [])
    members_rows = raw.get("members", [])

    # Log first row from each table for schema debugging
    if forums_rows:
        log_debug(f"ACP schema debug: forums[0] ({len(forums_rows[0])} cols) = {forums_rows[0][:10]}")
    if topics_rows:
        log_debug(f"ACP schema debug: topics[0] ({len(topics_rows[0])} cols) = {topics_rows[0][:20]}")
    if posts_rows:
        log_debug(f"ACP schema debug: posts[0] ({len(posts_rows[0])} cols) = {posts_rows[0][:16]}")
    if members_rows:
        log_debug(f"ACP schema debug: members[0] ({len(members_rows[0])} cols) = {members_rows[0][:12]}")

    # Step 1: Get forum IDs from forums table (col 0 is always id)
    forum_ids = set()
    for row in forums_rows:
        if row and row[0] is not None:
            forum_ids.add(str(row[0]))

    # Step 2: Get topic IDs from topics table (col 0 is always id)
    topic_ids = set()
    for row in topics_rows:
        if row and row[0] is not None:
            topic_ids.add(str(row[0]))

    # Step 3: Get member IDs from members table (col 0 is always id)
    member_ids = set()
    for row in members_rows:
        if row and row[0] is not None:
            member_ids.add(str(row[0]))

    schema = {}

    # Track assigned columns per table to prevent collisions.
    # When forum IDs and member IDs overlap (e.g. both contain {1,2,3}),
    # the same column could be detected as both forum_id and poster_id.
    topic_assigned: set[int] = set()
    post_assigned: set[int] = set()

    # ── Forum columns ──
    forum_name_col = _detect_name_column(forums_rows, start_col=1)
    schema["forum_name"] = forum_name_col if forum_name_col is not None else 6
    log_debug(f"ACP schema: forum name col = {schema['forum_name']}"
              f"{' (auto)' if forum_name_col is not None else ' (default)'}")

    # ── Topic columns ──
    # Detect forum_id first (fewer distinct values = more specific match)
    topic_forum_col = _detect_column(topics_rows, forum_ids, start_col=2,
                                      exclude_cols=topic_assigned)
    schema["topic_forum_id"] = topic_forum_col if topic_forum_col is not None else 15
    topic_assigned.add(schema["topic_forum_id"])
    log_debug(f"ACP schema: topic forum_id col = {schema['topic_forum_id']}"
              f"{' (auto)' if topic_forum_col is not None else ' (default)'}")

    topic_title_col = _detect_name_column(topics_rows, start_col=1)
    schema["topic_title"] = topic_title_col if topic_title_col is not None else 1
    topic_assigned.add(schema["topic_title"])

    topic_last_post_col = _detect_timestamp_column(topics_rows, start_col=2)
    schema["topic_last_post_date"] = topic_last_post_col if topic_last_post_col is not None else 8
    topic_assigned.add(schema["topic_last_post_date"])

    # Last poster ID: find column in topics containing member IDs,
    # EXCLUDING the column already assigned to forum_id
    topic_poster_col = _detect_column(topics_rows, member_ids, start_col=2,
                                       exclude_cols=topic_assigned)
    schema["topic_last_poster_id"] = topic_poster_col if topic_poster_col is not None else 7
    topic_assigned.add(schema["topic_last_poster_id"])
    log_debug(f"ACP schema: topic last_poster_id col = {schema['topic_last_poster_id']}"
              f"{' (auto)' if topic_poster_col is not None else ' (default)'}")

    # Last poster name: find a name column AFTER the poster ID column
    poster_name_start = schema["topic_last_poster_id"] + 1
    topic_poster_name_col = _detect_name_column(topics_rows, start_col=poster_name_start)
    schema["topic_last_poster_name"] = topic_poster_name_col if topic_poster_name_col is not None else 11

    # ── Post columns ──
    post_forum_col = _detect_column(posts_rows, forum_ids, start_col=2,
                                     exclude_cols=post_assigned)
    schema["post_forum_id"] = post_forum_col if post_forum_col is not None else 13
    post_assigned.add(schema["post_forum_id"])

    post_topic_col = _detect_column(posts_rows, topic_ids, start_col=2,
                                     exclude_cols=post_assigned)
    schema["post_topic_id"] = post_topic_col if post_topic_col is not None else 12
    post_assigned.add(schema["post_topic_id"])

    post_author_col = _detect_column(posts_rows, member_ids, start_col=2,
                                      exclude_cols=post_assigned)
    schema["post_author_id"] = post_author_col if post_author_col is not None else 3
    post_assigned.add(schema["post_author_id"])

    post_date_col = _detect_timestamp_column(posts_rows, start_col=2)
    schema["post_date"] = post_date_col if post_date_col is not None else 8

    # Author name: name column near the author_id column
    author_name_start = schema["post_author_id"] + 1
    post_author_name_col = _detect_name_column(posts_rows, start_col=author_name_start)
    schema["post_author_name"] = post_author_name_col if post_author_name_col is not None else 4

    # Post body: longest string column
    if posts_rows:
        sample = posts_rows[:50]
        max_cols = min(len(r) for r in sample) if sample else 0
        best_body_col = None
        best_avg_len = 0
        for col_idx in range(2, max_cols):
            total_len = 0
            str_count = 0
            for row in sample:
                val = row[col_idx]
                if isinstance(val, str) and len(val) > 50:
                    total_len += len(val)
                    str_count += 1
            if str_count > len(sample) * 0.3:
                avg = total_len / str_count
                if avg > best_avg_len:
                    best_avg_len = avg
                    best_body_col = col_idx
        schema["post_body"] = best_body_col if best_body_col is not None else 10

    # ── Member columns ──
    member_name_col = _detect_name_column(members_rows, start_col=1)
    schema["member_name"] = member_name_col if member_name_col is not None else 1

    log_debug(f"ACP schema detected: {schema}")
    return schema


def extract_post_records(raw: dict[str, list[list]], include_body: bool = False,
                         schema: dict | None = None) -> list[dict]:
    """Extract structured post records from parsed SQL dump.

    Returns list of dicts with: character_id, thread_id, post_date, forum_id, author_name
    When include_body=True, also includes post_body (HTML content) for quote extraction.
    """
    s = schema or {}
    col_author = s.get("post_author_id", 3)
    col_author_name = s.get("post_author_name", 4)
    col_date = s.get("post_date", 8)
    col_body = s.get("post_body", 10)
    col_topic = s.get("post_topic_id", 12)
    col_forum = s.get("post_forum_id", 13)

    min_col = max(col_author, col_date, col_topic, col_forum)
    posts_rows = raw.get("posts", [])
    records = []

    for row in posts_rows:
        if len(row) <= min_col:
            continue

        author_id = row[col_author]
        if author_id is None:
            continue

        record = {
            "character_id": str(author_id),
            "thread_id": str(row[col_topic]) if row[col_topic] else None,
            "post_date": _unix_to_iso(row[col_date]),
            "forum_id": str(row[col_forum]) if row[col_forum] else None,
            "author_name": row[col_author_name] if col_author_name < len(row) else None,
        }

        if include_body and col_body < len(row):
            body = row[col_body]
            record["post_body"] = body if isinstance(body, str) else None

        records.append(record)

    return records


def extract_topic_records(raw: dict[str, list[list]], schema: dict | None = None) -> list[dict]:
    """Extract structured topic (thread) records from parsed SQL dump.

    Returns list of dicts with: thread_id, title, forum_id, state,
    last_poster_id, last_poster_name, last_post_date
    """
    s = schema or {}
    col_title = s.get("topic_title", 1)
    col_forum = s.get("topic_forum_id", 15)
    col_last_post = s.get("topic_last_post_date", 8)
    col_poster_id = s.get("topic_last_poster_id", 7)
    col_poster_name = s.get("topic_last_poster_name", 11)

    topics_rows = raw.get("topics", [])
    records = []

    min_cols = max(_TOPIC_COL_ID, col_title, col_forum,
                   col_last_post, col_poster_id, col_poster_name) + 1

    for row in topics_rows:
        if len(row) < min_cols:
            continue

        topic_id = row[_TOPIC_COL_ID]
        if topic_id is None:
            continue

        records.append({
            "thread_id": str(topic_id),
            "title": row[col_title] or "Untitled",
            "forum_id": str(row[col_forum]) if row[col_forum] else None,
            "state": row[3] if len(row) > 3 else None,
            "last_poster_id": str(row[col_poster_id]) if row[col_poster_id] else None,
            "last_poster_name": row[col_poster_name] if isinstance(row[col_poster_name], str) else None,
            "last_post_date": _unix_to_iso(row[col_last_post]),
        })

    return records


def extract_member_records(raw: dict[str, list[list]], schema: dict | None = None) -> list[dict]:
    """Extract structured member records from parsed SQL dump.

    Returns list of dicts with: member_id, name, post_count
    """
    s = schema or {}
    col_name = s.get("member_name", 1)

    members_rows = raw.get("members", [])
    records = []

    for row in members_rows:
        if len(row) <= col_name:
            continue

        member_id = row[_MEMBER_COL_ID]
        if member_id is None:
            continue

        # Post count: find the first integer column > 2 that looks like a count
        post_count = 0
        if len(row) > 9:
            try:
                post_count = int(row[9]) if row[9] is not None else 0
            except (ValueError, TypeError):
                post_count = 0

        records.append({
            "member_id": str(member_id),
            "name": row[col_name] or "Unknown",
            "post_count": post_count,
        })

    return records


def extract_forum_records(raw: dict[str, list[list]], schema: dict | None = None) -> list[dict]:
    """Extract structured forum records from parsed SQL dump.

    Returns list of dicts with: forum_id, name, category_id
    """
    s = schema or {}
    col_name = s.get("forum_name", 6)

    forums_rows = raw.get("forums", [])
    records = []

    for row in forums_rows:
        if len(row) <= col_name:
            continue

        forum_id = row[_FORUM_COL_ID]
        if forum_id is None:
            continue

        records.append({
            "forum_id": str(forum_id),
            "name": row[col_name] or "Unknown Forum",
            "category_id": None,  # not critical for functionality
        })

    return records


class ACPClient:
    """Client for JCink's Admin Control Panel MySQL dump feature."""

    def __init__(self, username: str | None = None, password: str | None = None):
        self._client: httpx.AsyncClient | None = None
        self._token: str | None = None
        self._forum_name: str | None = None
        self._username = username or settings.admin_username
        self._password = password or settings.admin_password

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=120.0,
                follow_redirects=False,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; TWAICrawler/1.0)",
                },
            )
        return self._client

    async def login(self) -> bool:
        """Login to ACP and obtain adsess token.

        JCink ACP login: GET /admin.php?login=yes&username=X&password=Y
        On success, redirects to a URL containing adsess=TOKEN.
        """
        if not self._username or not self._password:
            log_debug("ACP: no admin credentials configured", level="error")
            return False

        # Extract forum name from base URL
        match = re.search(r"https?://(\w+)\.jcink\.net", settings.forum_base_url)
        if match:
            self._forum_name = match.group(1)
        else:
            log_debug("ACP: could not extract forum name from base URL", level="error")
            return False

        client = await self._get_client()

        login_url = (
            f"{settings.forum_base_url}/admin.php"
            f"?login=yes"
            f"&username={url_quote(self._username)}"
            f"&password={url_quote(self._password)}"
        )

        try:
            response = await client.get(login_url)

            # Check redirect for adsess token
            redirect_url = response.headers.get("location", "")
            if not redirect_url:
                redirect_url = str(response.url)

            if "adsess=" in redirect_url:
                idx = redirect_url.index("adsess=") + 7
                end = redirect_url.find("&", idx)
                self._token = redirect_url[idx:end] if end != -1 else redirect_url[idx:]
                log_debug("ACP: login successful, token obtained")
                return True

            # Maybe got a page with a redirect in the HTML
            if response.status_code == 200:
                text = response.text
                adsess_match = re.search(r"adsess=([a-f0-9]+)", text)
                if adsess_match:
                    self._token = adsess_match.group(1)
                    log_debug("ACP: login successful (from response body)")
                    return True

            log_debug(f"ACP: login failed — status {response.status_code}, no adsess token found", level="error")
            return False

        except Exception as e:
            log_debug(f"ACP: login failed: {e}", level="error")
            return False

    async def _dump_database(self, table_parts: list[str] | None = None) -> str | None:
        """Trigger a full MySQL dump via ACP and return the SQL file contents.

        Follows JCink's ACP dump pagination sequentially from step1=1 through
        all pages until no more "next" links are found.  This ensures all
        tables (topics, posts, forums, members, etc.) are included regardless
        of part numbering, which can vary between JCink instances.

        Flow:
        1. Clear previous backup
        2. Start dump with step1=1
        3. Follow every pagination link until done
        4. Wait for SQL file generation
        5. Fetch and return the SQL file
        """
        if not self._token:
            log_debug("ACP: no token — must login first", level="error")
            return None

        client = await self._get_client()
        base = settings.forum_base_url

        # Step 1: Clear old backup
        log_debug("ACP: clearing previous backup")
        try:
            await client.get(
                f"{base}/admin.php?act=mysql&code=backup&erase=1&adsess={self._token}",
                follow_redirects=True,
            )
        except Exception as e:
            log_debug(f"ACP: backup clear failed: {e}", level="warn")

        await asyncio.sleep(1)

        # Step 2: Start the full dump — step1=1 begins at page 1
        log_debug("ACP: starting full database dump")
        try:
            init_resp = await client.get(
                f"{base}/admin.php?act=mysql&code=dump&step1=1&adsess={self._token}",
                follow_redirects=True,
            )
            html = init_resp.text
        except Exception as e:
            log_debug(f"ACP: dump init failed: {e}", level="error")
            return None

        # Step 3: Follow ALL pagination links sequentially
        # Each page dumps a batch of rows; the "next" link advances through
        # every table in the database.  We follow blindly until done.
        total_pages = 1
        parts_seen: set[str] = set()
        max_total_pages = 2000  # Safety limit

        match = _NEXT_LINK_RE.search(html)
        if match:
            parts_seen.add(match.group(2))
        else:
            # Log a snippet of the response so we can diagnose link format issues
            # Look for any admin.php links in the HTML
            any_links = re.findall(r"admin\.php\?[^'\"<>\s]{10,120}", html)
            if any_links:
                log_debug(f"ACP: no pagination match on init page, but found links: {any_links[:3]}", level="warn")
            else:
                snippet = html[:500].replace("\n", " ").strip()
                log_debug(f"ACP: no links found in init response ({len(html)} chars): {snippet[:200]}...", level="warn")

        while match and total_pages < max_total_pages:
            line = int(match.group(1))
            part = match.group(2)
            parts_seen.add(part)

            url = f"{base}/admin.php?act=mysql&adsess={self._token}&code=dump&line={line}&part={part}"
            try:
                resp = await client.get(url, follow_redirects=True)
                html = resp.text
            except Exception as e:
                log_debug(f"ACP: dump page failed (part={part} line={line}): {e}", level="error")
                break

            total_pages += 1
            match = _NEXT_LINK_RE.search(html)

            # Brief pause every 10 pages to be polite
            if total_pages % 10 == 0:
                log_debug(f"ACP: dump progress — {total_pages} pages, parts seen: {sorted(parts_seen)}")
                await asyncio.sleep(0.3)

        log_debug(
            f"ACP: dump complete — {total_pages} pages, "
            f"{len(parts_seen)} table parts: {sorted(parts_seen)}"
        )

        # Step 4: Wait for SQL file generation
        sql_url = f"{base}/sqls/{self._token}-{self._forum_name}_.sql"
        sql_content = None

        for wait_secs in [2, 5, 10, 15, 30]:
            log_debug(f"ACP: waiting {wait_secs}s for SQL file")
            await asyncio.sleep(wait_secs)
            try:
                resp = await client.get(sql_url, follow_redirects=True)
                if resp.status_code == 200 and len(resp.text) > 100:
                    sql_content = resp.text
                    log_debug(f"ACP: SQL file retrieved ({len(sql_content):,} bytes)")
                    break
                else:
                    log_debug(f"ACP: SQL file not ready (status={resp.status_code}, size={len(resp.text)})")
            except Exception as e:
                log_debug(f"ACP: SQL file fetch error: {e}", level="warn")

        if not sql_content:
            log_debug("ACP: failed to retrieve SQL file after all retries", level="error")

        return sql_content

    async def fetch_posts(self) -> list[dict]:
        """Login, dump database, and extract post records.

        Returns list of post dicts with:
            character_id, thread_id, post_date (ISO), forum_id, author_name
        """
        if not await self.login():
            return []

        sql_content = await self._dump_database()
        if not sql_content:
            return []

        log_debug("ACP: parsing SQL dump")
        raw = parse_sql_dump(sql_content)

        tables_found = list(raw.keys())
        log_debug(f"ACP: tables found: {tables_found}")

        posts = extract_post_records(raw)
        log_debug(f"ACP: extracted {len(posts)} post records")

        return posts

    async def fetch_all_data(self, table_parts: list[str] | None = None) -> dict[str, list]:
        """Login, dump database, and return all parsed data.

        Returns dict with keys like 'posts', 'topics', 'members', 'forums', etc.
        Each value is a list of row arrays.
        """
        if not await self.login():
            return {}

        sql_content = await self._dump_database()
        if not sql_content:
            return {}

        log_debug("ACP: parsing SQL dump")
        raw = parse_sql_dump(sql_content)
        row_counts = {k: len(v) for k, v in raw.items()}
        log_debug(f"ACP: tables parsed — {row_counts}")

        return raw

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
        self._token = None
