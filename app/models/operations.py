"""Database operations for characters, threads, quotes, and profile fields.

Commit convention:
- "Top-level" operations that are always called standalone (upsert_character,
  update_character_crawl_time, set_crawl_status, delete_character) auto-commit.
- "Batch-friendly" operations (upsert_thread, link_character_thread, add_quote,
  upsert_profile_field, mark_thread_quote_scraped, replace_thread_posts) do NOT
  commit — callers must commit explicitly after a batch of writes.
"""

import aiosqlite
from app.config import settings
import re
from app.models.character import (
    CharacterSummary,
    ClaimsSummary,
    ThreadInfo,
    ThreadCategory,
    CharacterThreads,
    Quote,
    Relationship,
)


# --- Character Operations ---

async def get_character(db: aiosqlite.Connection, character_id: str) -> CharacterSummary | None:
    """Get character by ID."""
    cursor = await db.execute(
        """SELECT c.*, pf.field_value AS affiliation
           FROM characters c
           LEFT JOIN profile_fields pf
             ON pf.character_id = c.id AND pf.field_key = ?
           WHERE c.id = ?""",
        (settings.affiliation_field_key, character_id),
    )
    row = await cursor.fetchone()
    if not row:
        return None

    char = dict(row)
    # Get thread counts
    counts = await get_thread_counts(db, character_id)
    return CharacterSummary(
        id=char["id"],
        name=char["name"],
        profile_url=char["profile_url"],
        group_name=char.get("group_name"),
        avatar_url=char.get("avatar_url"),
        affiliation=char.get("affiliation"),
        thread_counts=counts,
        last_profile_crawl=char.get("last_profile_crawl"),
        last_thread_crawl=char.get("last_thread_crawl"),
    )


async def get_all_characters(db: aiosqlite.Connection) -> list[CharacterSummary]:
    """Get all tracked characters, excluding filtered names and IDs."""
    excluded = settings.excluded_name_set
    excluded_ids = settings.excluded_id_set

    # 1. All characters
    cursor = await db.execute(
        "SELECT * FROM characters WHERE COALESCE(hidden, 0) = 0 ORDER BY name"
    )
    rows = await cursor.fetchall()
    if not rows:
        return []

    char_ids = [row["id"] for row in rows if row["name"].lower() not in excluded and row["id"] not in excluded_ids]
    if not char_ids:
        return []

    # 2. Batch-load profile fields: affiliation, square_image, alias
    _panel_field_keys = [settings.affiliation_field_key, "square_image", "alias"]
    placeholders_ids = ",".join("?" * len(char_ids))
    placeholders_keys = ",".join("?" * len(_panel_field_keys))
    cursor = await db.execute(
        f"""SELECT character_id, field_key, field_value
            FROM profile_fields
            WHERE character_id IN ({placeholders_ids})
              AND field_key IN ({placeholders_keys})""",
        [*char_ids, *_panel_field_keys],
    )
    field_rows = await cursor.fetchall()

    fields_map: dict[str, dict[str, str]] = {}
    for row in field_rows:
        fields_map.setdefault(row["character_id"], {})[row["field_key"]] = row["field_value"]

    # 3. Batch-load thread counts
    cursor = await db.execute(
        f"""SELECT character_id, category, COUNT(*) as count
            FROM character_threads
            WHERE character_id IN ({placeholders_ids})
            GROUP BY character_id, category""",
        char_ids,
    )
    count_rows = await cursor.fetchall()

    counts_map: dict[str, dict[str, int]] = {}
    for row in count_rows:
        counts_map.setdefault(row["character_id"], {})[row["category"]] = row["count"]

    # 4. Assemble results
    results = []
    for row in rows:
        char = dict(row)
        if char["name"].lower() in excluded or char["id"] in excluded_ids:
            continue

        cid = char["id"]
        fields = fields_map.get(cid, {})
        raw_counts = counts_map.get(cid, {})
        thread_counts = {
            "ongoing": raw_counts.get("ongoing", 0),
            "comms": raw_counts.get("comms", 0),
            "complete": raw_counts.get("complete", 0),
            "incomplete": raw_counts.get("incomplete", 0),
        }
        thread_counts["total"] = sum(thread_counts.values())

        results.append(CharacterSummary(
            id=cid,
            name=char["name"],
            profile_url=char["profile_url"],
            group_name=char.get("group_name"),
            avatar_url=char.get("avatar_url"),
            square_image=fields.get("square_image"),
            alias=fields.get("alias"),
            affiliation=fields.get(settings.affiliation_field_key),
            thread_counts=thread_counts,
            last_profile_crawl=char.get("last_profile_crawl"),
            last_thread_crawl=char.get("last_thread_crawl"),
        ))
    return results


