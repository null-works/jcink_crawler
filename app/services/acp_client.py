"""JCink Admin Control Panel client for database dumps.

Replicates the login + MySQL dump flow from databaseParser.js:
1. Login to ACP → get adsess token
2. Clear previous backup
3. Iterate through dump pagination
4. Fetch generated SQL file
5. Parse REPLACE INTO statements into structured data

This gives us accurate post dates (Unix timestamps from ibf_posts)
instead of scraping dates from HTML which is fragile.
"""

import asyncio
import json
import re
from datetime import datetime, timezone
from urllib.parse import quote as url_quote

import httpx

from app.config import settings

# Column indices for JCink's ibf_posts table (IPB 1.3.x schema).
# Matches the fizzy activity tracker's field mappings.
_POST_COL_AUTHOR_ID = 3
_POST_COL_AUTHOR_NAME = 4
_POST_COL_POST_DATE = 8
_POST_COL_POST_BODY = 10
_POST_COL_TOPIC_ID = 12
_POST_COL_FORUM_ID = 13

# Column indices for ibf_topics
_TOPIC_COL_ID = 0
_TOPIC_COL_TITLE = 1
_TOPIC_COL_STATE = 3
_TOPIC_COL_LAST_POST_DATE = 8
_TOPIC_COL_LAST_POSTER_ID = 7
_TOPIC_COL_LAST_POSTER_NAME = 11
_TOPIC_COL_FORUM_ID = 15

# Column indices for ibf_members
_MEMBER_COL_ID = 0
_MEMBER_COL_NAME = 1
_MEMBER_COL_POST_COUNT = 9

