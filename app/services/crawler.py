import asyncio
import aiosqlite
from app.config import settings
from app.services.activity import set_activity, clear_activity
from app.services.fetcher import fetch_page, fetch_page_with_delay, fetch_pages_concurrent
from app.services.parser import (
    parse_search_results,
    parse_search_redirect,
    parse_last_poster,
    parse_thread_pagination,
    parse_profile_page,
    parse_avatar_from_profile,
    extract_quotes_from_html,
    extract_thread_authors,
    extract_post_records,
    parse_member_list,
    parse_member_list_pagination,
    is_board_message,
)
from app.models.operations import (
    upsert_character,
    update_character_crawl_time,
    upsert_thread,
    link_character_thread,
    add_quote,
    mark_thread_quote_scraped,
    replace_thread_posts,
    upsert_profile_field,
    get_character,
)


async def crawl_character_threads(character_id: str, db_path: str) -> dict:
    """Crawl all threads for a character.

    Follows the same logic as the client-side tracker:
    1. Hit JCink search for all posts by user
    2. Follow redirects if needed
    3. Paginate through all results
    4. Categorize threads by forum
    5. Fetch each thread to determine last poster
    6. Extract quotes along the way

    Uses concurrent fetching for throughput while respecting rate limits
    via the shared semaphore in fetcher.py.

    Args:
        character_id: JCink user ID
        db_path: Path to SQLite database

    Returns:
        Summary dict with counts
    """
    base_url = settings.forum_base_url
    search_url = f"{base_url}/index.php?act=Search&CODE=getalluser&mid={character_id}&type=posts"

    print(f"[Crawler] Starting thread crawl for character {character_id}")
    set_activity(f"Crawling threads", character_id=character_id)

    # Step 1: Hit search, handle redirect
    html = await fetch_page(search_url)
    if not html:
        print(f"[Crawler] Failed to fetch search page for {character_id}")
        return {"error": "Failed to fetch search page"}

    # Check for redirect
    redirect_url = parse_search_redirect(html)
    if redirect_url:
        print(f"[Crawler] Following redirect to {redirect_url}")
        await asyncio.sleep(settings.request_delay_seconds)
        html = await fetch_page(redirect_url)
        if not html:
            return {"error": "Failed to follow search redirect"}

    if is_board_message(html):
        print(f"[Crawler] Got board message (cooldown), will retry later")
        return {"error": "Search cooldown, will retry"}

    # Step 2: Parse first page of results
    all_threads, page_urls = parse_search_results(html)
    print(f"[Crawler] First page: {len(all_threads)} threads, {len(page_urls)} additional pages")

    # Step 3: Fetch additional search pages concurrently
    if page_urls:
        page_htmls = await fetch_pages_concurrent(page_urls)
        seen_ids = {t.thread_id for t in all_threads}
        for page_html in page_htmls:
            if not page_html:
                continue
            if is_board_message(page_html):
                break
            page_threads, _ = parse_search_results(page_html)
            for t in page_threads:
                if t.thread_id not in seen_ids:
                    all_threads.append(t)
                    seen_ids.add(t.thread_id)

    print(f"[Crawler] Total threads found: {len(all_threads)}")

    # Pre-load character info and quote scrape status in bulk
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        char = await get_character(db, character_id)

        # Load ALL known characters so we can opportunistically extract
        # quotes for other characters from pages we already fetch
        cursor = await db.execute("SELECT id, name FROM characters")
        rows = await cursor.fetchall()
        all_characters = {row["id"]: row["name"] for row in rows}

        # Bulk query: which (thread, character) pairs are already quote-scraped?
        scraped_pairs: set[tuple[str, str]] = set()
        if all_threads:
            thread_ids = [t.thread_id for t in all_threads]
            placeholders = ",".join("?" * len(thread_ids))
            cursor = await db.execute(
                f"SELECT thread_id, character_id FROM quote_crawl_log WHERE thread_id IN ({placeholders})",
                thread_ids,
            )
            rows = await cursor.fetchall()
            scraped_pairs = {(row["thread_id"], row["character_id"]) for row in rows}

    character_name = char.name if char else None
    thread_count = len(all_threads)
    if character_name:
        set_activity(
            f"Crawling threads for {character_name} — {thread_count} threads found",
            character_id=character_id,
            character_name=character_name,
        )

    # Avatar cache: avoid re-fetching the same user's profile for their avatar
    avatar_cache: dict[str, str | None] = {}

    async def _process_thread(thread):
        """Fetch and parse all data for a single thread.

        Returns a result dict or None if the fetch failed.
        HTTP concurrency is controlled by the semaphore in fetch_page_with_delay.
        """
        thread_html = await fetch_page_with_delay(thread.url)
        if not thread_html:
            return None

        # Check for multi-page threads — get last page
        max_st = parse_thread_pagination(thread_html)
        last_page_html = None
        if max_st > 0:
            sep = "&" if "?" in thread.url else "?"
            last_page_url = f"{thread.url}{sep}st={max_st}"
            last_page_html = await fetch_page_with_delay(last_page_url)

        thread_html_for_poster = last_page_html or thread_html

        # Extract last poster
        last_poster = parse_last_poster(thread_html_for_poster)
        last_poster_name = last_poster.name if last_poster else None
        last_poster_id = last_poster.user_id if last_poster else None
        is_user_last = (
            last_poster_name.lower() == character_name.lower()
            if last_poster_name and character_name
            else False
        )

        # Fetch last poster avatar (with cache to avoid duplicates)
        last_poster_avatar = None
        if last_poster_id:
            if last_poster_id in avatar_cache:
                last_poster_avatar = avatar_cache[last_poster_id]
            else:
                avatar_html = await fetch_page_with_delay(
                    f"{base_url}/index.php?showuser={last_poster_id}"
                )
                if avatar_html:
                    last_poster_avatar = parse_avatar_from_profile(avatar_html)
                avatar_cache[last_poster_id] = last_poster_avatar

        # Extract quotes — opportunistically for ALL known characters, not just
        # the current one. Since we already have the HTML, extracting for others
        # is essentially free and saves re-fetching these pages later.
        # quotes_by_character: {character_id: [{"text": ...}, ...]}
        quotes_by_character: dict[str, list[dict]] = {}
        characters_to_mark_scraped: list[str] = []

        # Which characters still need this thread scraped?
        chars_needing_scrape = {
            cid: cname for cid, cname in all_characters.items()
            if (thread.thread_id, cid) not in scraped_pairs
        }

        # Collect all available pages (reuse already-fetched HTML)
        all_pages = [thread_html]
        if max_st > 0:
            remaining_urls = []
            for st in range(25, max_st + 1, 25):
                if st == max_st and last_page_html:
                    all_pages.append(last_page_html)
                else:
                    sep = "&" if "?" in thread.url else "?"
                    remaining_urls.append(f"{thread.url}{sep}st={st}")

            if remaining_urls:
                intermediate_htmls = await fetch_pages_concurrent(remaining_urls)
                for ph in intermediate_htmls:
                    if ph:
                        all_pages.append(ph)

        # Extract authors and post records from all pages
        thread_author_ids: set[str] = set()
        all_post_records: list[dict] = []
        for page_html in all_pages:
            thread_author_ids.update(extract_thread_authors(page_html))
            all_post_records.extend(extract_post_records(page_html))

        # Count posts per character for this thread
        post_counts_by_char: dict[str, int] = {}
        for rec in all_post_records:
            cid = rec["character_id"]
            post_counts_by_char[cid] = post_counts_by_char.get(cid, 0) + 1

        # Extract quotes for characters that need this thread scraped
        if chars_needing_scrape:
            for cid, cname in chars_needing_scrape.items():
                char_quotes = []
                for page_html in all_pages:
                    page_quotes = extract_quotes_from_html(page_html, cname)
                    char_quotes.extend(page_quotes)
                quotes_by_character[cid] = char_quotes
                characters_to_mark_scraped.append(cid)

        return {
            "thread": thread,
            "last_poster_id": last_poster_id,
            "last_poster_name": last_poster_name,
            "last_poster_avatar": last_poster_avatar,
            "is_user_last": is_user_last,
            "quotes_by_character": quotes_by_character,
            "characters_to_mark_scraped": characters_to_mark_scraped,
            "thread_author_ids": thread_author_ids,
            "post_records": all_post_records,
            "post_counts_by_char": post_counts_by_char,
        }

    # Step 4: Process all threads concurrently
    tasks = [_process_thread(t) for t in all_threads]
    thread_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Step 5: Batch write all results to DB in a single connection
    results = {"ongoing": 0, "comms": 0, "complete": 0, "incomplete": 0, "quotes_added": 0}

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        for result in thread_results:
            if isinstance(result, Exception):
                print(f"[Crawler] Error processing thread: {result}")
                continue
            if result is None:
                continue

            thread = result["thread"]

            await upsert_thread(
                db,
                thread_id=thread.thread_id,
                title=thread.title,
                url=thread.url,
                forum_id=thread.forum_id,
                forum_name=thread.forum_name,
                category=thread.category,
                last_poster_id=result["last_poster_id"],
                last_poster_name=result["last_poster_name"],
                last_poster_avatar=result["last_poster_avatar"],
            )
            post_counts = result.get("post_counts_by_char", {})
            await link_character_thread(
                db,
                character_id=character_id,
                thread_id=thread.thread_id,
                category=thread.category,
                is_user_last_poster=result["is_user_last"],
                post_count=post_counts.get(character_id, 0),
            )

            # Opportunistically link this thread to other known characters
            # who posted in it — saves them needing a full search crawl
            for author_id in result.get("thread_author_ids", set()):
                if author_id != character_id and author_id in all_characters:
                    is_author_last = (
                        result["last_poster_id"] == author_id
                        if result["last_poster_id"]
                        else False
                    )
                    await link_character_thread(
                        db,
                        character_id=author_id,
                        thread_id=thread.thread_id,
                        category=thread.category,
                        is_user_last_poster=is_author_last,
                        post_count=post_counts.get(author_id, 0),
                    )

            # Store individual post records (for date-based activity queries)
            post_records = result.get("post_records", [])
            if post_records:
                await replace_thread_posts(db, thread.thread_id, post_records)

            results[thread.category] = results.get(thread.category, 0) + 1

            # Save quotes for ALL characters extracted from this thread
            for cid, char_quotes in result["quotes_by_character"].items():
                for q in char_quotes:
                    added = await add_quote(
                        db, cid, q["text"],
                        thread.thread_id, thread.title
                    )
                    # Only count quotes for the character we're crawling
                    if added and cid == character_id:
                        results["quotes_added"] += 1

            for cid in result["characters_to_mark_scraped"]:
                await mark_thread_quote_scraped(db, thread.thread_id, cid)

        await db.commit()

    # Update crawl timestamp
    async with aiosqlite.connect(db_path) as db:
        await update_character_crawl_time(db, character_id, "threads")

    clear_activity()
    print(f"[Crawler] Thread crawl complete for {character_id}: {results}")
    return results