async def upsert_character(
    db: aiosqlite.Connection,
    character_id: str,
    name: str,
    profile_url: str,
    group_name: str | None = None,
    avatar_url: str | None = None,
) -> None:
    """Create or update a character."""
    await db.execute("""
        INSERT INTO characters (id, name, profile_url, group_name, avatar_url)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            profile_url = excluded.profile_url,
            group_name = excluded.group_name,
            avatar_url = excluded.avatar_url,
            updated_at = CURRENT_TIMESTAMP
    """, (character_id, name, profile_url, group_name, avatar_url))
    await db.commit()


async def toggle_character_hidden(
    db: aiosqlite.Connection,
    character_id: str,
) -> bool | None:
    """Toggle the hidden flag for a character. Returns new hidden state, or None if not found."""
    cursor = await db.execute(
        "SELECT hidden FROM characters WHERE id = ?", (character_id,)
    )
    row = await cursor.fetchone()
    if not row:
        return None
    new_val = 0 if row["hidden"] else 1
    await db.execute(
        "UPDATE characters SET hidden = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (new_val, character_id),
    )
    await db.commit()
    return bool(new_val)


async def update_character_crawl_time(
    db: aiosqlite.Connection,
    character_id: str,
    crawl_type: str,
) -> None:
    """Update the last crawl timestamp for a character."""
    column = "last_thread_crawl" if crawl_type == "threads" else "last_profile_crawl"
    await db.execute(
        f"UPDATE characters SET {column} = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (character_id,)
    )
    await db.commit()


# --- Thread Operations ---

async def upsert_thread(
    db: aiosqlite.Connection,
    thread_id: str,
    title: str,
    url: str,
    forum_id: str | None,
    forum_name: str | None,
    category: str,
    last_poster_id: str | None = None,
    last_poster_name: str | None = None,
    last_poster_avatar: str | None = None,
) -> None:
    """Create or update a thread."""
    await db.execute("""
        INSERT INTO threads (id, title, url, forum_id, forum_name, category,
                           last_poster_id, last_poster_name, last_poster_avatar,
                           last_crawled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            url = excluded.url,
            forum_id = excluded.forum_id,
            forum_name = excluded.forum_name,
            category = excluded.category,
            last_poster_id = excluded.last_poster_id,
            last_poster_name = excluded.last_poster_name,
            last_poster_avatar = COALESCE(excluded.last_poster_avatar, threads.last_poster_avatar),
            last_crawled = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
    """, (thread_id, title, url, forum_id, forum_name, category,
          last_poster_id, last_poster_name, last_poster_avatar))


async def link_character_thread(
    db: aiosqlite.Connection,
    character_id: str,
    thread_id: str,
    category: str,
    is_user_last_poster: bool = False,
    post_count: int = 0,
) -> None:
    """Link a character to a thread."""
    await db.execute("""
        INSERT INTO character_threads (character_id, thread_id, category, is_user_last_poster, post_count)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(character_id, thread_id) DO UPDATE SET
            category = excluded.category,
            is_user_last_poster = excluded.is_user_last_poster,
            post_count = excluded.post_count
    """, (character_id, thread_id, category, int(is_user_last_poster), post_count))