# Regex to find "next page" link in ACP dump pagination
_NEXT_LINK_RE = re.compile(
    r"admin\.php\?[^'\"]*act=mysql[^'\"]*code=dump[^'\"]*line=(\d+)[^'\"]*part=(\d+)[^'\"]*adsess=([a-f0-9]+)",
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


def extract_post_records(raw: dict[str, list[list]], include_body: bool = False) -> list[dict]:
    """Extract structured post records from parsed SQL dump.

    Returns list of dicts with: character_id, thread_id, post_date, forum_id, author_name
    When include_body=True, also includes post_body (HTML content) for quote extraction.
    """
    posts_rows = raw.get("posts", [])
    records = []

    for row in posts_rows:
        if len(row) <= max(_POST_COL_AUTHOR_ID, _POST_COL_POST_DATE, _POST_COL_TOPIC_ID, _POST_COL_FORUM_ID):
            continue

        author_id = row[_POST_COL_AUTHOR_ID]
        if author_id is None:
            continue

        record = {
            "character_id": str(author_id),
            "thread_id": str(row[_POST_COL_TOPIC_ID]) if row[_POST_COL_TOPIC_ID] else None,
            "post_date": _unix_to_iso(row[_POST_COL_POST_DATE]),
            "forum_id": str(row[_POST_COL_FORUM_ID]) if row[_POST_COL_FORUM_ID] else None,
            "author_name": row[_POST_COL_AUTHOR_NAME],
        }

        if include_body and len(row) > _POST_COL_POST_BODY:
            body = row[_POST_COL_POST_BODY]
            record["post_body"] = body if isinstance(body, str) else None

        records.append(record)

    return records


def extract_topic_records(raw: dict[str, list[list]]) -> list[dict]:
    """Extract structured topic (thread) records from parsed SQL dump.

    Returns list of dicts with: thread_id, title, forum_id, state,
    last_poster_id, last_poster_name, last_post_date
    """
    topics_rows = raw.get("topics", [])
    records = []

    min_cols = max(
        _TOPIC_COL_ID, _TOPIC_COL_TITLE, _TOPIC_COL_STATE,
        _TOPIC_COL_LAST_POSTER_ID, _TOPIC_COL_LAST_POST_DATE,
        _TOPIC_COL_LAST_POSTER_NAME, _TOPIC_COL_FORUM_ID,
    ) + 1

    for row in topics_rows:
        if len(row) < min_cols:
            continue

        topic_id = row[_TOPIC_COL_ID]
        if topic_id is None:
            continue

        records.append({
            "thread_id": str(topic_id),
            "title": row[_TOPIC_COL_TITLE] or "Untitled",
            "forum_id": str(row[_TOPIC_COL_FORUM_ID]) if row[_TOPIC_COL_FORUM_ID] else None,
            "state": row[_TOPIC_COL_STATE],
            "last_poster_id": str(row[_TOPIC_COL_LAST_POSTER_ID]) if row[_TOPIC_COL_LAST_POSTER_ID] else None,
            "last_poster_name": row[_TOPIC_COL_LAST_POSTER_NAME] if isinstance(row[_TOPIC_COL_LAST_POSTER_NAME], str) else None,
            "last_post_date": _unix_to_iso(row[_TOPIC_COL_LAST_POST_DATE]),
        })

    return records


def extract_member_records(raw: dict[str, list[list]]) -> list[dict]:
    """Extract structured member records from parsed SQL dump.

    Returns list of dicts with: member_id, name, post_count
    """
    members_rows = raw.get("members", [])
    records = []

    min_cols = max(_MEMBER_COL_ID, _MEMBER_COL_NAME, _MEMBER_COL_POST_COUNT) + 1

    for row in members_rows:
        if len(row) < min_cols:
            continue

        member_id = row[_MEMBER_COL_ID]
        if member_id is None:
            continue

        post_count = row[_MEMBER_COL_POST_COUNT]
        try:
            post_count = int(post_count) if post_count is not None else 0
        except (ValueError, TypeError):
            post_count = 0

        records.append({
            "member_id": str(member_id),
            "name": row[_MEMBER_COL_NAME] or "Unknown",
            "post_count": post_count,
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
            print("[ACP] No admin credentials configured")
            return False

        # Extract forum name from base URL
        match = re.search(r"https?://(\w+)\.jcink\.net", settings.forum_base_url)
        if match:
            self._forum_name = match.group(1)
        else:
            print("[ACP] Could not extract forum name from base URL")
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
                print(f"[ACP] Login successful, token obtained")
                return True

            # Maybe got a page with a redirect in the HTML
            if response.status_code == 200:
                text = response.text
                adsess_match = re.search(r"adsess=([a-f0-9]+)", text)
                if adsess_match:
                    self._token = adsess_match.group(1)
                    print(f"[ACP] Login successful (from response body)")
                    return True

            print(f"[ACP] Login failed — status {response.status_code}, no adsess token found")
            return False

        except Exception as e:
            print(f"[ACP] Login failed: {e}")
            return False

    async def _dump_database(self) -> str | None:
        """Trigger a MySQL dump and return the SQL file contents.

        Flow:
        1. Clear previous backup
        2. Start dump with step1=1
        3. Follow pagination links until no more
        4. Wait for SQL file generation
        5. Fetch and return the SQL file
        """
        if not self._token:
            print("[ACP] No token — must login first")
            return None

        client = await self._get_client()
        base = settings.forum_base_url

        # Step 1: Clear old backup
        print("[ACP] Clearing previous backup...")
        try:
            await client.get(
                f"{base}/admin.php?act=mysql&code=backup&erase=1&adsess={self._token}",
                follow_redirects=True,
            )
        except Exception as e:
            print(f"[ACP] Warning: backup clear failed: {e}")

        await asyncio.sleep(1)

        # Step 2: Start dump
        print("[ACP] Starting database dump...")
        try:
            resp = await client.get(
                f"{base}/admin.php?act=mysql&code=dump&step1=1&adsess={self._token}",
                follow_redirects=True,
            )
            html = resp.text
        except Exception as e:
            print(f"[ACP] Dump start failed: {e}")
            return None

        # Step 3: Follow pagination links
        page_count = 0
        max_pages = 500  # Safety limit
        while page_count < max_pages:
            match = _NEXT_LINK_RE.search(html)
            if not match:
                break

            line = match.group(1)
            part = match.group(2)
            page_count += 1

            if page_count % 10 == 0:
                print(f"[ACP] Dump progress: page {page_count} (part {part}, line {line})")

            try:
                resp = await client.get(
                    f"{base}/admin.php?act=mysql&code=dump&line={line}&part={part}&adsess={self._token}",
                    follow_redirects=True,
                )
                html = resp.text
            except Exception as e:
                print(f"[ACP] Dump page failed: {e}")
                break

            await asyncio.sleep(0.5)  # Be polite to the ACP

        print(f"[ACP] Dump pagination complete after {page_count} pages")

        # Step 4: Wait for SQL file generation
        sql_url = f"{base}/sqls/{self._token}-{self._forum_name}_.sql"
        sql_content = None

        for wait_secs in [2, 5, 10, 15, 30]:
            print(f"[ACP] Waiting {wait_secs}s for SQL file...")
            await asyncio.sleep(wait_secs)
            try:
                resp = await client.get(sql_url, follow_redirects=True)
                if resp.status_code == 200 and len(resp.text) > 100:
                    sql_content = resp.text
                    print(f"[ACP] SQL file retrieved ({len(sql_content)} bytes)")
                    break
            except Exception as e:
                print(f"[ACP] SQL file not ready: {e}")

        if not sql_content:
            print("[ACP] Failed to retrieve SQL file after all retries")

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

        print("[ACP] Parsing SQL dump...")
        raw = parse_sql_dump(sql_content)

        tables_found = list(raw.keys())
        print(f"[ACP] Tables found in dump: {tables_found}")

        posts = extract_post_records(raw)
        print(f"[ACP] Extracted {len(posts)} post records")

        return posts

    async def fetch_all_data(self) -> dict[str, list]:
        """Login, dump database, and return all parsed tables.

        Returns dict with keys like 'posts', 'topics', 'members', etc.
        Each value is a list of row arrays.
        """
        if not await self.login():
            return {}

        sql_content = await self._dump_database()
        if not sql_content:
            return {}

        print("[ACP] Parsing SQL dump...")
        raw = parse_sql_dump(sql_content)
        print(f"[ACP] Tables: {list(raw.keys())} ({sum(len(v) for v in raw.values())} total rows)")

        return raw

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
        self._token = None
