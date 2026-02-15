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

    Looks at the final .pr-a post element on the page.
    The TWAI theme uses .pr-a for post wrappers and .pr-j for the author name div.
    """
    soup = BeautifulSoup(html, "html.parser")
    posts = soup.select(".pr-a")
    if not posts:
        return None

    last_post = posts[-1]
    name_el = last_post.select_one(".pr-j")
    if not name_el:
        return None

    name = name_el.get_text(strip=True)
    user_id = None
    user_link = last_post.select_one('.pr-j a[href*="showuser="]')
    if user_link:
        match = re.search(r"showuser=(\d+)", user_link.get("href", ""))
        if match:
            user_id = match.group(1)

    return ParsedLastPoster(name=name, user_id=user_id)


def extract_thread_authors(html: str) -> set[str]:
    """Extract all unique author user IDs from a thread page.

    Parses every .pr-a post container and pulls the user ID from the
    author link in .pr-j.  Returns a set of user ID strings.
    """
    soup = BeautifulSoup(html, "html.parser")
    author_ids: set[str] = set()
    for post in soup.select(".pr-a"):
        user_link = post.select_one('.pr-j a[href*="showuser="]')
        if user_link:
            match = re.search(r"showuser=(\d+)", user_link.get("href", ""))
            if match:
                author_ids.add(match.group(1))
    return author_ids


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

    # Get character name
    # Method 1: h1.profile-name
    name_el = soup.select_one("h1.profile-name")
    # Method 2: div.pf-e (TWAI static skin)
    if not name_el:
        name_el = soup.select_one("div.pf-e")
    if name_el:
        name = name_el.get_text(strip=True)
    else:
        # Fallback: parse from page title "Viewing Profile -> Name"
        title_el = soup.select_one("title")
        if title_el and "->" in title_el.get_text():
            name = title_el.get_text().split("->")[-1].strip()
        else:
            name = "Unknown"

    # Get group name
    # Method 1: .profile-app.group-{N} class
    group_name = None
    profile_app = soup.select_one(".profile-app")
    if profile_app:
        for cls in profile_app.get("class", []):
            match = re.match(r"group-(\d+)", cls)
            if match:
                group_name = _GROUP_MAP.get(match.group(1), cls)
                break
    # Method 2: div.mp-b in pf-x (TWAI static skin)
    if not group_name:
        group_el = soup.select_one("div.pf-x div.mp-b")
        if group_el:
            group_name = group_el.get_text(strip=True)

    # Get avatar from background-image styles
    avatar_url = None
    # Try multiple selectors in order of preference
    for sel in [".hero-sq-top", ".pf-c", ".profile-gif", ".hero-rect", ".hero-portrait"]:
        el = soup.select_one(sel)
        if el:
            style = el.get("style", "")
            url_match = re.search(r"url\(['\"]?(https?://[^'\"\)\s,]+)['\"]?\)", style, re.I)
            if url_match:
                avatar_url = url_match.group(1)
                break

    # Extract custom profile fields
    fields = {}

    # Method 1: dl.profile-dossier (dt/dd pairs)
    dossier = soup.select_one("dl.profile-dossier")
    if dossier:
        dts = dossier.select("dt")
        dds = dossier.select("dd")
        for dt, dd in zip(dts, dds):
            field_key = dt.get_text(strip=True).lower()
            field_value = dd.get_text(strip=True)
            if field_key and field_value and field_value != "No Information":
                fields[field_key] = field_value

    # Method 2: div.pf-k / span.pf-l (TWAI static skin)
    if not fields:
        for pf_k in soup.select("div.pf-k"):
            label_el = pf_k.select_one("span.pf-l")
            if label_el:
                field_key = label_el.get_text(strip=True).lower()
                # Value is the text after the label span
                label_el.extract()
                field_value = pf_k.get_text(strip=True)
                if field_key and field_value and field_value != "No Information":
                    fields[field_key] = field_value

    # Grab codename from h2.profile-codename or div.pf-s span.pf-1
    codename_el = soup.select_one("h2.profile-codename")
    if not codename_el:
        codename_el = soup.select_one("div.pf-s span.pf-1")
    if codename_el:
        codename = codename_el.get_text(strip=True)
        if codename and codename.lower() != "code name" and codename != "No Information":
            fields["codename"] = codename

    # Extract "played by" from div.pf-z (format: "played by <b>name</b>")
    pf_z = soup.select_one("div.pf-z")
    if pf_z:
        bold = pf_z.select_one("b")
        if bold:
            player_name = bold.get_text(strip=True)
            if player_name:
                fields["player"] = player_name

    # Extract player metadata from div.pf-ab (title attr = key, text = value)
    for pf_ab in soup.select("div.pf-ab"):
        title = pf_ab.get("title", "").strip().lower()
        if not title:
            continue
        # Skip "please avoid: ..." trigger warnings — title contains the value already
        if title.startswith("please avoid"):
            fields["triggers"] = title.replace("please avoid: ", "").replace("please avoid:", "").strip()
            continue
        # The value is the text content minus the icon span
        icon = pf_ab.select_one("span.pf-ac")
        if icon:
            icon.extract()
        value = pf_ab.get_text(strip=True)
        if value and value != "No Information":
            fields[title] = value

    # Extract hero images from background-image styles (fields 7, 8, 21, 9)
    for selector, key in [
        (".hero-portrait", "portrait_image"),
        (".hero-sq-top", "square_image"),
        (".hero-sq-bot", "secondary_square_image"),
        (".hero-rect", "rectangle_gif"),
    ]:
        el = soup.select_one(selector)
        if el:
            style = el.get("style", "")
            img_match = re.search(r"url\(['\"]?(https?://[^'\"\)\s,]+)['\"]?\)", style, re.I)
            if img_match:
                fields[key] = img_match.group(1)

    # Extract OOC alias from .profile-ooc-footer (field_1)
    ooc_footer = soup.select_one(".profile-ooc-footer")
    if ooc_footer:
        alias_text = ooc_footer.get_text(strip=True)
        if alias_text and alias_text != "No Information":
            fields.setdefault("alias", alias_text)

    # Extract short quote from .profile-short-quote or mini profile area (field_26)
    short_quote_el = soup.select_one(".profile-short-quote")
    if short_quote_el:
        sq_text = short_quote_el.get_text(strip=True)
        if sq_text and sq_text != "No Information":
            fields["short_quote"] = sq_text

    # Extract connections from .profile-connections (field_41)
    connections_el = soup.select_one(".profile-connections")
    if connections_el:
        conn_text = connections_el.get_text(strip=True)
        if conn_text and conn_text != "No Information":
            fields["connections"] = conn_text

    # Extract power grid from .profile-stat elements (fields 27-32)
    # Each stat has a .profile-stat-label (INT/STR/etc) and a
    # .profile-stat-fill with data-value="N" holding the numeric value.
    for stat in soup.select("div.profile-stat"):
        label_el = stat.select_one(".profile-stat-label")
        fill_el = stat.select_one(".profile-stat-fill")
        if not label_el or not fill_el:
            continue
        label = label_el.get_text(strip=True).lower()
        value = (fill_el.get("data-value") or "").strip()
        if value and value != "No Information":
            fields[f"power grid - {label}"] = value

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

    The TWAI theme uses .pr-a for post wrappers, .pr-j for the author name div,
    and .postcolor for the post body (all nested inside .pr-a).

    Returns list of dicts with 'text' key.
    """
    soup = BeautifulSoup(html, "html.parser")
    quotes = []
    min_words = settings.quote_min_words

    for post_container in soup.select(".pr-a"):
        # Check if this post is by the character
        name_el = post_container.select_one(".pr-j")
        if not name_el:
            continue

        post_author = name_el.get_text(strip=True)
        if post_author.lower() != character_name.lower():
            continue

        # Find the post body
        post_body = post_container.select_one(".postcolor")
        if not post_body:
            continue

        for bold_el in post_body.select("b, strong"):
            text = bold_el.get_text(strip=True)

            # Check if it starts with a quote character
            if not re.match(r'^["\'\u201C\u2018\u00AB]', text):
                continue

            # Clean up quote marks
            cleaned = re.sub(r'^["\'\u201C\u2018\u00AB]+', '', text)
            cleaned = re.sub(r'["\'\u201D\u2019\u00BB]+$', '', cleaned)
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


