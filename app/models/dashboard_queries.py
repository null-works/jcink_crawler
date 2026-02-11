import aiosqlite
from app.config import settings

ALLOWED_CHAR_SORTS = {"name", "id", "affiliation", "player", "total_threads", "last_thread_crawl"}
ALLOWED_THREAD_SORTS = {"title", "category", "last_poster_name", "forum_name", "char_name", "is_user_last_poster"}
ALLOWED_QUOTE_SORTS = {"created_at", "quote_text"}
ALLOWED_PLAYER_SORTS = {"player", "character_count", "total_threads", "awaiting_threads", "last_active"}


async def search_characters(
    db: aiosqlite.Connection,
    query: str | None = None,
    affiliations: list[str] | None = None,
    group_name: str | None = None,
    player_name: str | None = None,
    sort_by: str = "name",
    sort_dir: str = "asc",
    page: int = 1,
    per_page: int = 25,
) -> tuple[list[dict], int]:
    """Search/filter characters with pagination. Returns (rows, total_count)."""
    excluded = settings.excluded_name_set

    base = """
        FROM characters c
        LEFT JOIN profile_fields pf
          ON pf.character_id = c.id AND pf.field_key = ?
        LEFT JOIN profile_fields pf_player
          ON pf_player.character_id = c.id AND pf_player.field_key = ?
    """
    params: list = [settings.affiliation_field_key, settings.player_field_key]
    wheres: list[str] = []

    if query:
        wheres.append("c.name LIKE ?")
        params.append(f"%{query}%")

    if affiliations:
        placeholders = ",".join("?" for _ in affiliations)
        wheres.append(f"pf.field_value IN ({placeholders})")
        params.extend(affiliations)

    if group_name:
        wheres.append("c.group_name = ?")
        params.append(group_name)

    if player_name:
        wheres.append("pf_player.field_value = ?")
        params.append(player_name)

    where_clause = (" WHERE " + " AND ".join(wheres)) if wheres else ""

    # Count total
    count_sql = f"SELECT COUNT(*) as cnt {base}{where_clause}"
    cursor = await db.execute(count_sql, params)
    row = await cursor.fetchone()
    total = row["cnt"] if row else 0

    # Sort
    if sort_by not in ALLOWED_CHAR_SORTS:
        sort_by = "name"
    direction = "DESC" if sort_dir.lower() == "desc" else "ASC"

    if sort_by == "affiliation":
        order = f"pf.field_value {direction}"
    elif sort_by == "player":
        order = f"pf_player.field_value {direction}"
    elif sort_by == "total_threads":
        order = f"(SELECT COUNT(*) FROM character_threads ct WHERE ct.character_id = c.id) {direction}"
    else:
        order = f"c.{sort_by} {direction}"

    offset = (max(page, 1) - 1) * per_page
    select_sql = f"""
        SELECT c.*, pf.field_value AS affiliation, pf_player.field_value AS player
        {base}{where_clause}
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """
    params.extend([per_page, offset])
    cursor = await db.execute(select_sql, params)
    rows = await cursor.fetchall()

    results = []
    for r in rows:
        d = dict(r)
        if d["name"].lower() in excluded:
            continue
        # Get thread counts inline
        tc = await db.execute(
            "SELECT category, COUNT(*) as count FROM character_threads WHERE character_id = ? GROUP BY category",
            (d["id"],),
        )
        tc_rows = await tc.fetchall()
        counts = {tr["category"]: tr["count"] for tr in tc_rows}
        counts["total"] = sum(counts.values())
        d["thread_counts"] = counts
        results.append(d)

    return results, total