async def crawl_single_thread(
    thread_id: str,
    db_path: str,
    user_id: str | None = None,
    forum_id: str | None = None,
) -> dict:
    """Crawl a single thread by ID — lightweight targeted re-crawl.

    Used by the webhook endpoint when the theme reports a new post/topic.
    Much faster than crawl_character_threads() since it skips the JCink
    search entirely and fetches only the one thread.

    Args:
        thread_id: JCink topic ID
        db_path: Path to SQLite database
        user_id: Optional user ID to link the thread to
        forum_id: Optional forum ID for categorization

    Returns:
        Summary dict
    """
    from app.services.parser import categorize_thread

    base_url = settings.forum_base_url
    thread_url = f"{base_url}/index.php?showtopic={thread_id}"

    print(f"[Crawler] Targeted crawl for thread {thread_id}")
    set_activity(f"Targeted crawl: thread {thread_id}", character_id=user_id)

    # Fetch thread first page
    thread_html = await fetch_page_with_delay(thread_url)
    if not thread_html:
        clear_activity()
        return {"error": "Failed to fetch thread"}

    if is_board_message(thread_html):
        clear_activity()
        return {"error": "Board message (cooldown)"}

    # Get last page for last poster
    max_st = parse_thread_pagination(thread_html)
    last_page_html = None
    if max_st > 0:
        last_page_url = f"{thread_url}&st={max_st}"
        last_page_html = await fetch_page_with_delay(last_page_url)

    poster_html = last_page_html or thread_html

    # Extract last poster
    last_poster = parse_last_poster(poster_html)
    last_poster_name = last_poster.name if last_poster else None
    last_poster_id = last_poster.user_id if last_poster else None

    # Fetch last poster avatar
    last_poster_avatar = None
    if last_poster_id:
        avatar_html = await fetch_page_with_delay(
            f"{base_url}/index.php?showuser={last_poster_id}"
        )
        if avatar_html:
            last_poster_avatar = parse_avatar_from_profile(avatar_html)

    # Extract thread title from the page
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(thread_html, "html.parser")
    title_el = soup.select_one("title")
    title = "Unknown Thread"
    if title_el:
        raw_title = title_el.get_text(strip=True)
        # JCink titles are often "Board Name -> Thread Title"
        if "->" in raw_title:
            title = raw_title.split("->")[-1].strip()
        else:
            title = raw_title

    # Determine forum_id from the page if not provided
    if not forum_id:
        forum_link = soup.select_one('a[href*="showforum="]')
        if forum_link:
            import re
            f_match = re.search(r"showforum=(\d+)", forum_link.get("href", ""))
            if f_match:
                forum_id = f_match.group(1)

    category = categorize_thread(forum_id)

    # Get forum name from page
    forum_name = None
    forum_link = soup.select_one('a[href*="showforum="]')
    if forum_link:
        forum_name = forum_link.get_text(strip=True)

    # Load known characters for quote extraction
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id, name FROM characters")
        rows = await cursor.fetchall()
        all_characters = {row["id"]: row["name"] for row in rows}

        # Check which characters still need quote scraping for this thread
        cursor = await db.execute(
            "SELECT character_id FROM quote_crawl_log WHERE thread_id = ?",
            (thread_id,),
        )
        already_scraped = {row["character_id"] for row in await cursor.fetchall()}

    # Collect all thread pages for quote extraction
    all_pages = [thread_html]
    if max_st > 0:
        remaining_urls = []
        for st in range(25, max_st + 1, 25):
            if st == max_st and last_page_html:
                all_pages.append(last_page_html)
            else:
                remaining_urls.append(f"{thread_url}&st={st}")
        if remaining_urls:
            intermediate_htmls = await fetch_pages_concurrent(remaining_urls)
            all_pages.extend(h for h in intermediate_htmls if h)

    # Extract authors and post records from all pages
    thread_author_ids: set[str] = set()
    all_post_records: list[dict] = []
    for page_html in all_pages:
        thread_author_ids.update(extract_thread_authors(page_html))
        all_post_records.extend(extract_post_records(page_html))

    # Count posts per character for this thread
    post_counts_by_char: dict[str, int] = {}
    for rec in all_post_records:
        cid = rec["character_id"]
        post_counts_by_char[cid] = post_counts_by_char.get(cid, 0) + 1

    # Extract quotes for characters who need this thread scraped
    quotes_by_character: dict[str, list[dict]] = {}
    chars_to_mark: list[str] = []
    for cid, cname in all_characters.items():
        if cid not in already_scraped:
            char_quotes = []
            for page_html in all_pages:
                char_quotes.extend(extract_quotes_from_html(page_html, cname))
            quotes_by_character[cid] = char_quotes
            chars_to_mark.append(cid)

    # Determine if user is last poster
    character_name = all_characters.get(user_id) if user_id else None
    is_user_last = (
        last_poster_name and character_name
        and last_poster_name.lower() == character_name.lower()
    )

    # Write everything to DB
    quotes_added = 0
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        await upsert_thread(
            db,
            thread_id=thread_id,
            title=title,
            url=thread_url,
            forum_id=forum_id,
            forum_name=forum_name,
            category=category,
            last_poster_id=last_poster_id,
            last_poster_name=last_poster_name,
            last_poster_avatar=last_poster_avatar,
        )

        # Link to requesting user
        if user_id:
            await link_character_thread(
                db,
                character_id=user_id,
                thread_id=thread_id,
                category=category,
                is_user_last_poster=bool(is_user_last),
                post_count=post_counts_by_char.get(user_id, 0),
            )

        # Also link other known authors
        for author_id in thread_author_ids:
            if author_id in all_characters and author_id != user_id:
                is_author_last = last_poster_id == author_id if last_poster_id else False
                await link_character_thread(
                    db,
                    character_id=author_id,
                    thread_id=thread_id,
                    category=category,
                    is_user_last_poster=is_author_last,
                    post_count=post_counts_by_char.get(author_id, 0),
                )

        # Store post records for date-based activity queries
        if all_post_records:
            await replace_thread_posts(db, thread_id, all_post_records)

        # Save quotes
        for cid, char_quotes in quotes_by_character.items():
            for q in char_quotes:
                added = await add_quote(db, cid, q["text"], thread_id, title)
                if added:
                    quotes_added += 1
        for cid in chars_to_mark:
            await mark_thread_quote_scraped(db, thread_id, cid)

        await db.commit()

    clear_activity()
    print(f"[Crawler] Targeted crawl complete: thread {thread_id}, {quotes_added} quotes added")
    return {
        "thread_id": thread_id,
        "title": title,
        "category": category,
        "last_poster": last_poster_name,
        "quotes_added": quotes_added,
    }


