import re
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from app.config import settings


@dataclass
class ParsedThread:
    """A thread extracted from search results."""
    thread_id: str
    title: str
    url: str
    forum_id: str | None = None
    forum_name: str | None = None
    category: str = "ongoing"


@dataclass
class ParsedLastPoster:
    """Last poster info extracted from a thread page."""
    name: str
    user_id: str | None = None


@dataclass
class ParsedProfile:
    """Profile data extracted from a user's profile page."""
    user_id: str
    name: str
    group_name: str | None = None
    avatar_url: str | None = None
    fields: dict[str, str] = field(default_factory=dict)


def categorize_thread(forum_id: str | None) -> str:
    """Categorize a thread based on its forum ID."""
    if forum_id == settings.forum_complete_id:
        return "complete"
    elif forum_id == settings.forum_incomplete_id:
        return "incomplete"
    elif forum_id == settings.forum_comms_id:
        return "comms"
    return "ongoing"


def parse_search_results(html: str) -> tuple[list[ParsedThread], list[str]]:
    """Parse JCink search results page for threads.

    Returns:
        Tuple of (list of parsed threads, list of additional page URLs to fetch)
    """
    soup = BeautifulSoup(html, "html.parser")
    threads = []
    page_urls = []
    seen_ids = set()
    excluded = settings.excluded_forum_ids

    # Find pagination links to determine all pages
    max_st = 0
    base_url = ""
    for link in soup.select(".pagination a[href]"):
        href = link.get("href", "")
        if "javascript:" in href:
            continue
        st_match = re.search(r"st=(\d+)", href)
        if st_match:
            st = int(st_match.group(1))
            if st > max_st:
                max_st = st
                base_url = href if href.startswith("http") else f"{settings.forum_base_url}/{href.lstrip('/')}"

    # Generate all page URLs
    if max_st > 0:
        template_url = re.sub(r"&st=\d+", "", base_url)
        template_url = re.sub(r"\?st=\d+&", "?", template_url)
        template_url = re.sub(r"\?st=\d+$", "", template_url)
        sep = "&" if "?" in template_url else "?"
        for st in range(25, max_st + 1, 25):
            page_urls.append(f"{template_url}{sep}st={st}")

    # Parse thread results from tableborder divs (JCink search result format)
    for result_div in soup.select(".tableborder"):
        topic_link = result_div.select_one('a[href*="showtopic="]')
        if not topic_link:
            continue

        href = topic_link.get("href", "")
        topic_match = re.search(r"showtopic=(\d+)", href)
        if not topic_match:
            continue

        thread_id = topic_match.group(1)
        if thread_id in seen_ids:
            continue
        seen_ids.add(thread_id)

        # Get forum info
        forum_link = result_div.select_one('a[href*="showforum="]')
        forum_id = None
        forum_name = ""
        if forum_link:
            forum_name = forum_link.get_text(strip=True)
            f_match = re.search(r"showforum=(\d+)", forum_link.get("href", ""))
            forum_id = f_match.group(1) if f_match else None

        # Skip excluded forums
        if forum_id and forum_id in excluded:
            continue

        # Skip excluded forum names
        excluded_names = {"Guidebook", "OOC Archives"}
        if forum_name in excluded_names:
            continue

        title = topic_link.get_text(strip=True)

        # Skip auto-claim threads
        if "From: Auto Claims" in title:
            continue

        # Build full URL
        if not href.startswith("http"):
            href = f"{settings.forum_base_url}/{href.lstrip('/')}"

        category = categorize_thread(forum_id)

        threads.append(ParsedThread(
            thread_id=thread_id,
            title=title,
            url=href,
            forum_id=forum_id,
            forum_name=forum_name,
            category=category,
        ))

    return threads, page_urls


def parse_last_poster(html: str) -> ParsedLastPoster | None:
    """Extract the last poster from a thread page.

    Looks at the final .pr-wrap element on the page.
    """
    soup = BeautifulSoup(html, "html.parser")
    posts = soup.select(".pr-wrap")
    if not posts:
        return None

    last_post = posts[-1]
    name_el = last_post.select_one(".pr-name a, .pr-name")
    if not name_el:
        return None

    name = name_el.get_text(strip=True)
    user_id = None
    user_link = last_post.select_one('.pr-name a[href*="showuser="]')
    if user_link:
        match = re.search(r"showuser=(\d+)", user_link.get("href", ""))
        if match:
            user_id = match.group(1)

    return ParsedLastPoster(name=name, user_id=user_id)