async def search_threads_global(
    db: aiosqlite.Connection,
    query: str | None = None,
    category: str | None = None,
    status: str | None = None,
    character_id: str | None = None,
    player_name: str | None = None,
    sort_by: str = "title",
    sort_dir: str = "asc",
    page: int = 1,
    per_page: int = 25,
) -> tuple[list[dict], int]:
    """Search threads globally with filters. Returns (rows, total_count)."""
    base = """
        FROM threads t
        JOIN character_threads ct ON t.id = ct.thread_id
        JOIN characters c ON c.id = ct.character_id
    """
    params: list = []
    wheres: list[str] = []

    if player_name:
        base += " JOIN profile_fields pf_player ON pf_player.character_id = c.id AND pf_player.field_key = ?"
        params.append(settings.player_field_key)
        wheres.append("pf_player.field_value = ?")
        params.append(player_name)

    if query:
        wheres.append("t.title LIKE ?")
        params.append(f"%{query}%")

    if category:
        wheres.append("ct.category = ?")
        params.append(category)

    if status == "awaiting":
        wheres.append("ct.is_user_last_poster = 0")
    elif status == "replied":
        wheres.append("ct.is_user_last_poster = 1")

    if character_id:
        wheres.append("ct.character_id = ?")
        params.append(character_id)

    where_clause = (" WHERE " + " AND ".join(wheres)) if wheres else ""

    count_sql = f"SELECT COUNT(*) as cnt {base}{where_clause}"
    cursor = await db.execute(count_sql, params)
    row = await cursor.fetchone()
    total = row["cnt"] if row else 0

    if sort_by not in ALLOWED_THREAD_SORTS:
        sort_by = "title"
    direction = "DESC" if sort_dir.lower() == "desc" else "ASC"
    sort_map = {
        "category": f"ct.category {direction}",
        "char_name": f"c.name {direction}",
        "is_user_last_poster": f"ct.is_user_last_poster {direction}",
        "last_poster_name": f"t.last_poster_name {direction}",
    }
    order = sort_map.get(sort_by, f"t.{sort_by} {direction}")

    offset = (max(page, 1) - 1) * per_page
    select_sql = f"""
        SELECT t.id, t.title, t.url, t.forum_id, t.forum_name,
               t.last_poster_id, t.last_poster_name, t.last_poster_avatar,
               ct.category AS char_category, ct.is_user_last_poster,
               c.id AS char_id, c.name AS char_name
        {base}{where_clause}
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """
    cursor = await db.execute(select_sql, params + [per_page, offset])
    rows = await cursor.fetchall()
    return [dict(r) for r in rows], total


async def search_quotes_global(
    db: aiosqlite.Connection,
    query: str | None = None,
    character_id: str | None = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    page: int = 1,
    per_page: int = 25,
) -> tuple[list[dict], int]:
    """Search quotes globally. Returns (rows, total_count)."""
    base = """
        FROM quotes q
        JOIN characters c ON c.id = q.character_id
    """
    params: list = []
    wheres: list[str] = []

    if query:
        wheres.append("q.quote_text LIKE ?")
        params.append(f"%{query}%")

    if character_id:
        wheres.append("q.character_id = ?")
        params.append(character_id)

    where_clause = (" WHERE " + " AND ".join(wheres)) if wheres else ""

    count_sql = f"SELECT COUNT(*) as cnt {base}{where_clause}"
    cursor = await db.execute(count_sql, params)
    row = await cursor.fetchone()
    total = row["cnt"] if row else 0

    if sort_by not in ALLOWED_QUOTE_SORTS:
        sort_by = "created_at"
    direction = "DESC" if sort_dir.lower() == "desc" else "ASC"

    offset = (max(page, 1) - 1) * per_page
    select_sql = f"""
        SELECT q.*, c.name AS character_name, c.avatar_url AS character_avatar
        {base}{where_clause}
        ORDER BY q.{sort_by} {direction}
        LIMIT ? OFFSET ?
    """
    cursor = await db.execute(select_sql, params + [per_page, offset])
    rows = await cursor.fetchall()
    return [dict(r) for r in rows], total


async def get_unique_affiliations(db: aiosqlite.Connection) -> list[str]:
    """Get all distinct affiliation values."""
    cursor = await db.execute(
        "SELECT DISTINCT field_value FROM profile_fields WHERE field_key = ? AND field_value IS NOT NULL AND field_value != '' ORDER BY field_value",
        (settings.affiliation_field_key,),
    )
    rows = await cursor.fetchall()
    return [r["field_value"] for r in rows]


async def get_unique_groups(db: aiosqlite.Connection) -> list[str]:
    """Get all distinct group names."""
    cursor = await db.execute(
        "SELECT DISTINCT group_name FROM characters WHERE group_name IS NOT NULL AND group_name != '' ORDER BY group_name"
    )
    rows = await cursor.fetchall()
    return [r["group_name"] for r in rows]


