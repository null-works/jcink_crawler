import aiosqlite
from app.config import settings
from app.models.character import (
    CharacterSummary,
    ClaimsSummary,
    ThreadInfo,
    ThreadCategory,
    CharacterThreads,
    Quote,
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
    """Get all tracked characters, excluding filtered names."""
    excluded = settings.excluded_name_set
    cursor = await db.execute(
        """SELECT c.*, pf.field_value AS affiliation
           FROM characters c
           LEFT JOIN profile_fields pf
             ON pf.character_id = c.id AND pf.field_key = ?
           ORDER BY c.name""",
        (settings.affiliation_field_key,),
    )
    rows = await cursor.fetchall()
    results = []
    for row in rows:
        char = dict(row)
        if char["name"].lower() in excluded:
            continue
        counts = await get_thread_counts(db, char["id"])
        results.append(CharacterSummary(
            id=char["id"],
            name=char["name"],
            profile_url=char["profile_url"],
            group_name=char.get("group_name"),
            avatar_url=char.get("avatar_url"),
            affiliation=char.get("affiliation"),
            thread_counts=counts,
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
                           last_poster_id, last_poster_name, last_poster_avatar, last_crawled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            url = excluded.url,
            forum_id = excluded.forum_id,
            forum_name = excluded.forum_name,
            category = excluded.category,
            last_poster_id = excluded.last_poster_id,
            last_poster_name = excluded.last_poster_name,
            last_poster_avatar = excluded.last_poster_avatar,
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
) -> None:
    """Link a character to a thread."""
    await db.execute("""
        INSERT INTO character_threads (character_id, thread_id, category, is_user_last_poster)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(character_id, thread_id) DO UPDATE SET
            category = excluded.category,
            is_user_last_poster = excluded.is_user_last_poster
    """, (character_id, thread_id, category, int(is_user_last_poster)))


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
        SELECT t.*, ct.category as char_category, ct.is_user_last_poster
        FROM threads t
        JOIN character_threads ct ON t.id = ct.thread_id
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
        info = ThreadInfo(
            id=r["id"],
            title=r["title"],
            url=r["url"],
            forum_id=r.get("forum_id"),
            forum_name=r.get("forum_name"),
            category=r["char_category"],
            last_poster_id=r.get("last_poster_id"),
            last_poster_name=r.get("last_poster_name"),
            last_poster_avatar=r.get("last_poster_avatar"),
            is_user_last_poster=bool(r.get("is_user_last_poster", 0)),
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
    except Exception:
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
    "affiliation",
    "connections",
)


async def get_all_claims(db: aiosqlite.Connection) -> list[ClaimsSummary]:
    """Get all characters with claims-specific profile fields and thread counts.

    Single bulk query approach: fetch all characters, then batch-load their
    profile fields and thread counts to avoid N+1 queries.
    """
    excluded = settings.excluded_name_set

    # 1. All characters
    cursor = await db.execute(
        "SELECT id, name, profile_url, group_name, avatar_url FROM characters ORDER BY name"
    )
    char_rows = await cursor.fetchall()

    if not char_rows:
        return []

    char_ids = [row["id"] for row in char_rows if row["name"].lower() not in excluded]
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
        if char["name"].lower() in excluded:
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
            alias=fields.get("alias"),
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