async def crawl_character_profile(character_id: str, db_path: str) -> dict:
    """Crawl a character's profile page for field data.

    Args:
        character_id: JCink user ID
        db_path: Path to SQLite database

    Returns:
        Summary dict
    """
    base_url = settings.forum_base_url
    profile_url = f"{base_url}/index.php?showuser={character_id}"

    print(f"[Crawler] Starting profile crawl for {character_id}")
    set_activity(f"Crawling profile for #{character_id}", character_id=character_id)

    html = await fetch_page(profile_url)
    if not html:
        return {"error": "Failed to fetch profile page"}

    profile = parse_profile_page(html, character_id)
    if profile.name:
        set_activity(
            f"Crawling profile for {profile.name}",
            character_id=character_id,
            character_name=profile.name,
        )

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await upsert_character(
            db,
            character_id=character_id,
            name=profile.name,
            profile_url=profile_url,
            group_name=profile.group_name,
            avatar_url=profile.avatar_url,
        )
        for key, value in profile.fields.items():
            await upsert_profile_field(db, character_id, key, value)
        await update_character_crawl_time(db, character_id, "profile")
        await db.commit()

    clear_activity()
    print(f"[Crawler] Profile crawl complete for {character_id}: {len(profile.fields)} fields")
    return {
        "name": profile.name,
        "fields_count": len(profile.fields),
        "group": profile.group_name,
    }