async def get_character_threads(
    db: aiosqlite.Connection, character_id: str
) -> CharacterThreads:
    """Get all threads for a character, categorized."""
    # Get character name
    cursor = await db.execute(
        "SELECT name FROM characters WHERE id = ?", (character_id,)
    )
    row = await cursor.fetchone()
    char_name = row["name"] if row else "Unknown"

    cursor = await db.execute("""
        SELECT t.id, t.title, t.url, t.forum_id, t.forum_name,
               t.last_poster_id, t.last_poster_name,
               ct.category as char_category, ct.is_user_last_poster,
               COALESCE(t.last_poster_avatar, c_poster.avatar_url) AS resolved_avatar,
               p_last.last_post_date,
               q_dialog.quote_text AS last_post_excerpt
        FROM threads t
        JOIN character_threads ct ON t.id = ct.thread_id
        LEFT JOIN characters c_poster ON c_poster.id = t.last_poster_id
        LEFT JOIN (
            SELECT thread_id, MAX(post_date) AS last_post_date
            FROM posts
            WHERE post_date IS NOT NULL
            GROUP BY thread_id
        ) p_last ON p_last.thread_id = t.id
        LEFT JOIN (
            SELECT source_thread_id, character_id, quote_text,
                   ROW_NUMBER() OVER (PARTITION BY source_thread_id, character_id ORDER BY id DESC) AS rn
            FROM quotes
            WHERE source_thread_id IS NOT NULL
        ) q_dialog ON q_dialog.source_thread_id = t.id
                  AND q_dialog.character_id = t.last_poster_id
                  AND q_dialog.rn = 1
        WHERE ct.character_id = ?
        ORDER BY t.updated_at DESC
    """, (character_id,))
    rows = await cursor.fetchall()

    threads = CharacterThreads(
        character_id=character_id,
        character_name=char_name,
    )

    for row in rows:
        r = dict(row)
        # Truncate dialog quote to 150 chars at word boundary
        excerpt = r.get("last_post_excerpt")
        if excerpt and len(excerpt) > 150:
            excerpt = excerpt[:150].rsplit(" ", 1)[0] + "\u2026"
        info = ThreadInfo(
            id=r["id"],
            title=r["title"],
            url=r["url"],
            forum_id=r.get("forum_id"),
            forum_name=r.get("forum_name"),
            category=r["char_category"],
            last_poster_id=r.get("last_poster_id"),
            last_poster_name=r.get("last_poster_name"),
            last_poster_avatar=r.get("resolved_avatar"),
            is_user_last_poster=bool(r.get("is_user_last_poster", 0)),
            last_post_date=r.get("last_post_date"),
            last_post_excerpt=excerpt,
        )
        cat = r["char_category"]
        if cat == "ongoing":
            threads.ongoing.append(info)
        elif cat == "comms":
            threads.comms.append(info)
        elif cat == "complete":
            threads.complete.append(info)
        elif cat == "incomplete":
            threads.incomplete.append(info)

    threads.counts = {
        "ongoing": len(threads.ongoing),
        "comms": len(threads.comms),
        "complete": len(threads.complete),
        "incomplete": len(threads.incomplete),
        "total": len(threads.ongoing) + len(threads.comms) + len(threads.complete) + len(threads.incomplete),
    }
    return threads


async def get_thread_counts(
    db: aiosqlite.Connection, character_id: str
) -> dict[str, int]:
    """Get thread counts by category for a character."""
    cursor = await db.execute("""
        SELECT category, COUNT(*) as count
        FROM character_threads
        WHERE character_id = ?
        GROUP BY category
    """, (character_id,))
    rows = await cursor.fetchall()
    counts = {r["category"]: r["count"] for r in rows}
    counts["total"] = sum(counts.values())
    return counts