async def get_unique_players(db: aiosqlite.Connection) -> list[str]:
    """Get all distinct player names."""
    cursor = await db.execute(
        "SELECT DISTINCT field_value FROM profile_fields WHERE field_key = ? AND field_value IS NOT NULL AND field_value != '' ORDER BY field_value",
        (settings.player_field_key,),
    )
    rows = await cursor.fetchall()
    return [r["field_value"] for r in rows]


async def search_players(
    db: aiosqlite.Connection,
    query: str | None = None,
    sort_by: str = "player",
    sort_dir: str = "asc",
    page: int = 1,
    per_page: int = 25,
) -> tuple[list[dict], int]:
    """Get players with character counts, thread counts, and activity. Returns (rows, total_count)."""
    excluded = settings.excluded_name_set

    base = """
        FROM profile_fields pf_player
        JOIN characters c ON c.id = pf_player.character_id
        WHERE pf_player.field_key = ?
          AND pf_player.field_value IS NOT NULL
          AND pf_player.field_value != ''
    """
    params: list = [settings.player_field_key]

    if query:
        base += " AND pf_player.field_value LIKE ?"
        params.append(f"%{query}%")

    # Count distinct players
    count_sql = f"SELECT COUNT(DISTINCT pf_player.field_value) as cnt {base}"
    cursor = await db.execute(count_sql, params)
    row = await cursor.fetchone()
    total = row["cnt"] if row else 0

    # Sort
    if sort_by not in ALLOWED_PLAYER_SORTS:
        sort_by = "player"
    direction = "DESC" if sort_dir.lower() == "desc" else "ASC"

    sort_map = {
        "player": f"player_name {direction}",
        "character_count": f"character_count {direction}",
        "total_threads": f"total_threads {direction}",
        "awaiting_threads": f"awaiting_threads {direction}",
        "last_active": f"last_active {direction}",
    }
    order = sort_map.get(sort_by, f"player_name {direction}")

    offset = (max(page, 1) - 1) * per_page
    select_sql = f"""
        SELECT
            pf_player.field_value AS player_name,
            COUNT(DISTINCT c.id) AS character_count,
            (SELECT COUNT(*) FROM character_threads ct2
             JOIN profile_fields pf2 ON pf2.character_id = ct2.character_id AND pf2.field_key = ?
             WHERE pf2.field_value = pf_player.field_value) AS total_threads,
            (SELECT COUNT(*) FROM character_threads ct3
             JOIN profile_fields pf3 ON pf3.character_id = ct3.character_id AND pf3.field_key = ?
             WHERE pf3.field_value = pf_player.field_value
               AND ct3.category = 'ongoing' AND ct3.is_user_last_poster = 0) AS awaiting_threads,
            MAX(c.last_thread_crawl) AS last_active
        {base}
        GROUP BY pf_player.field_value
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """
    all_params = [settings.player_field_key, settings.player_field_key] + params + [per_page, offset]
    cursor = await db.execute(select_sql, all_params)
    rows = await cursor.fetchall()

    results = []
    for r in rows:
        d = dict(r)
        # Skip if all characters for this player are excluded
        chars_cursor = await db.execute(
            """SELECT c.id, c.name, c.avatar_url, c.group_name
               FROM characters c
               JOIN profile_fields pf ON pf.character_id = c.id AND pf.field_key = ?
               WHERE pf.field_value = ?""",
            (settings.player_field_key, d["player_name"]),
        )
        chars = await chars_cursor.fetchall()
        char_list = [dict(ch) for ch in chars if ch["name"].lower() not in excluded]
        if not char_list:
            continue
        d["characters"] = char_list
        results.append(d)

    return results, total