async def register_character(user_id: str, db_path: str) -> dict:
    """Register a new character by fetching their profile.

    Args:
        user_id: JCink user ID
        db_path: Path to SQLite database

    Returns:
        Character info dict
    """
    # First crawl their profile to get name/avatar
    profile_result = await crawl_character_profile(user_id, db_path)
    if "error" in profile_result:
        return profile_result

    # Then kick off a thread crawl
    thread_result = await crawl_character_threads(user_id, db_path)

    return {
        "character_id": user_id,
        "profile": profile_result,
        "threads": thread_result,
    }


async def sync_posts_from_acp(db_path: str) -> dict:
    """Sync post records using JCink Admin CP SQL dump.

    Logs into the ACP, dumps the posts table, and updates our
    local posts table with accurate dates from the database.
    This supplements the HTML-based post extraction with data
    that has precise Unix timestamps.

    Args:
        db_path: Path to SQLite database

    Returns:
        Summary dict with counts
    """
    from app.services.acp_client import ACPClient

    if not settings.admin_username or not settings.admin_password:
        return {"error": "No admin credentials configured"}

    print("[Crawler] Starting ACP post sync")
    set_activity("Syncing posts from ACP")

    client = ACPClient()
    try:
        posts = await client.fetch_posts()
        if not posts:
            clear_activity()
            return {"error": "No post data retrieved from ACP", "posts_synced": 0}

        print(f"[Crawler] ACP returned {len(posts)} total posts")
        set_activity(f"Processing {len(posts)} posts from ACP")

        # Load tracked character IDs and thread IDs
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row

            cursor = await db.execute("SELECT id FROM characters")
            tracked_chars = {row["id"] for row in await cursor.fetchall()}

            cursor = await db.execute("SELECT id FROM threads")
            tracked_threads = {row["id"] for row in await cursor.fetchall()}

        # Filter to posts by tracked characters in tracked threads
        relevant = [
            p for p in posts
            if p["character_id"] in tracked_chars
            and p.get("thread_id") in tracked_threads
        ]
        print(f"[Crawler] {len(relevant)} posts match tracked characters & threads")

        # Group by thread for efficient DB operations
        by_thread: dict[str, list[dict]] = {}
        for p in relevant:
            tid = p["thread_id"]
            by_thread.setdefault(tid, []).append(p)

        # Also count posts per (character, thread) pair for character_threads.post_count
        post_counts: dict[tuple[str, str], int] = {}
        for p in relevant:
            key = (p["character_id"], p["thread_id"])
            post_counts[key] = post_counts.get(key, 0) + 1

        # Update database
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row

            # Replace post records per thread
            threads_updated = 0
            for tid, thread_posts in by_thread.items():
                await db.execute("DELETE FROM posts WHERE thread_id = ?", (tid,))
                for p in thread_posts:
                    await db.execute(
                        "INSERT INTO posts (character_id, thread_id, post_date) VALUES (?, ?, ?)",
                        (p["character_id"], tid, p.get("post_date")),
                    )
                threads_updated += 1

            # Update character_threads.post_count
            counts_updated = 0
            for (cid, tid), count in post_counts.items():
                result = await db.execute(
                    "UPDATE character_threads SET post_count = ? WHERE character_id = ? AND thread_id = ?",
                    (count, cid, tid),
                )
                if result.rowcount > 0:
                    counts_updated += 1

            await db.commit()

        clear_activity()
        summary = {
            "total_acp_posts": len(posts),
            "relevant_posts": len(relevant),
            "threads_updated": threads_updated,
            "counts_updated": counts_updated,
        }
        print(f"[Crawler] ACP sync complete: {summary}")
        return summary

    finally:
        await client.close()