# --- Quote Operations ---

async def add_quote(
    db: aiosqlite.Connection,
    character_id: str,
    quote_text: str,
    source_thread_id: str | None = None,
    source_thread_title: str | None = None,
) -> bool:
    """Add a quote if it doesn't already exist. Returns True if inserted."""
    try:
        cursor = await db.execute("""
            INSERT OR IGNORE INTO quotes
                (character_id, quote_text, source_thread_id, source_thread_title)
            VALUES (?, ?, ?, ?)
        """, (character_id, quote_text, source_thread_id, source_thread_title))
        return cursor.rowcount > 0
    except Exception as e:
        print(f"[DB] Failed to add quote for character {character_id}: {e}")
        return False


async def get_random_quote(
    db: aiosqlite.Connection, character_id: str
) -> Quote | None:
    """Get a random quote for a character."""
    cursor = await db.execute("""
        SELECT * FROM quotes
        WHERE character_id = ?
        ORDER BY RANDOM()
        LIMIT 1
    """, (character_id,))
    row = await cursor.fetchone()
    if row:
        return Quote(**dict(row))
    return None


async def get_all_quotes(
    db: aiosqlite.Connection, character_id: str
) -> list[Quote]:
    """Get all quotes for a character."""
    cursor = await db.execute("""
        SELECT * FROM quotes
        WHERE character_id = ?
        ORDER BY created_at DESC
    """, (character_id,))
    rows = await cursor.fetchall()
    return [Quote(**dict(r)) for r in rows]


async def get_quote_count(
    db: aiosqlite.Connection, character_id: str
) -> int:
    """Get total quote count for a character."""
    cursor = await db.execute(
        "SELECT COUNT(*) as count FROM quotes WHERE character_id = ?",
        (character_id,)
    )
    row = await cursor.fetchone()
    return row["count"] if row else 0


async def is_thread_quote_scraped(
    db: aiosqlite.Connection, thread_id: str, character_id: str
) -> bool:
    """Check if a thread has already been scraped for quotes."""
    cursor = await db.execute(
        "SELECT 1 FROM quote_crawl_log WHERE thread_id = ? AND character_id = ?",
        (thread_id, character_id)
    )
    return await cursor.fetchone() is not None


async def mark_thread_quote_scraped(
    db: aiosqlite.Connection, thread_id: str, character_id: str
) -> None:
    """Mark a thread as scraped for quotes."""
    await db.execute("""
        INSERT OR IGNORE INTO quote_crawl_log (thread_id, character_id)
        VALUES (?, ?)
    """, (thread_id, character_id))


# --- Profile Field Operations ---

async def upsert_profile_field(
    db: aiosqlite.Connection,
    character_id: str,
    field_key: str,
    field_value: str,
) -> None:
    """Create or update a profile field."""
    await db.execute("""
        INSERT INTO profile_fields (character_id, field_key, field_value)
        VALUES (?, ?, ?)
        ON CONFLICT(character_id, field_key) DO UPDATE SET
            field_value = excluded.field_value,
            updated_at = CURRENT_TIMESTAMP
    """, (character_id, field_key, field_value))


async def get_profile_fields(
    db: aiosqlite.Connection, character_id: str
) -> dict[str, str]:
    """Get all profile fields for a character."""
    cursor = await db.execute(
        "SELECT field_key, field_value FROM profile_fields WHERE character_id = ?",
        (character_id,)
    )
    rows = await cursor.fetchall()
    return {r["field_key"]: r["field_value"] for r in rows}


# --- Claims Operations ---

# Reverse lookup: group name -> group id from parser's _GROUP_MAP
_GROUP_NAME_TO_ID: dict[str, str] = {
    "Admin": "4",
    "Reserved": "5",
    "Red": "6",
    "Orange": "7",
    "Yellow": "8",
    "Green": "9",
    "Blue": "10",
    "Purple": "11",
    "Corrupted": "12",
    "Pastel": "13",
    "Pink": "14",
    "Neutral": "15",
}

