import aiosqlite
from app.config import settings
from app.models.character import (
    CharacterSummary,
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
    """Get all tracked characters."""
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