async def get_player_detail(
    db: aiosqlite.Connection,
    player_name: str,
) -> dict | None:
    """Get full player detail: characters, thread breakdown, quotes count."""
    excluded = settings.excluded_name_set

    # Get all characters for this player
    cursor = await db.execute(
        """SELECT c.*, pf_aff.field_value AS affiliation
           FROM characters c
           JOIN profile_fields pf ON pf.character_id = c.id AND pf.field_key = ?
           LEFT JOIN profile_fields pf_aff ON pf_aff.character_id = c.id AND pf_aff.field_key = ?
           WHERE pf.field_value = ?""",
        (settings.player_field_key, settings.affiliation_field_key, player_name),
    )
    rows = await cursor.fetchall()
    characters = [dict(r) for r in rows if r["name"].lower() not in excluded]

    if not characters:
        return None

    # Gather thread counts and awaiting threads per character
    total_threads = 0
    total_awaiting = 0
    total_quotes = 0
    for char in characters:
        tc = await db.execute(
            "SELECT category, COUNT(*) as count FROM character_threads WHERE character_id = ? GROUP BY category",
            (char["id"],),
        )
        tc_rows = await tc.fetchall()
        counts = {tr["category"]: tr["count"] for tr in tc_rows}
        counts["total"] = sum(counts.values())
        char["thread_counts"] = counts
        total_threads += counts["total"]

        aw = await db.execute(
            "SELECT COUNT(*) as cnt FROM character_threads WHERE character_id = ? AND category = 'ongoing' AND is_user_last_poster = 0",
            (char["id"],),
        )
        aw_row = await aw.fetchone()
        char["awaiting"] = aw_row["cnt"] if aw_row else 0
        total_awaiting += char["awaiting"]

        qc = await db.execute(
            "SELECT COUNT(*) as cnt FROM quotes WHERE character_id = ?",
            (char["id"],),
        )
        qc_row = await qc.fetchone()
        char["quote_count"] = qc_row["cnt"] if qc_row else 0
        total_quotes += char["quote_count"]

    # Get awaiting threads across all characters for this player
    char_ids = [c["id"] for c in characters]
    placeholders = ",".join("?" for _ in char_ids)
    awaiting_cursor = await db.execute(
        f"""SELECT t.id, t.title, t.url, t.last_poster_name, t.last_poster_avatar,
                   ct.category, c.id AS char_id, c.name AS char_name
            FROM character_threads ct
            JOIN threads t ON t.id = ct.thread_id
            JOIN characters c ON c.id = ct.character_id
            WHERE ct.character_id IN ({placeholders})
              AND ct.category = 'ongoing'
              AND ct.is_user_last_poster = 0
            ORDER BY c.name, t.title""",
        char_ids,
    )
    awaiting_threads = [dict(r) for r in await awaiting_cursor.fetchall()]

    return {
        "player_name": player_name,
        "characters": characters,
        "total_threads": total_threads,
        "total_awaiting": total_awaiting,
        "total_quotes": total_quotes,
        "awaiting_threads": awaiting_threads,
    }


async def get_dashboard_stats(db: aiosqlite.Connection) -> dict:
    """Get aggregate stats for the dashboard."""
    excluded = settings.excluded_name_set

    cursor = await db.execute("SELECT COUNT(*) as cnt FROM characters")
    total_chars = (await cursor.fetchone())["cnt"]
    # Subtract excluded
    if excluded:
        placeholders = ",".join("?" for _ in excluded)
        cursor = await db.execute(
            f"SELECT COUNT(*) as cnt FROM characters WHERE LOWER(name) IN ({placeholders})",
            list(excluded),
        )
        excluded_count = (await cursor.fetchone())["cnt"]
        total_chars -= excluded_count

    cursor = await db.execute("SELECT COUNT(*) as cnt FROM threads")
    total_threads = (await cursor.fetchone())["cnt"]

    cursor = await db.execute("SELECT COUNT(*) as cnt FROM quotes")
    total_quotes = (await cursor.fetchone())["cnt"]

    cursor = await db.execute(
        "SELECT COUNT(*) as cnt FROM character_threads WHERE category = 'ongoing' AND is_user_last_poster = 0"
    )
    threads_awaiting = (await cursor.fetchone())["cnt"]

    cursor = await db.execute(
        "SELECT MAX(last_thread_crawl) as last_crawl FROM characters"
    )
    row = await cursor.fetchone()
    last_crawl = row["last_crawl"] if row else None

    cursor = await db.execute(
        "SELECT COUNT(DISTINCT field_value) as cnt FROM profile_fields WHERE field_key = ? AND field_value IS NOT NULL AND field_value != ''",
        (settings.player_field_key,),
    )
    total_players = (await cursor.fetchone())["cnt"]

    return {
        "characters_tracked": total_chars,
        "total_threads": total_threads,
        "total_quotes": total_quotes,
        "threads_awaiting": threads_awaiting,
        "total_players": total_players,
        "last_crawl": last_crawl,
    }
