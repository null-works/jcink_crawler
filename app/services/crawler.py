import asyncio
import aiosqlite
from app.config import settings
from app.services.activity import set_activity, clear_activity, log_debug
from app.services.fetcher import fetch_page, fetch_page_rendered, fetch_page_with_delay, fetch_pages_concurrent
from app.services.parser import (
    parse_search_results,
    parse_search_redirect,
    parse_last_poster,
    parse_thread_pagination,
    parse_profile_page,
    parse_application_url,
    parse_power_grid,
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
    delete_character,
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

    log_debug(f"Starting thread crawl for character {character_id}")
    set_activity(f"Crawling threads", character_id=character_id)

    # Step 1: Hit search, handle redirect (with cooldown retry)
    html = None
    for attempt in range(3):
        html = await fetch_page(search_url)
        if not html:
            log_debug(f"Failed to fetch search page for {character_id}", level="error")
            return {"error": "Failed to fetch search page"}

        # Check for redirect
        redirect_url = parse_search_redirect(html)
        if redirect_url:
            log_debug(f"Following search redirect to {redirect_url}")
            await asyncio.sleep(settings.request_delay_seconds)
            html = await fetch_page(redirect_url)
            if not html:
                return {"error": "Failed to follow search redirect"}

        if is_board_message(html):
            if attempt < 2:
                wait = 30 * (attempt + 1)
                log_debug(f"Search cooldown for {character_id}, waiting {wait}s (attempt {attempt + 1}/3)", level="error")
                await asyncio.sleep(wait)
                continue
            log_debug(f"Search cooldown persists for {character_id} after 3 attempts", level="error")
            return {"error": "Search cooldown, retries exhausted"}
        break  # Success — got search results

    # Step 2: Parse first page of results
    all_threads, page_urls = parse_search_results(html)
    log_debug(f"First page: {len(all_threads)} threads, {len(page_urls)} additional pages")

    # Step 3: Fetch additional search pages concurrently
    if page_urls:
        page_htmls = await fetch_pages_concurrent(page_urls)
        seen_ids = {t.thread_id for t in all_threads}
        for page_html in page_htmls:
            if not page_html:
                continue
            if is_board_message(page_html):
                log_debug("Skipping board message page in search results", level="error")
                continue
            page_threads, _ = parse_search_results(page_html)
            for t in page_threads:
                if t.thread_id not in seen_ids:
                    all_threads.append(t)
                    seen_ids.add(t.thread_id)

    log_debug(f"Total threads found: {len(all_threads)}")

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

        # Extract last poster and their post excerpt from the last page
        last_poster = parse_last_poster(thread_html_for_poster)
        last_poster_name = last_poster.name if last_poster else None
        last_poster_id = last_poster.user_id if last_poster else None
        is_user_last = (
            last_poster_id == character_id
            if last_poster_id
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
                    page_quotes = extract_quotes_from_html(page_html, cname, cid)
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

    # Summarize quote extraction results for debugging
    total_quotes_all_chars = 0
    total_quotes_target = 0
    threads_with_quotes = 0
    for r in thread_results:
        if isinstance(r, Exception) or r is None:
            continue
        qbc = r.get("quotes_by_character", {})
        thread_has_quotes = False
        for cid, cq in qbc.items():
            total_quotes_all_chars += len(cq)
            if cid == character_id:
                total_quotes_target += len(cq)
            if cq:
                thread_has_quotes = True
        if thread_has_quotes:
            threads_with_quotes += 1
    if character_name:
        log_debug(
            f"Quote summary for {character_name}: {total_quotes_target} quotes from {threads_with_quotes}/{len(all_threads)} threads "
            f"({total_quotes_all_chars} total across all chars)"
        )

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        for result in thread_results:
            if isinstance(result, Exception):
                log_debug(f"Error processing thread: {result}", level="error")
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
    log_debug(f"Thread crawl complete for {character_id}: {results}", level="done")
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

    log_debug(f"Targeted crawl for thread {thread_id}")
    set_activity(f"Targeted crawl: thread {thread_id}", character_id=user_id)

    # Wait for JCink to finish processing the post submission.
    # The webhook fires on form submit (before JCink saves the post),
    # so without this delay we'd fetch stale thread data.
    if settings.webhook_crawl_delay_seconds > 0:
        await asyncio.sleep(settings.webhook_crawl_delay_seconds)

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

    # Extract last poster and their post excerpt
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
                char_quotes.extend(extract_quotes_from_html(page_html, cname, cid))
            quotes_by_character[cid] = char_quotes
            chars_to_mark.append(cid)

    # Determine if user is last poster
    is_user_last = (
        last_poster_id == user_id
        if last_poster_id and user_id
        else False
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
    log_debug(f"Targeted crawl complete: thread {thread_id}, {quotes_added} quotes added", level="done")
    return {
        "thread_id": thread_id,
        "title": title,
        "category": category,
        "last_poster": last_poster_name,
        "quotes_added": quotes_added,
    }


async def check_profile_exists(character_id: str) -> str | None:
    """Quick httpx check whether a profile exists.

    Returns the character name if the profile is valid, or None if the
    user ID points to a deleted/banned account (board message) or the
    fetch fails entirely.  Does NOT use Playwright — this is intentionally
    lightweight so we can skip non-existent IDs fast.
    """
    url = f"{settings.forum_base_url}/index.php?showuser={character_id}"
    html = await fetch_page_with_delay(url)
    if not html or is_board_message(html):
        return None
    profile = parse_profile_page(html, character_id)
    if not profile.name or profile.name == "Unknown":
        return None
    return profile.name


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

    log_debug(f"Starting profile crawl for {character_id}")
    set_activity(f"Crawling profile for #{character_id}", character_id=character_id)

    html = await fetch_page_rendered(profile_url)
    if not html:
        return {"error": "Failed to fetch profile page"}

    # Detect removed/banned profiles — JCink returns a "Board Message" page
    # for deleted users. Without this check the parser would overwrite good
    # data with name="Unknown" and empty fields.
    if is_board_message(html):
        log_debug(f"Profile {character_id} returned board message — removing character", level="error")
        set_activity(f"Removing deleted profile #{character_id}", character_id=character_id)
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            removed = await delete_character(db, character_id)
        clear_activity()
        log_debug(f"Character {character_id} removed: {removed}", level="done")
        return {"removed": True, "character_id": character_id, "reason": "Profile no longer exists"}

    profile = parse_profile_page(html, character_id)

    # Power grid fallback: if .profile-stat extraction didn't find power grid
    # data (common when JS doesn't render), try the application thread page.
    _PG_KEYS = {"power grid - int", "power grid - str", "power grid - spd",
                "power grid - dur", "power grid - pwr", "power grid - cmb"}
    if not (_PG_KEYS & set(profile.fields.keys())):
        app_url = parse_application_url(html)
        if app_url:
            log_debug(f"No power grid from profile, trying application: {app_url}")
            app_html = await fetch_page_with_delay(app_url)
            if app_html:
                pg_fields = parse_power_grid(app_html)
                if pg_fields:
                    log_debug(f"Power grid from application: {pg_fields}")
                    profile.fields.update(pg_fields)

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
    log_debug(f"Profile crawl complete for {character_id}: {len(profile.fields)} fields", level="done")
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


async def sync_posts_from_acp(db_path: str, username: str | None = None, password: str | None = None) -> dict:
    """Sync thread and post data using JCink Admin CP SQL dump.

    This is the primary data sync when ACP is configured. It replaces the
    thread discovery + last-poster detection that crawl_character_threads does
    via HTML scraping. After this runs, only quote extraction needs HTML.

    Flow:
    1. Login to ACP, dump full database
    2. Parse topics → upsert threads with forum categorization + last poster
    3. Parse posts → link characters to threads, set post counts, record dates
    4. Fetch avatars for last posters (with caching)

    Credentials are resolved in order:
    1. Explicit username/password params
    2. Database crawl_status username + env var ADMIN_PASSWORD
    3. Environment variables (ADMIN_USERNAME / ADMIN_PASSWORD)

    Args:
        db_path: Path to SQLite database
        username: Optional ACP username override
        password: Optional ACP password override

    Returns:
        Summary dict with counts
    """
    from app.services.acp_client import ACPClient, extract_topic_records, extract_post_records as acp_extract_posts, extract_forum_records
    from app.services.parser import categorize_thread
    from app.models.operations import get_crawl_status, set_crawl_status
    from datetime import datetime, timezone

    # Resolve credentials: params > DB username + env password > env
    if not username or not password:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            db_user = await get_crawl_status(db, "acp_username")
        username = username or db_user or settings.admin_username
        password = password or settings.admin_password

    if not username or not password:
        return {"error": "No admin credentials configured — set them in Admin > ACP Settings"}

    log_debug("Starting ACP full sync")
    set_activity("Syncing from ACP")

    client = ACPClient(username=username, password=password)
    try:
        raw = await client.fetch_all_data()
        if not raw:
            clear_activity()
            return {"error": "No data retrieved from ACP"}

        # Extract structured records from SQL dump
        topics = extract_topic_records(raw)
        posts = acp_extract_posts(raw, include_body=False)
        forums = extract_forum_records(raw)
        log_debug(f"ACP dump: {len(topics)} topics, {len(posts)} posts, {len(forums)} forums")

        if not topics and not posts:
            clear_activity()
            return {"error": "ACP dump contained no topic or post data"}

        # Build forum name lookup from the dump
        forum_name_map: dict[str, str] = {f["forum_id"]: f["name"] for f in forums}

        set_activity(f"Processing {len(topics)} topics, {len(posts)} posts from ACP")

        # Load tracked character IDs
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT id FROM characters")
            tracked_chars = {row["id"] for row in await cursor.fetchall()}

        # Excluded forum IDs
        excluded_forums = settings.excluded_forum_ids

        # ── Phase 1: Upsert threads from topics ──
        # Build topic lookup for quick access
        topic_map: dict[str, dict] = {}
        for t in topics:
            if t["forum_id"] in excluded_forums:
                continue
            topic_map[t["thread_id"]] = t

        # ── Phase 2: Figure out which threads involve tracked characters ──
        # Group posts by thread, and by (character, thread)
        posts_by_thread: dict[str, list[dict]] = {}
        post_counts: dict[tuple[str, str], int] = {}
        chars_in_thread: dict[str, set[str]] = {}

        for p in posts:
            tid = p.get("thread_id")
            cid = p["character_id"]
            if not tid:
                continue

            posts_by_thread.setdefault(tid, []).append(p)
            key = (cid, tid)
            post_counts[key] = post_counts.get(key, 0) + 1
            chars_in_thread.setdefault(tid, set()).add(cid)

        # Find threads that have at least one tracked character
        relevant_thread_ids = set()
        for tid, char_ids in chars_in_thread.items():
            if char_ids & tracked_chars:
                relevant_thread_ids.add(tid)

        log_debug(f"{len(relevant_thread_ids)} threads involve tracked characters")

        # ── Phase 3: Fetch last poster avatars ──
        # Collect unique last poster IDs that need avatar lookups
        avatar_cache: dict[str, str | None] = {}
        poster_ids_needing_avatar: set[str] = set()
        for tid in relevant_thread_ids:
            topic = topic_map.get(tid)
            if topic and topic.get("last_poster_id"):
                poster_ids_needing_avatar.add(topic["last_poster_id"])

        # Check which avatars we already have in DB
        if poster_ids_needing_avatar:
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                placeholders = ",".join("?" * len(poster_ids_needing_avatar))
                cursor = await db.execute(
                    f"SELECT id, avatar_url FROM characters WHERE id IN ({placeholders})",
                    list(poster_ids_needing_avatar),
                )
                for row in await cursor.fetchall():
                    if row["avatar_url"]:
                        avatar_cache[row["id"]] = row["avatar_url"]

                # Also check threads table for cached avatars
                cursor = await db.execute(
                    f"SELECT last_poster_id, last_poster_avatar FROM threads WHERE last_poster_id IN ({placeholders}) AND last_poster_avatar IS NOT NULL",
                    list(poster_ids_needing_avatar),
                )
                for row in await cursor.fetchall():
                    if row["last_poster_id"] not in avatar_cache:
                        avatar_cache[row["last_poster_id"]] = row["last_poster_avatar"]

        # Fetch missing avatars via HTTP (batched, polite)
        missing_avatar_ids = poster_ids_needing_avatar - set(avatar_cache.keys())
        if missing_avatar_ids:
            log_debug(f"Fetching {len(missing_avatar_ids)} last-poster avatars")
            set_activity(f"Fetching {len(missing_avatar_ids)} avatars")
            for poster_id in missing_avatar_ids:
                try:
                    avatar_html = await fetch_page_with_delay(
                        f"{settings.forum_base_url}/index.php?showuser={poster_id}"
                    )
                    if avatar_html:
                        avatar_cache[poster_id] = parse_avatar_from_profile(avatar_html)
                    else:
                        avatar_cache[poster_id] = None
                except Exception:
                    avatar_cache[poster_id] = None

        # ── Phase 4: Write everything to DB ──
        set_activity("Writing ACP data to database")
        base_url = settings.forum_base_url
        threads_upserted = 0
        links_created = 0
        posts_stored = 0

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row

            for tid in relevant_thread_ids:
                topic = topic_map.get(tid)
                if not topic:
                    # Thread exists in posts but not in topics — skip thread upsert
                    # but still process posts for it if thread already exists in our DB
                    cursor = await db.execute("SELECT id FROM threads WHERE id = ?", (tid,))
                    if not await cursor.fetchone():
                        continue

                if topic:
                    category = categorize_thread(topic["forum_id"])
                    thread_url = f"{base_url}/index.php?showtopic={tid}"
                    last_poster_id = topic.get("last_poster_id")
                    last_poster_avatar = avatar_cache.get(last_poster_id) if last_poster_id else None

                    await upsert_thread(
                        db,
                        thread_id=tid,
                        title=topic["title"],
                        url=thread_url,
                        forum_id=topic["forum_id"],
                        forum_name=forum_name_map.get(topic["forum_id"]),
                        category=category,
                        last_poster_id=last_poster_id,
                        last_poster_name=topic.get("last_poster_name"),
                        last_poster_avatar=last_poster_avatar,
                    )
                    threads_upserted += 1

                # Link tracked characters to this thread
                thread_chars = chars_in_thread.get(tid, set())
                topic_data = topic_map.get(tid)
                category = categorize_thread(topic_data["forum_id"]) if topic_data else "ongoing"

                for cid in thread_chars:
                    if cid not in tracked_chars:
                        continue
                    is_last = (
                        topic_data.get("last_poster_id") == cid
                        if topic_data else False
                    )
                    count = post_counts.get((cid, tid), 0)
                    await link_character_thread(
                        db,
                        character_id=cid,
                        thread_id=tid,
                        category=category,
                        is_user_last_poster=is_last,
                        post_count=count,
                    )
                    links_created += 1

                # Store individual post records for date-based activity queries
                thread_posts = posts_by_thread.get(tid, [])
                # Only store posts by tracked characters
                relevant_posts = [p for p in thread_posts if p["character_id"] in tracked_chars]
                if relevant_posts:
                    await db.execute("DELETE FROM posts WHERE thread_id = ?", (tid,))
                    for p in relevant_posts:
                        await db.execute(
                            "INSERT INTO posts (character_id, thread_id, post_date) VALUES (?, ?, ?)",
                            (p["character_id"], tid, p.get("post_date")),
                        )
                    posts_stored += len(relevant_posts)

            await db.commit()

        # Record last sync time
        async with aiosqlite.connect(db_path) as db:
            await set_crawl_status(db, "acp_last_sync", datetime.now(timezone.utc).isoformat())

        clear_activity()
        summary = {
            "total_topics": len(topics),
            "total_posts": len(posts),
            "threads_upserted": threads_upserted,
            "character_links": links_created,
            "posts_stored": posts_stored,
        }
        log_debug(f"ACP sync complete: {summary}", level="done")
        return summary

    finally:
        await client.close()


async def crawl_quotes_only(db_path: str, batch_size: int | None = None) -> dict:
    """Crawl thread pages for quote extraction only.

    This is designed to run after sync_posts_from_acp() which already handles
    thread discovery, post counts, and last poster. This function only needs
    to fetch the HTML of threads that haven't been quote-scraped yet and
    extract dialog quotes from the post bodies.

    Uses the quote_crawl_log to skip threads already processed.

    Args:
        db_path: Path to SQLite database
        batch_size: Max threads to process per run (0 = unlimited, default from config)

    Returns:
        Summary dict with counts
    """
    if batch_size is None:
        batch_size = settings.crawl_quotes_batch_size

    base_url = settings.forum_base_url

    log_debug("Starting quote-only crawl pass")
    set_activity("Crawling quotes")

    # Load all characters and find threads needing quote scraping
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        # All known characters
        cursor = await db.execute("SELECT id, name FROM characters")
        rows = await cursor.fetchall()
        all_characters = {row["id"]: row["name"] for row in rows}

        if not all_characters:
            clear_activity()
            return {"threads_processed": 0, "quotes_added": 0}

        # Find threads that have at least one character not yet quote-scraped.
        # We look at character_threads to find which threads involve tracked characters,
        # then check quote_crawl_log to see if they've been scraped.
        query = """
            SELECT DISTINCT ct.thread_id, t.url
            FROM character_threads ct
            JOIN threads t ON t.id = ct.thread_id
            WHERE NOT EXISTS (
                SELECT 1 FROM quote_crawl_log qcl
                WHERE qcl.thread_id = ct.thread_id
                  AND qcl.character_id = ct.character_id
            )
        """
        if batch_size and batch_size > 0:
            query += " LIMIT ?"
            cursor = await db.execute(query, (batch_size,))
        else:
            cursor = await db.execute(query)
        threads_to_scrape = [dict(r) for r in await cursor.fetchall()]

    if not threads_to_scrape:
        clear_activity()
        log_debug("No threads need quote scraping")
        return {"threads_processed": 0, "quotes_added": 0}

    log_debug(f"{len(threads_to_scrape)} threads need quote scraping")

    total_quotes = 0
    threads_processed = 0
    threads_total = len(threads_to_scrape)

    for thread_row in threads_to_scrape:
        tid = thread_row["thread_id"]
        thread_url = thread_row["url"] or f"{base_url}/index.php?showtopic={tid}"

        set_activity(
            f"Scraping quotes ({threads_processed + 1}/{threads_total})",
        )

        # Fetch all pages of this thread
        thread_html = await fetch_page_with_delay(thread_url)
        if not thread_html or is_board_message(thread_html):
            log_debug(f"Quote scrape: skipped thread {tid} (fetch failed or board message)", level="error")
            continue

        max_st = parse_thread_pagination(thread_html)
        all_pages = [thread_html]

        if max_st > 0:
            page_urls = []
            for st in range(25, max_st + 1, 25):
                sep = "&" if "?" in thread_url else "?"
                page_urls.append(f"{thread_url}{sep}st={st}")
            if page_urls:
                page_htmls = await fetch_pages_concurrent(page_urls)
                all_pages.extend(h for h in page_htmls if h)

        # Check which characters still need this thread scraped
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT character_id FROM quote_crawl_log WHERE thread_id = ?",
                (tid,),
            )
            already_scraped = {row["character_id"] for row in await cursor.fetchall()}

        chars_needing = {
            cid: cname for cid, cname in all_characters.items()
            if cid not in already_scraped
        }

        # Extract quotes for each character
        thread_title = None
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT title FROM threads WHERE id = ?", (tid,))
            row = await cursor.fetchone()
            thread_title = row["title"] if row else "Unknown"

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            for cid, cname in chars_needing.items():
                char_quotes = []
                for page_html in all_pages:
                    char_quotes.extend(extract_quotes_from_html(page_html, cname, cid))

                added_count = 0
                for q in char_quotes:
                    added = await add_quote(db, cid, q["text"], tid, thread_title)
                    if added:
                        total_quotes += 1
                        added_count += 1

                if char_quotes:
                    log_debug(f"Thread {tid}: {cname} — {len(char_quotes)} quotes found, {added_count} new")

                await mark_thread_quote_scraped(db, tid, cid)

            await db.commit()

        threads_processed += 1

    clear_activity()
    log_debug(f"Quote-only crawl complete: {threads_processed} threads, {total_quotes} quotes", level="done")
    return {
        "threads_processed": threads_processed,
        "quotes_added": total_quotes,
    }


async def crawl_recent_threads(db_path: str) -> dict:
    """Crawl all threads visible in RP forum listings to capture post records.

    Browses each non-excluded forum's topic listing page and collects thread
    URLs. Then fetches each thread (all pages) and extracts post records.
    This doesn't use JCink search at all, so there's no flood control risk.

    Only updates post records (for activity tracking) — does not update
    thread metadata or character links (the per-character crawl handles that).
    """
    import re
    from bs4 import BeautifulSoup

    base_url = settings.forum_base_url
    excluded_forums = settings.excluded_forum_ids

    log_debug("Browsing forum listings for threads")
    set_activity("Scanning forum listings")

    # Step 1: Get all forum IDs from the main index page
    index_html = await fetch_page_with_delay(f"{base_url}/index.php")
    if not index_html:
        clear_activity()
        return {"error": "Failed to fetch forum index"}

    soup = BeautifulSoup(index_html, "html.parser")
    forum_ids = set()
    for link in soup.select('a[href*="showforum="]'):
        m = re.search(r"showforum=(\d+)", link.get("href", ""))
        if m and m.group(1) not in excluded_forums:
            forum_ids.add(m.group(1))

    log_debug(f"Found {len(forum_ids)} non-excluded forums")

    # Step 2: Browse each forum's first page to collect thread IDs
    thread_ids = set()
    for fid in forum_ids:
        html = await fetch_page_with_delay(f"{base_url}/index.php?showforum={fid}")
        if not html:
            continue
        fsoup = BeautifulSoup(html, "html.parser")
        for link in fsoup.select('a[href*="showtopic="]'):
            m = re.search(r"showtopic=(\d+)", link.get("href", ""))
            if m:
                thread_ids.add(m.group(1))

    log_debug(f"Found {len(thread_ids)} threads across all forums")

    if not thread_ids:
        clear_activity()
        return {"threads": 0, "posts_stored": 0}

    # Load tracked character IDs
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id FROM characters")
        tracked_chars = {row["id"] for row in await cursor.fetchall()}

    # Step 3: Fetch each thread (all pages) and extract post records
    posts_stored = 0
    threads_processed = 0
    total = len(thread_ids)

    async def _fetch_thread_posts(tid: str):
        """Fetch all pages of a thread and extract post records."""
        url = f"{base_url}/index.php?showtopic={tid}"
        thread_html = await fetch_page_with_delay(url)
        if not thread_html:
            return None

        max_st = parse_thread_pagination(thread_html)
        all_pages = [thread_html]

        if max_st > 0:
            remaining_urls = []
            last_page_html = await fetch_page_with_delay(f"{url}&st={max_st}")

            for st in range(25, max_st + 1, 25):
                if st == max_st and last_page_html:
                    all_pages.append(last_page_html)
                else:
                    remaining_urls.append(f"{url}&st={st}")

            if remaining_urls:
                intermediate_htmls = await fetch_pages_concurrent(remaining_urls)
                for ph in intermediate_htmls:
                    if ph:
                        all_pages.append(ph)

        records = []
        for page_html in all_pages:
            records.extend(extract_post_records(page_html))
        return {"thread_id": tid, "records": records}

    set_activity(f"Fetching {total} threads from forum listings")
    tasks = [_fetch_thread_posts(tid) for tid in thread_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        for result in results:
            if isinstance(result, Exception) or result is None:
                continue

            thread_id = result["thread_id"]
            records = result["records"]
            # Only store posts by tracked characters
            relevant = [r for r in records if r["character_id"] in tracked_chars]
            if relevant:
                await replace_thread_posts(db, thread_id, relevant)
                posts_stored += len(relevant)
                threads_processed += 1

        await db.commit()

    clear_activity()
    log_debug(f"Recent threads: {threads_processed} threads, {posts_stored} posts stored", level="done")
    return {"threads": threads_processed, "posts_stored": posts_stored}


async def discover_characters(db_path: str) -> dict:
    """Auto-discover characters by crawling the full forum member list.

    Paginates through every page of the member list, registering any
    new characters found. No artificial limits on user IDs or page counts.

    Returns:
        Summary dict with counts
    """
    base_url = settings.forum_base_url

    log_debug("Starting auto-discovery via member list")
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
        log_debug("Failed to fetch member list", level="error")
        return {"new_registered": 0, "already_tracked": 0, "skipped": 0}

    max_st = parse_member_list_pagination(first_page_html)
    page_offsets = [0] + list(range(30, max_st + 1, 30))
    total_pages = len(page_offsets)
    log_debug(f"Member list has {total_pages} pages")

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

            log_debug(f"Discovered: {profile.name} (ID {uid})")
            set_activity(f"Discovered {profile.name}", character_id=uid, character_name=profile.name)

            try:
                # Full profile crawl handles Playwright + app thread fallback
                # for power grid data, rather than just storing the httpx parse.
                await crawl_character_profile(uid, db_path)
                existing_ids.add(uid)
                await crawl_character_threads(uid, db_path)
                new_count += 1
            except Exception as e:
                log_debug(f"Error registering {profile.name}: {e}", level="error")

    clear_activity()
    log_debug(f"Discovery complete: {new_count} new, {existing_count} existing, {skipped_count} skipped", level="done")
    return {
        "new_registered": new_count,
        "already_tracked": existing_count,
        "skipped": skipped_count,
    }
