import asyncio
import aiosqlite
from app.config import settings
from app.services.fetcher import fetch_page, fetch_page_with_delay
from app.services.parser import (
    parse_search_results,
    parse_search_redirect,
    parse_last_poster,
    parse_thread_pagination,
    parse_profile_page,
    parse_avatar_from_profile,
    extract_quotes_from_html,
    is_board_message,
)
from app.models.operations import (
    upsert_character,
    update_character_crawl_time,
    upsert_thread,
    link_character_thread,
    add_quote,
    is_thread_quote_scraped,
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

    Args:
        character_id: JCink user ID
        db_path: Path to SQLite database

    Returns:
        Summary dict with counts
    """
    base_url = settings.forum_base_url
    search_url = f"{base_url}/index.php?act=Search&CODE=getalluser&mid={character_id}&type=posts"

    print(f"[Crawler] Starting thread crawl for character {character_id}")

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

    # Step 3: Fetch additional pages
    for page_url in page_urls:
        page_html = await fetch_page_with_delay(page_url)
        if not page_html:
            continue
        if is_board_message(page_html):
            print(f"[Crawler] Got board message on pagination, stopping")
            break
        page_threads, _ = parse_search_results(page_html)
        # Deduplicate
        seen_ids = {t.thread_id for t in all_threads}
        for t in page_threads:
            if t.thread_id not in seen_ids:
                all_threads.append(t)
                seen_ids.add(t.thread_id)

    print(f"[Crawler] Total threads found: {len(all_threads)}")

    # Get character name for quote extraction
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        char = await get_character(db, character_id)
    character_name = char.name if char else None

    # Step 4: Fetch each thread to determine last poster + extract quotes
    results = {"ongoing": 0, "comms": 0, "complete": 0, "incomplete": 0, "quotes_added": 0}

    for thread in all_threads:
        thread_html = await fetch_page_with_delay(thread.url)
        if not thread_html:
            continue

        # Check for multi-page threads — get last page
        max_st = parse_thread_pagination(thread_html)
        if max_st > 0:
            sep = "&" if "?" in thread.url else "?"
            last_page_url = f"{thread.url}{sep}st={max_st}"
            last_page_html = await fetch_page_with_delay(last_page_url)
            if last_page_html:
                thread_html_for_poster = last_page_html
            else:
                thread_html_for_poster = thread_html
        else:
            thread_html_for_poster = thread_html

        # Extract last poster
        last_poster = parse_last_poster(thread_html_for_poster)
        last_poster_name = last_poster.name if last_poster else None
        last_poster_id = last_poster.user_id if last_poster else None
        is_user_last = (
            last_poster_name.lower() == character_name.lower()
            if last_poster_name and character_name
            else False
        )

        # Fetch last poster avatar
        last_poster_avatar = None
        if last_poster_id:
            avatar_html = await fetch_page_with_delay(
                f"{base_url}/index.php?showuser={last_poster_id}"
            )
            if avatar_html:
                last_poster_avatar = parse_avatar_from_profile(avatar_html)

        # Save to DB
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            await upsert_thread(
                db,
                thread_id=thread.thread_id,
                title=thread.title,
                url=thread.url,
                forum_id=thread.forum_id,
                forum_name=thread.forum_name,
                category=thread.category,
                last_poster_id=last_poster_id,
                last_poster_name=last_poster_name,
                last_poster_avatar=last_poster_avatar,
            )
            await link_character_thread(
                db,
                character_id=character_id,
                thread_id=thread.thread_id,
                category=thread.category,
                is_user_last_poster=is_user_last,
            )
            await db.commit()

        results[thread.category] = results.get(thread.category, 0) + 1

        # Step 5: Extract quotes if not already scraped
        if character_name:
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                already_scraped = await is_thread_quote_scraped(
                    db, thread.thread_id, character_id
                )

            if not already_scraped:
                # For quotes, we want ALL pages of the thread
                all_thread_html = [thread_html]
                if max_st > 0:
                    for st in range(25, max_st + 1, 25):
                        sep = "&" if "?" in thread.url else "?"
                        page_url = f"{thread.url}{sep}st={st}"
                        page_html = await fetch_page_with_delay(page_url)
                        if page_html:
                            all_thread_html.append(page_html)

                for page_html in all_thread_html:
                    quotes = extract_quotes_from_html(page_html, character_name)
                    async with aiosqlite.connect(db_path) as db:
                        db.row_factory = aiosqlite.Row
                        for q in quotes:
                            added = await add_quote(
                                db, character_id, q["text"],
                                thread.thread_id, thread.title
                            )
                            if added:
                                results["quotes_added"] += 1
                        await db.commit()

                # Mark thread as quote-scraped
                async with aiosqlite.connect(db_path) as db:
                    await mark_thread_quote_scraped(
                        db, thread.thread_id, character_id
                    )
                    await db.commit()

    # Update crawl timestamp
    async with aiosqlite.connect(db_path) as db:
        await update_character_crawl_time(db, character_id, "threads")

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
    max_consecutive_misses = 20

    print(f"[Crawler] Starting auto-discovery from ID 1 to {max_id}")

    new_count = 0
    existing_count = 0
    skipped_count = 0

    for user_id in range(1, max_id + 1):
        uid = str(user_id)

        # Skip if already tracked
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            existing = await get_character(db, uid)

        if existing:
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

    print(f"[Crawler] Discovery complete: {new_count} new, {existing_count} existing, {skipped_count} skipped")
    return {
        "new_registered": new_count,
        "already_tracked": existing_count,
        "skipped": skipped_count,
    }