# Profile field keys needed for claims
_CLAIMS_FIELD_KEYS = (
    "face claim",
    "species",
    "codename",
    "alias",
    "player",
    "affiliation",
    "connections",
)


async def get_all_claims(db: aiosqlite.Connection) -> list[ClaimsSummary]:
    """Get all characters with claims-specific profile fields and thread counts.

    Single bulk query approach: fetch all characters, then batch-load their
    profile fields and thread counts to avoid N+1 queries.
    """
    excluded = settings.excluded_name_set
    excluded_ids = settings.excluded_id_set

    # 1. All characters
    cursor = await db.execute(
        "SELECT id, name, profile_url, group_name, avatar_url FROM characters WHERE COALESCE(hidden, 0) = 0 ORDER BY name"
    )
    char_rows = await cursor.fetchall()

    if not char_rows:
        return []

    char_ids = [row["id"] for row in char_rows if row["name"].lower() not in excluded and row["id"] not in excluded_ids]
    if not char_ids:
        return []

    # 2. Batch-load claims-relevant profile fields
    placeholders_ids = ",".join("?" * len(char_ids))
    placeholders_keys = ",".join("?" * len(_CLAIMS_FIELD_KEYS))
    cursor = await db.execute(
        f"""SELECT character_id, field_key, field_value
            FROM profile_fields
            WHERE character_id IN ({placeholders_ids})
              AND field_key IN ({placeholders_keys})""",
        [*char_ids, *_CLAIMS_FIELD_KEYS],
    )
    field_rows = await cursor.fetchall()

    # Build {character_id: {field_key: field_value}}
    fields_map: dict[str, dict[str, str]] = {}
    for row in field_rows:
        fields_map.setdefault(row["character_id"], {})[row["field_key"]] = row["field_value"]

    # 3. Batch-load thread counts
    cursor = await db.execute(
        f"""SELECT character_id, category, COUNT(*) as count
            FROM character_threads
            WHERE character_id IN ({placeholders_ids})
            GROUP BY character_id, category""",
        char_ids,
    )
    count_rows = await cursor.fetchall()

    # Build {character_id: {category: count}}
    counts_map: dict[str, dict[str, int]] = {}
    for row in count_rows:
        counts_map.setdefault(row["character_id"], {})[row["category"]] = row["count"]

    # 4. Assemble results
    results = []
    for row in char_rows:
        char = dict(row)
        if char["name"].lower() in excluded or char["id"] in excluded_ids:
            continue

        cid = char["id"]
        fields = fields_map.get(cid, {})
        raw_counts = counts_map.get(cid, {})
        thread_counts = {
            "ongoing": raw_counts.get("ongoing", 0),
            "comms": raw_counts.get("comms", 0),
            "complete": raw_counts.get("complete", 0),
            "incomplete": raw_counts.get("incomplete", 0),
        }
        thread_counts["total"] = sum(thread_counts.values())

        group_name = char.get("group_name")
        group_id = _GROUP_NAME_TO_ID.get(group_name) if group_name else None

        results.append(ClaimsSummary(
            id=cid,
            name=char["name"],
            profile_url=char["profile_url"],
            group_id=group_id,
            group_name=group_name,
            avatar_url=char.get("avatar_url"),
            face_claim=fields.get("face claim"),
            species=fields.get("species"),
            codename=fields.get("codename"),
            alias=fields.get("alias") or fields.get("player"),
            affiliation=fields.get("affiliation"),
            connections=fields.get("connections"),
            thread_counts=thread_counts,
        ))

    return results


# --- Batch Field Operations ---