def parse_thread_pagination(html: str) -> int:
    """Get the highest st= value from thread pagination.

    Returns 0 if single page.
    """
    soup = BeautifulSoup(html, "html.parser")
    max_st = 0
    for link in soup.select('.pagination a[href*="st="]'):
        match = re.search(r"st=(\d+)", link.get("href", ""))
        if match:
            st = int(match.group(1))
            if st > max_st:
                max_st = st
    return max_st


# Group ID to name mapping for the proper TWAI theme
_GROUP_MAP = {
    "4": "Admin",
    "5": "Reserved",
    "6": "Red",
    "7": "Orange",
    "8": "Yellow",
    "9": "Green",
    "10": "Blue",
    "11": "Purple",
    "12": "Corrupted",
    "13": "Pastel",
    "14": "Pink",
    "15": "Neutral",
}


def parse_profile_page(html: str, user_id: str) -> ParsedProfile:
    """Extract profile data from a JCink profile page (proper TWAI theme).

    Extracts:
    - Username from h1.profile-name or page title
    - Group name from .profile-app.group-{N} class
    - Avatar URL from .hero-sq-top background-image
    - Custom profile fields from dl.profile-dossier (dt/dd pairs)
    """
    soup = BeautifulSoup(html, "html.parser")

    # Get character name from h1.profile-name
    name_el = soup.select_one("h1.profile-name")
    if name_el:
        name = name_el.get_text(strip=True)
    else:
        # Fallback: parse from page title "Viewing Profile -> Name"
        title_el = soup.select_one("title")
        if title_el and "->" in title_el.get_text():
            name = title_el.get_text().split("->")[-1].strip()
        else:
            name = "Unknown"

    # Get group name from .profile-app.group-{N} class
    group_name = None
    profile_app = soup.select_one(".profile-app")
    if profile_app:
        for cls in profile_app.get("class", []):
            match = re.match(r"group-(\d+)", cls)
            if match:
                group_name = _GROUP_MAP.get(match.group(1), cls)
                break

    # Get avatar from .hero-sq-top background-image
    avatar_url = None
    avatar_el = soup.select_one(".hero-sq-top")
    if avatar_el:
        style = avatar_el.get("style", "")
        url_match = re.search(r"url\(['\"]?(https?://[^'\"\)\s,]+)['\"]?\)", style, re.I)
        if url_match:
            avatar_url = url_match.group(1)

    # Fallback avatar: .profile-gif or any hero image
    if not avatar_url:
        for sel in [".profile-gif", ".hero-rect", ".hero-portrait"]:
            el = soup.select_one(sel)
            if el:
                style = el.get("style", "")
                url_match = re.search(r"url\(['\"]?(https?://[^'\"\)\s,]+)['\"]?\)", style, re.I)
                if url_match:
                    avatar_url = url_match.group(1)
                    break

    # Extract custom profile fields from dl.profile-dossier (dt/dd pairs)
    fields = {}
    dossier = soup.select_one("dl.profile-dossier")
    if dossier:
        dts = dossier.select("dt")
        dds = dossier.select("dd")
        for dt, dd in zip(dts, dds):
            field_key = dt.get_text(strip=True).lower()
            field_value = dd.get_text(strip=True)
            if field_key and field_value and field_value != "No Information":
                fields[field_key] = field_value

    # Also grab codename if present
    codename_el = soup.select_one("h2.profile-codename")
    if codename_el:
        codename = codename_el.get_text(strip=True)
        if codename and codename != "No Information":
            fields["codename"] = codename

    return ParsedProfile(
        user_id=user_id,
        name=name,
        group_name=group_name,
        avatar_url=avatar_url,
        fields=fields,
    )


