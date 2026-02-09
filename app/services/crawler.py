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
    is_board_message,
)
from app.models.operations import (
    upsert_character,
    update_character_crawl_time,
    upsert_thread,
    link_character_thread,
    add_quote,
    mark_thread_quote_scraped,
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
    if character_name:
        set_activity(f"Crawling threads for {character_name}", character_id=character_id, character_name=character_name)

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

        # Extract authors from pages we already have (no extra HTTP)
        thread_author_ids: set[str] = set()
        thread_author_ids.update(extract_thread_authors(thread_html))
        if last_page_html:
            thread_author_ids.update(extract_thread_authors(last_page_html))

        if chars_needing_scrape:
            # Collect all pages for quote extraction (reuse already-fetched)
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
                            # Also grab authors from intermediate pages
                            thread_author_ids.update(extract_thread_authors(ph))

            # Extract quotes for every character that needs this thread scraped
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
            await link_character_thread(
                db,
                character_id=character_id,
                thread_id=thread.thread_id,
                category=thread.category,
                is_user_last_poster=result["is_user_last"],
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
                    )

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
    set_activity(f"Crawling profile", character_id=character_id)

    html = await fetch_page(profile_url)
    if not html:
        return {"error": "Failed to fetch profile page"}

    profile = parse_profile_page(html, character_id)

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


async def discover_characters(db_path: str) -> dict:
    """Auto-discover characters by iterating through user IDs starting from 1.

    Fetches each profile page sequentially. If a profile returns a valid
    character name, it gets registered. Stops after consecutive misses.

    Returns:
        Summary dict with counts
    """
    base_url = settings.forum_base_url
    max_id = settings.discovery_max_user_id
    consecutive_misses = 0
    max_consecutive_misses = settings.discovery_max_consecutive_misses

    print(f"[Crawler] Starting auto-discovery from ID 1 to {max_id}")
    set_activity("Discovering characters")

    # Pre-load existing character IDs to avoid per-ID DB queries
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id FROM characters")
        rows = await cursor.fetchall()
        existing_ids = {row["id"] for row in rows}

    new_count = 0
    existing_count = 0
    skipped_count = 0

    for user_id in range(1, max_id + 1):
        uid = str(user_id)

        # Skip if already tracked (using pre-loaded set)
        if uid in existing_ids:
            existing_count += 1
            consecutive_misses = 0
            continue

        # Fetch profile page
        profile_url = f"{base_url}/index.php?showuser={uid}"
        html = await fetch_page_with_delay(profile_url)

        if not html or is_board_message(html):
            consecutive_misses += 1
            skipped_count += 1
            if consecutive_misses >= max_consecutive_misses:
                print(f"[Crawler] {max_consecutive_misses} consecutive misses at ID {uid}, stopping discovery")
                break
            continue

        # Check if the profile actually has a character name
        profile = parse_profile_page(html, uid)
        if not profile.name or profile.name == "Unknown":
            consecutive_misses += 1
            skipped_count += 1
            continue

        consecutive_misses = 0
        print(f"[Crawler] Discovered: {profile.name} (ID {uid})")
        set_activity(f"Discovered {profile.name}", character_id=uid, character_name=profile.name)

        try:
            # Save the profile we already fetched
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

            # Crawl their threads
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