async def get_characters_fields_batch(
    db: aiosqlite.Connection,
    character_ids: list[str],
    field_keys: list[str] | None = None,
) -> dict[str, dict[str, str]]:
    """Get profile fields for multiple characters in one query.

    Args:
        character_ids: List of character IDs to fetch.
        field_keys: Optional list of specific field keys to return.
                    If None, returns all fields for each character.

    Returns:
        {character_id: {field_key: field_value, ...}, ...}
    """
    if not character_ids:
        return {}

    placeholders_ids = ",".join("?" * len(character_ids))

    if field_keys:
        placeholders_keys = ",".join("?" * len(field_keys))
        cursor = await db.execute(
            f"""SELECT character_id, field_key, field_value
                FROM profile_fields
                WHERE character_id IN ({placeholders_ids})
                  AND field_key IN ({placeholders_keys})""",
            [*character_ids, *field_keys],
        )
    else:
        cursor = await db.execute(
            f"""SELECT character_id, field_key, field_value
                FROM profile_fields
                WHERE character_id IN ({placeholders_ids})""",
            character_ids,
        )

    rows = await cursor.fetchall()
    result: dict[str, dict[str, str]] = {cid: {} for cid in character_ids}
    for row in rows:
        result.setdefault(row["character_id"], {})[row["field_key"]] = row["field_value"]
    return result


# --- Crawl Status Operations ---

async def set_crawl_status(
    db: aiosqlite.Connection, key: str, value: str
) -> None:
    """Set a crawl status value."""
    await db.execute("""
        INSERT INTO crawl_status (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
    """, (key, value))
    await db.commit()


async def get_crawl_status(
    db: aiosqlite.Connection, key: str
) -> str | None:
    """Get a crawl status value."""
    cursor = await db.execute(
        "SELECT value FROM crawl_status WHERE key = ?", (key,)
    )
    row = await cursor.fetchone()
    return row["value"] if row else None


# --- Post Operations ---

async def replace_thread_posts(
    db: aiosqlite.Connection,
    thread_id: str,
    records: list[dict],
) -> None:
    """Replace all post records for a thread with fresh data.

    Deletes existing records and inserts new ones in one pass.
    Each record: {'character_id': str, 'post_date': str | None}
    """
    await db.execute("DELETE FROM posts WHERE thread_id = ?", (thread_id,))
    for rec in records:
        await db.execute(
            "INSERT INTO posts (character_id, thread_id, post_date) VALUES (?, ?, ?)",
            (rec["character_id"], thread_id, rec.get("post_date")),
        )


async def set_approval_date(
    db: aiosqlite.Connection,
    character_id: str,
    approval_date: str | None,
) -> bool:
    """Set the approval date for a single character. Returns True if found."""
    cursor = await db.execute(
        "UPDATE characters SET approval_date = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (approval_date, character_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def set_approval_dates(
    db: aiosqlite.Connection,
    entries: list[dict],
) -> dict:
    """Bulk-set approval dates by matching character name.

    Each entry: {'name': str, 'approval_date': str (YYYY-MM-DD)}.
    Returns {'matched': int, 'unmatched': list[str]}.
    """
    matched = 0
    unmatched = []
    for entry in entries:
        name = entry["name"].strip()
        date = entry["approval_date"].strip()
        cursor = await db.execute(
            "UPDATE characters SET approval_date = ? WHERE LOWER(name) = LOWER(?)",
            (date, name),
        )
        if cursor.rowcount > 0:
            matched += cursor.rowcount
        else:
            unmatched.append(name)
    await db.commit()
    return {"matched": matched, "unmatched": unmatched}


async def delete_character(db: aiosqlite.Connection, character_id: str) -> dict:
    """Delete a character and all associated data.

    Cascade-deletes from all child tables: profile_fields, character_threads,
    quotes, quote_crawl_log, and posts. Used when a profile crawl detects
    that a character no longer exists on JCink (banned/deleted).

    Returns a summary of what was removed.
    """
    counts = {}
    for table, col in [
        ("profile_fields", "character_id"),
        ("character_threads", "character_id"),
        ("quotes", "character_id"),
        ("quote_crawl_log", "character_id"),
        ("posts", "character_id"),
    ]:
        cursor = await db.execute(
            f"DELETE FROM {table} WHERE {col} = ?", (character_id,)
        )
        counts[table] = cursor.rowcount

    cursor = await db.execute(
        "DELETE FROM characters WHERE id = ?", (character_id,)
    )
    counts["characters"] = cursor.rowcount
    await db.commit()
    return counts