def parse_avatar_from_profile(html: str) -> str | None:
    """Extract just the avatar URL from a profile page.

    Checks .hero-sq-top and .profile-gif elements first (field_8),
    then falls back to any element with background-image.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Primary: field_8 in .hero-sq-top or .profile-gif
    for selector in [".hero-sq-top", ".profile-gif"]:
        el = soup.select_one(selector)
        if el:
            style = el.get("style", "")
            match = re.search(r"url\(['\"]?(https?://[^'\"\)\s]+)['\"]?\)", style, re.I)
            if match:
                return match.group(1)

    # Fallback: any element with background-image
    for el in soup.select("[style*='background-image']"):
        style = el.get("style", "")
        match = re.search(r"url\(['\"]?(https?://[^'\"\)\s]+)['\"]?\)", style, re.I)
        if match:
            return match.group(1)

    return None


def extract_quotes_from_html(html: str, character_name: str) -> list[dict]:
    """Extract dialog quotes from a thread page.

    Finds bold text matching dialog patterns (<b>"..."</b> or <strong>"..."</strong>)
    but ONLY from posts authored by the specified character.

    Returns list of dicts with 'text' key.
    """
    soup = BeautifulSoup(html, "html.parser")
    quotes = []
    min_words = settings.quote_min_words

    for post_container in soup.select(".pr-wrap"):
        # Check if this post is by the character
        name_el = post_container.select_one(".pr-name")
        if not name_el:
            continue

        post_author = name_el.get_text(strip=True)
        if post_author.lower() != character_name.lower():
            continue

        # Find bold elements in post body
        # First check inside .pr-wrap (some themes nest it)
        post_body = post_container.select_one(".pr-body, .postcolor")
        if not post_body:
            # In most JCink themes, .pr-body/.postcolor is a sibling of .pr-wrap,
            # not a child — walk siblings to find the post content
            for sibling in post_container.find_next_siblings():
                # Stop at the next post's author block to avoid cross-post matches
                if sibling.select_one(".pr-name") or "pr-wrap" in sibling.get("class", []):
                    break
                if "postcolor" in sibling.get("class", []) or "pr-body" in sibling.get("class", []):
                    post_body = sibling
                    break
                found = sibling.select_one(".postcolor")
                if found:
                    post_body = found
                    break
        if not post_body:
            continue

        for bold_el in post_body.select("b, strong"):
            text = bold_el.get_text(strip=True)

            # Check if it starts with a quote character
            if not re.match(r'^["\'\u201C\u2018]', text):
                continue

            # Clean up quote marks
            cleaned = re.sub(r'^["\'\u201C\u2018]+', '', text)
            cleaned = re.sub(r'["\'\u201D\u2019]+$', '', cleaned)
            cleaned = cleaned.strip()

            # Check minimum word count
            word_count = len(cleaned.split())
            if word_count < min_words:
                continue

            # Reasonable max length
            if len(cleaned) > 500:
                cleaned = cleaned[:500].rsplit(" ", 1)[0] + "..."

            quotes.append({"text": cleaned})

    return quotes


def parse_member_list(html: str) -> list[dict]:
    """Parse JCink member list page for user IDs and names.

    Returns list of dicts with 'user_id' and 'name' keys.
    """
    soup = BeautifulSoup(html, "html.parser")
    members = []
    seen_ids = set()

    for link in soup.select('a[href*="showuser="]'):
        href = link.get("href", "")
        match = re.search(r"showuser=(\d+)", href)
        if not match:
            continue

        user_id = match.group(1)
        if user_id in seen_ids:
            continue
        seen_ids.add(user_id)

        name = link.get_text(strip=True)
        if not name:
            continue

        members.append({"user_id": user_id, "name": name})

    return members


def parse_member_list_pagination(html: str) -> int:
    """Get the highest st= value from member list pagination.

    Returns 0 if single page.
    """
    soup = BeautifulSoup(html, "html.parser")
    max_st = 0
    for link in soup.select('.pagination a[href*="st="]'):
        match = re.search(r"st=(\d+)", link.get("href", ""))
        if match:
            st = int(match.group(1))
            if st > max_st:
                max_st = st
    return max_st


def parse_search_redirect(html: str) -> str | None:
    """Check if a search results page has a meta refresh redirect.

    JCink sometimes returns a redirect page before showing results.
    """
    soup = BeautifulSoup(html, "html.parser")
    refresh = soup.select_one('meta[http-equiv="refresh"]')
    if refresh:
        content = refresh.get("content", "")
        match = re.search(r"url=(.+)$", content, re.I)
        if match:
            url = match.group(1)
            if not url.startswith("http"):
                url = f"{settings.forum_base_url}/{url.lstrip('/')}"
            return url
    return None


def is_board_message(html: str) -> bool:
    """Check if the page is a JCink 'Board Message' (error/cooldown)."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("title")
    return title is not None and "Board Message" in title.get_text()