_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Matches "Jan 15 2026, 08:30 PM" or "Jan 15 2026, 20:30"
_DATE_RE = re.compile(
    r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})\s+(\d{4})',
    re.IGNORECASE,
)

# JCink uses relative dates for recent posts
_TODAY_RE = re.compile(r'\bToday\b', re.IGNORECASE)
_YESTERDAY_RE = re.compile(r'\bYesterday\b', re.IGNORECASE)


def _parse_jcink_date(text: str) -> str | None:
    """Try to parse a JCink date string into ISO format (YYYY-MM-DD).

    Handles:
    - Absolute: "Jan 15 2026, 08:30 PM"
    - Relative: "Today, 08:30 PM" / "Yesterday, 05:12 AM"

    Returns date string or None if unparseable.
    """
    from datetime import datetime, timedelta, timezone

    # Check for "Today" / "Yesterday" first (JCink replaces dates for recent posts)
    if _TODAY_RE.search(text):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _YESTERDAY_RE.search(text):
        return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    match = _DATE_RE.search(text)
    if not match:
        return None
    month_str, day_str, year_str = match.group(1), match.group(2), match.group(3)
    month = _MONTH_MAP.get(month_str.lower())
    if not month:
        return None
    return f"{year_str}-{month:02d}-{int(day_str):02d}"


def extract_post_records(html: str) -> list[dict]:
    """Extract individual post records from a thread page.

    Parses each .pr-a post container for:
    - author user ID (from .pr-j author link)
    - post date (from .pr-d date container, or fallback to header text)

    Returns list of dicts: {'character_id': str, 'post_date': str | None}
    """
    from copy import copy

    soup = BeautifulSoup(html, "html.parser")
    records = []

    for post in soup.select(".pr-a"):
        # Extract author user ID
        user_link = post.select_one('.pr-j a[href*="showuser="]')
        if not user_link:
            continue
        match = re.search(r"showuser=(\d+)", user_link.get("href", ""))
        if not match:
            continue
        character_id = match.group(1)

        # Extract post date — try .pr-d first (TWAI theme date container),
        # then fall back to searching all header text
        post_date = None
        date_el = post.select_one(".pr-d")
        if date_el:
            post_date = _parse_jcink_date(date_el.get_text(" ", strip=True))

        if not post_date:
            post_copy = copy(post)
            for body in post_copy.select(".postcolor"):
                body.decompose()
            header_text = post_copy.get_text(" ", strip=True)
            post_date = _parse_jcink_date(header_text)

        records.append({"character_id": character_id, "post_date": post_date})

    return records


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