# --- User Activity Operations ---

async def record_user_activity(
    db: aiosqlite.Connection,
    user_id: str,
    user_name: str,
    source: str = "webhook",
) -> None:
    """Record or update a user's last-seen timestamp. Auto-commits."""
    await db.execute(
        """INSERT INTO user_activity (user_id, user_name, last_seen, source)
           VALUES (?, ?, datetime('now'), ?)
           ON CONFLICT(user_id) DO UPDATE SET
               user_name = excluded.user_name,
               last_seen = excluded.last_seen,
               source = excluded.source""",
        (user_id, user_name, source),
    )
    await db.commit()


async def get_recent_users(
    db: aiosqlite.Connection,
    hours: int = 6,
) -> list[dict]:
    """Return users active within the last `hours` hours, most recent first."""
    cursor = await db.execute(
        """SELECT user_id, user_name, last_seen, source
           FROM user_activity
           WHERE last_seen >= datetime('now', ?)
           ORDER BY last_seen DESC""",
        (f"-{hours} hours",),
    )
    rows = await cursor.fetchall()
    excluded = settings.excluded_name_set
    excluded_ids = settings.excluded_id_set
    base = settings.forum_base_url
    return [
        {
            "id": row["user_id"],
            "name": row["user_name"],
            "last_seen": row["last_seen"],
            "profile_url": f"{base}/index.php?showuser={row['user_id']}",
            "source": row["source"],
        }
        for row in rows
        if row["user_name"].lower() not in excluded and row["user_id"] not in excluded_ids
    ]


# --- Relationship Operations ---

_RELATIONSHIP_JOIN_SQL = """
    SELECT r.*,
           ca.name AS character_a_name, ca.avatar_url AS character_a_avatar,
           cb.name AS character_b_name, cb.avatar_url AS character_b_avatar
    FROM relationships r
    JOIN characters ca ON ca.id = r.character_a_id
    JOIN characters cb ON cb.id = r.character_b_id
"""


def _row_to_relationship(row) -> Relationship:
    return Relationship(
        id=row["id"],
        character_a_id=row["character_a_id"],
        character_b_id=row["character_b_id"],
        relationship_type=row["relationship_type"],
        label=row["label"],
        character_a_name=row["character_a_name"],
        character_b_name=row["character_b_name"],
        character_a_avatar=row["character_a_avatar"],
        character_b_avatar=row["character_b_avatar"],
    )


async def get_all_relationships(db: aiosqlite.Connection) -> list[Relationship]:
    """Get all relationships with joined character info."""
    cursor = await db.execute(
        _RELATIONSHIP_JOIN_SQL + " ORDER BY r.created_at DESC"
    )
    return [_row_to_relationship(r) for r in await cursor.fetchall()]


async def get_relationships_for_character(
    db: aiosqlite.Connection, character_id: str
) -> list[Relationship]:
    """Get all relationships involving a specific character."""
    cursor = await db.execute(
        _RELATIONSHIP_JOIN_SQL
        + " WHERE r.character_a_id = ? OR r.character_b_id = ? ORDER BY r.relationship_type",
        (character_id, character_id),
    )
    return [_row_to_relationship(r) for r in await cursor.fetchall()]