async def discover_characters(db_path: str) -> dict:
    """Auto-discover characters by crawling the full forum member list.

    Paginates through every page of the member list, registering any
    new characters found. No artificial limits on user IDs or page counts.

    Returns:
        Summary dict with counts
    """
    base_url = settings.forum_base_url

    print("[Crawler] Starting auto-discovery via member list")
    set_activity("Discovering characters")

    # Pre-load existing character IDs to avoid per-ID DB queries
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id FROM characters")
        rows = await cursor.fetchall()
        existing_ids = {row["id"] for row in rows}

    # Fetch first page to get pagination info
    member_list_url = f"{base_url}/index.php?act=Members&max_results=30"
    first_page_html = await fetch_page_with_delay(member_list_url)
    if not first_page_html:
        clear_activity()
        print("[Crawler] Failed to fetch member list")
        return {"new_registered": 0, "already_tracked": 0, "skipped": 0}

    max_st = parse_member_list_pagination(first_page_html)
    page_offsets = [0] + list(range(30, max_st + 1, 30))
    total_pages = len(page_offsets)
    print(f"[Crawler] Member list has {total_pages} pages")

    new_count = 0
    existing_count = 0
    skipped_count = 0

    for page_num, st in enumerate(page_offsets, 1):
        set_activity(f"Discovering characters (page {page_num}/{total_pages})")

        if st == 0:
            html = first_page_html
        else:
            url = f"{member_list_url}&st={st}"
            html = await fetch_page_with_delay(url)
            if not html:
                skipped_count += 1
                continue

        members = parse_member_list(html)
        if not members:
            continue

        excluded = settings.excluded_name_set
        for member in members:
            uid = member["user_id"]

            if member["name"].lower() in excluded:
                skipped_count += 1
                continue

            if uid in existing_ids:
                existing_count += 1
                continue

            # Fetch their profile to get full details
            profile_url = f"{base_url}/index.php?showuser={uid}"
            profile_html = await fetch_page_with_delay(profile_url)

            if not profile_html or is_board_message(profile_html):
                skipped_count += 1
                continue

            profile = parse_profile_page(profile_html, uid)
            if not profile.name or profile.name == "Unknown":
                skipped_count += 1
                continue

            print(f"[Crawler] Discovered: {profile.name} (ID {uid})")
            set_activity(f"Discovered {profile.name}", character_id=uid, character_name=profile.name)

            try:
                async with aiosqlite.connect(db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await upsert_character(
                        db,
                        character_id=uid,
                        name=profile.name,
                        profile_url=profile_url,
                        group_name=profile.group_name,
                        avatar_url=profile.avatar_url,
                    )
                    for key, value in profile.fields.items():
                        await upsert_profile_field(db, uid, key, value)
                    await update_character_crawl_time(db, uid, "profile")
                    await db.commit()

                existing_ids.add(uid)
                await crawl_character_threads(uid, db_path)
                new_count += 1
            except Exception as e:
                print(f"[Crawler] Error registering {profile.name}: {e}")

    clear_activity()
    print(f"[Crawler] Discovery complete: {new_count} new, {existing_count} existing, {skipped_count} skipped")
    return {
        "new_registered": new_count,
        "already_tracked": existing_count,
        "skipped": skipped_count,
    }