async def create_relationship(
    db: aiosqlite.Connection,
    char_a_id: str,
    char_b_id: str,
    rel_type: str = "other",
    label: str | None = None,
) -> int | None:
    """Create a relationship between two characters. Auto-commits."""
    # Normalize order so (A,B) and (B,A) are the same unique pair
    a, b = sorted([char_a_id, char_b_id])
    cursor = await db.execute(
        """INSERT OR IGNORE INTO relationships
           (character_a_id, character_b_id, relationship_type, label)
           VALUES (?, ?, ?, ?)""",
        (a, b, rel_type, label),
    )
    await db.commit()
    return cursor.lastrowid if cursor.rowcount > 0 else None


async def update_relationship(
    db: aiosqlite.Connection,
    relationship_id: int,
    rel_type: str,
    label: str | None = None,
) -> bool:
    """Update a relationship's type and label. Auto-commits."""
    cursor = await db.execute(
        """UPDATE relationships
           SET relationship_type = ?, label = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (rel_type, label, relationship_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def delete_relationship(db: aiosqlite.Connection, relationship_id: int) -> bool:
    """Delete a relationship. Auto-commits."""
    cursor = await db.execute(
        "DELETE FROM relationships WHERE id = ?", (relationship_id,)
    )
    await db.commit()
    return cursor.rowcount > 0


# Mapping from parenthetical hints to relationship types
_HINT_MAP = {
    "twin": "family", "sister": "family", "brother": "family",
    "mother": "family", "father": "family", "parent": "family",
    "daughter": "family", "son": "family", "sibling": "family",
    "cousin": "family", "uncle": "family", "aunt": "family",
    "family": "family", "adopted": "family",
    "partner": "romantic", "wife": "romantic", "husband": "romantic",
    "girlfriend": "romantic", "boyfriend": "romantic", "fiancé": "romantic",
    "fiancee": "romantic", "lover": "romantic", "romantic": "romantic",
    "ex": "romantic",
    "mentor": "mentor", "mentee": "mentor", "student": "mentor",
    "teacher": "mentor", "protégé": "mentor",
    "rival": "enemy", "enemy": "enemy", "nemesis": "enemy",
    "adversary": "enemy", "antagonist": "enemy",
}


def _guess_relationship_type(hint: str) -> str:
    """Map a parenthetical hint to a relationship type."""
    hint_lower = hint.lower().strip()
    for keyword, rel_type in _HINT_MAP.items():
        if keyword in hint_lower:
            return rel_type
    return "ally"


async def seed_relationships_from_connections(db: aiosqlite.Connection) -> int:
    """Parse 'connections' profile fields and create relationships. Auto-commits.

    Returns the number of new relationships created.
    """
    # Get all connections fields
    cursor = await db.execute(
        "SELECT character_id, field_value FROM profile_fields "
        "WHERE field_key = 'connections' AND field_value IS NOT NULL AND field_value != ''"
    )
    conn_rows = await cursor.fetchall()

    # Build name→id lookup (case-insensitive)
    cursor = await db.execute("SELECT id, name FROM characters")
    char_rows = await cursor.fetchall()
    name_to_id: dict[str, str] = {row["name"].lower(): row["id"] for row in char_rows}

    created = 0
    for row in conn_rows:
        source_id = row["character_id"]
        entries = [e.strip() for e in row["field_value"].split(",")]
        for entry in entries:
            if not entry:
                continue
            # Parse "Name (hint)" pattern
            match = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", entry)
            if match:
                name, hint = match.group(1).strip(), match.group(2).strip()
                rel_type = _guess_relationship_type(hint)
                label = hint
            else:
                name = entry.strip()
                rel_type = "ally"
                label = None

            target_id = name_to_id.get(name.lower())
            if not target_id or target_id == source_id:
                continue

            a, b = sorted([source_id, target_id])
            cur = await db.execute(
                """INSERT OR IGNORE INTO relationships
                   (character_a_id, character_b_id, relationship_type, label)
                   VALUES (?, ?, ?, ?)""",
                (a, b, rel_type, label),
            )
            if cur.rowcount > 0:
                created += 1

    await db.commit()
    return created
