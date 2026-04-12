import pathlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import aiosqlite

from app.database import get_db
from app.config import settings, APP_VERSION, APP_BUILD_TIME
from app.models import (
    get_character,
    get_all_characters,
    get_all_claims,
    get_character_threads,
    get_profile_fields,
    get_all_quotes,
    search_characters,
    search_threads_global,
    search_quotes_global,
    get_unique_affiliations,
    get_characters_by_affiliation,
    get_unique_groups,
    get_unique_players,
    search_players,
    get_player_detail,
    get_dashboard_stats,
    get_dashboard_chart_data,
    get_activity_check_data,
    get_all_relationships,
    get_relationships_for_character,
    create_relationship,
    update_relationship,
    delete_relationship,
    seed_relationships_from_connections,
    RELATIONSHIP_TYPES,
)
from app.models.operations import set_crawl_status, get_crawl_status, toggle_character_hidden, set_approval_date, set_approval_dates
from app.services import crawl_character_threads, crawl_character_profile, register_character
from app.services.crawler import sync_posts_from_acp, crawl_quotes_only, crawl_all_profile_fields
from app.services.scheduler import _crawl_all_characters
from app.services.activity import get_activity, get_debug_log, clear_debug_log

router = APIRouter()

TEMPLATES_DIR = pathlib.Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.globals["app_version"] = APP_VERSION
templates.env.globals["app_build"] = APP_BUILD_TIME

COOKIE_NAME = "watcher_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


# --- Jinja2 custom filters ---

def format_time(ts) -> str:
    """Format a timestamp as relative time, using America/New_York."""
    if not ts:
        return "Never"
    try:
        from app.config import now_et
        tz = ZoneInfo(settings.activity_timezone)
        if isinstance(ts, str):
            ts_clean = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts_clean)
        else:
            dt = ts
        now = now_et()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        delta = now - dt
        minutes = int(delta.total_seconds() / 60)
        if minutes < 1:
            return "Just now"
        elif minutes < 60:
            return f"{minutes}m ago"
        elif minutes < 1440:
            return f"{minutes // 60}h ago"
        else:
            return f"{minutes // 1440}d ago"
    except Exception:
        return str(ts)[:19] if ts else "Never"


templates.env.filters["format_time"] = format_time


def activity_level(ongoing_count) -> dict:
    """Derive activity level from ongoing thread count."""
    try:
        n = int(ongoing_count)
    except (TypeError, ValueError):
        n = 0
    if n >= 5:
        return {"label": "Very Active", "css": "badge-very-active", "color": "purple"}
    elif n >= 3:
        return {"label": "Active", "css": "badge-active", "color": "green"}
    elif n >= 1:
        return {"label": "Low Activity", "css": "badge-low-activity", "color": "yellow"}
    else:
        return {"label": "Inactive", "css": "badge-inactive", "color": "red"}


templates.env.filters["activity_level"] = activity_level


# --- Auth helpers ---

def _get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.dashboard_secret_key)


def _check_auth(request: Request) -> bool:
    """Check if the request has a valid session cookie. Returns True if auth is valid or no password is set."""
    if not settings.dashboard_password:
        return True
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    try:
        s = _get_serializer()
        s.loads(token, max_age=COOKIE_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


def _require_auth(request: Request):
    """Redirect to login if not authenticated."""
    if not _check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    return None


def _require_auth_htmx(request: Request):
    """Return 401 for unauthenticated HTMX requests."""
    if not _check_auth(request):
        return HTMLResponse(status_code=401, content="Unauthorized")
    return None


# --- Login / Logout ---

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if not settings.dashboard_password or _check_auth(request):
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(request, "pages/login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    form = await request.form()
    password = form.get("password", "")
    if password == settings.dashboard_password:
        s = _get_serializer()
        token = s.dumps({"auth": True})
        response = RedirectResponse(url="/dashboard", status_code=302)
        response.set_cookie(COOKIE_NAME, token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax")
        return response
    return templates.TemplateResponse(request, "pages/login.html", {"error": "Invalid password"})


@router.get("/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response


# --- Full Page Routes ---

@router.get("/", response_class=HTMLResponse)
async def root():
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_overview(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    redirect = _require_auth(request)
    if redirect:
        return redirect

    stats = await get_dashboard_stats(db)
    chart_data = await get_dashboard_chart_data(db)
    activity = get_activity()

    return templates.TemplateResponse(request, "pages/overview.html", {
        "stats": stats,
        "chart_data": chart_data,
        "activity": activity,
    })


@router.get("/characters", response_class=HTMLResponse)
async def characters_page(
    request: Request,
    q: str | None = None,
    affiliation: str | None = None,
    group: str | None = None,
    player: str | None = None,
    sort: str = "name",
    dir: str = "asc",
    page: int = 1,
    db: aiosqlite.Connection = Depends(get_db),
):
    redirect = _require_auth(request)
    if redirect:
        return redirect

    stats = await get_dashboard_stats(db)
    affiliations_list = affiliation.split(",") if affiliation else None
    characters, total = await search_characters(db, q, affiliations_list, group, player, sort, dir, page)
    all_affiliations = await get_unique_affiliations(db)
    all_groups = await get_unique_groups(db)
    all_players = await get_unique_players(db)
    activity = get_activity()

    return templates.TemplateResponse(request, "pages/characters.html", {
        "stats": stats,
        "characters": characters,
        "total": total,
        "affiliations": all_affiliations,
        "groups": all_groups,
        "players": all_players,
        "activity": activity,
        "q": q,
        "affiliation": affiliation,
        "group": group,
        "player": player,
        "sort": sort,
        "dir": dir,
        "page": page,
        "per_page": 25,
    })


@router.get("/character/{character_id}", response_class=HTMLResponse)
async def character_detail_page(
    request: Request,
    character_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    redirect = _require_auth(request)
    if redirect:
        return redirect

    char = await get_character(db, character_id)
    if not char:
        return HTMLResponse(status_code=404, content="Character not found")

    fields = await get_profile_fields(db, character_id)
    threads = await get_character_threads(db, character_id)
    quotes = await get_all_quotes(db, character_id)
    activity = get_activity()

    # Check hidden state and approval date
    cursor = await db.execute("SELECT hidden, approval_date FROM characters WHERE id = ?", (character_id,))
    row = await cursor.fetchone()
    is_hidden = bool(row["hidden"]) if row else False
    approval_date = row["approval_date"] if row else None

    total_quotes = len(quotes)
    character_relationships = await get_relationships_for_character(db, character_id)
    return templates.TemplateResponse(request, "pages/character_detail.html", {
        "character": char,
        "fields": fields,
        "threads": threads,
        "quotes": quotes[:20],
        "total_quotes": total_quotes,
        "total": total_quotes,
        "activity": activity,
        "per_page": 20,
        "page": 1,
        "q": None,
        "character_id": character_id,
        "category": None,
        "is_hidden": is_hidden,
        "approval_date": approval_date,
        "character_relationships": character_relationships,
    })


@router.get("/threads", response_class=HTMLResponse)
async def threads_page(
    request: Request,
    q: str | None = None,
    category: str | None = None,
    status: str | None = None,
    character_id: str | None = None,
    player: str | None = None,
    sort: str = "title",
    dir: str = "asc",
    page: int = 1,
    db: aiosqlite.Connection = Depends(get_db),
):
    redirect = _require_auth(request)
    if redirect:
        return redirect

    threads, total = await search_threads_global(db, q, category, status, character_id, player, sort, dir, page)
    all_chars = await get_all_characters(db)
    all_players = await get_unique_players(db)
    activity = get_activity()

    # If HTMX request, return just the table rows
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(request, "partials/thread_table_rows.html", {
            "threads": threads,
            "total": total,
            "page": page,
            "per_page": 25,
            "q": q,
            "category": category,
            "status": status,
            "character_id": character_id,
            "player": player,
            "sort": sort,
            "dir": dir,
        })

    return templates.TemplateResponse(request, "pages/threads.html", {
        "threads": threads,
        "total": total,
        "all_characters": all_chars,
        "all_players": all_players,
        "activity": activity,
        "q": q,
        "category": category,
        "status": status,
        "character_id": character_id,
        "player": player,
        "sort": sort,
        "dir": dir,
        "page": page,
        "per_page": 25,
    })


@router.get("/quotes", response_class=HTMLResponse)
async def quotes_page(
    request: Request,
    q: str | None = None,
    character_id: str | None = None,
    sort: str = "created_at",
    dir: str = "desc",
    page: int = 1,
    db: aiosqlite.Connection = Depends(get_db),
):
    redirect = _require_auth(request)
    if redirect:
        return redirect

    quotes, total = await search_quotes_global(db, q, character_id, sort, dir, page)
    all_chars = await get_all_characters(db)
    activity = get_activity()

    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(request, "partials/quote_list_items.html", {
            "quotes": quotes,
            "total": total,
            "page": page,
            "per_page": 25,
            "q": q,
            "character_id": character_id,
            "sort": sort,
            "dir": dir,
        })

    return templates.TemplateResponse(request, "pages/quotes.html", {
        "quotes": quotes,
        "total": total,
        "all_characters": all_chars,
        "activity": activity,
        "q": q,
        "character_id": character_id,
        "sort": sort,
        "dir": dir,
        "page": page,
        "per_page": 25,
    })


@router.get("/activity-check", response_class=HTMLResponse)
async def activity_check_page(
    request: Request,
    month: str | None = None,
    filter: str | None = None,
    q: str | None = None,
    sort: str | None = None,
    dir: str = "desc",
    db: aiosqlite.Connection = Depends(get_db),
):
    redirect = _require_auth(request)
    if redirect:
        return redirect

    now = datetime.now(ZoneInfo(settings.activity_timezone))
    current_month = now.strftime("%Y-%m")

    # Build last 12 months for toggle pills
    months = []
    for i in range(11, -1, -1):
        y = now.year
        m = now.month - i
        while m <= 0:
            m += 12
            y -= 1
        val = f"{y}-{m:02d}"
        label = datetime(y, m, 1).strftime("%b %Y")
        months.append({"value": val, "label": label})

    # Parse selected month (format: "YYYY-MM"), default to current
    selected = month or current_month
    try:
        dt = datetime.strptime(selected, "%Y-%m")
        month_start = dt.strftime("%Y-%m-01")
        if dt.month == 12:
            month_end = f"{dt.year + 1}-01-01"
        else:
            month_end = f"{dt.year}-{dt.month + 1:02d}-01"
    except ValueError:
        month_start = None
        month_end = None

    data = await get_activity_check_data(db, month_start, month_end)
    activity = get_activity()

    # Apply status filter — filter at the character level, then drop empty players
    if filter and filter in ("safe", "warning", "danger", "pending"):
        filtered_players = []
        for p in data["players"]:
            chars = [c for c in p["characters"] if c["ac_status"] == filter]
            if chars:
                p = {**p, "characters": chars}
                filtered_players.append(p)
        data["players"] = filtered_players

    # Apply sorting
    if sort in ("monthly_posts", "total_posts"):
        key = "total_monthly_posts" if sort == "monthly_posts" else "total_posts"
        reverse = dir != "asc"
        data["players"] = sorted(data["players"], key=lambda p: p[key], reverse=reverse)

    return templates.TemplateResponse(request, "pages/activity_check.html", {
        "data": data,
        "activity": activity,
        "month": month,
        "current_month": current_month,
        "months": months,
        "filter": filter,
        "q": q or "",
        "sort": sort or "",
        "dir": dir,
    })


@router.get("/ac-results", response_class=HTMLResponse)
async def ac_results_page(
    request: Request,
    period: str | None = None,
    q: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
):
    redirect = _require_auth(request)
    if redirect:
        return redirect

    now = datetime.now(ZoneInfo(settings.activity_timezone))
    current_month = now.strftime("%Y-%m")

    # Previous month
    if now.month == 1:
        prev_y, prev_m = now.year - 1, 12
    else:
        prev_y, prev_m = now.year, now.month - 1
    prev_month = f"{prev_y}-{prev_m:02d}"

    # Default to previous month ("last")
    if period == "current":
        selected = current_month
    else:
        period = "last"
        selected = prev_month

    dt = datetime.strptime(selected, "%Y-%m")
    month_start = dt.strftime("%Y-%m-01")
    if dt.month == 12:
        month_end = f"{dt.year + 1}-01-01"
    else:
        month_end = f"{dt.year}-{dt.month + 1:02d}-01"

    data = await get_activity_check_data(db, month_start, month_end)
    activity = get_activity()

    # Filter to only danger + warning characters (missed AC)
    filtered_players = []
    failed_total = 0
    for p in data["players"]:
        chars = [c for c in p["characters"] if c["ac_status"] in ("danger", "warning")]
        if chars:
            failed_total += len(chars)
            filtered_players.append({**p, "characters": chars})
    data["players"] = filtered_players

    period_label = datetime.strptime(selected, "%Y-%m").strftime("%B %Y")

    return templates.TemplateResponse(request, "pages/ac_results.html", {
        "data": data,
        "activity": activity,
        "period": period,
        "period_label": period_label,
        "failed_total": failed_total,
        "q": q or "",
    })


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    redirect = _require_auth(request)
    if redirect:
        return redirect

    stats = await get_dashboard_stats(db)
    activity = get_activity()
    characters, _ = await search_characters(db, per_page=500)

    # ACP state
    acp_username = await get_crawl_status(db, "acp_username") or settings.admin_username
    acp_password = await get_crawl_status(db, "acp_password") or settings.admin_password
    acp_configured = bool(acp_username and acp_password)
    acp_last_sync = await get_crawl_status(db, "acp_last_sync")
    browser_sync_url = await get_crawl_status(db, "browser_sync_url") or ""

    # Banner album
    banner_album_url = await get_crawl_status(db, "banner_album_url") or "https://imagehut.ch/album/TWAI-BANNER-IMAGES.u6h"
    # Import cache state to show count
    from app.routes.character import _banner_cache
    banner_count = len(_banner_cache["urls"]) if _banner_cache["urls"] else 0

    return templates.TemplateResponse(request, "pages/admin.html", {
        "stats": stats,
        "activity": activity,
        "characters": characters,
        "acp_configured": acp_configured,
        "acp_username": acp_username or "",
        "acp_last_sync": acp_last_sync,
        "forum_base_url": settings.forum_base_url,
        "browser_sync_url": browser_sync_url,
        "banner_album_url": banner_album_url,
        "banner_count": banner_count,
    })


@router.get("/connections", response_class=HTMLResponse)
async def connections_page(
    request: Request,
    tab: str = "all",
    focus: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
):
    redirect = _require_auth(request)
    if redirect:
        return redirect

    characters = await get_all_characters(db)
    relationships = await get_all_relationships(db)
    groups = await get_characters_by_affiliation(db)
    stats = await get_dashboard_stats(db)
    activity = get_activity()

    return templates.TemplateResponse(request, "pages/connections.html", {
        "characters": characters,
        "relationships": relationships,
        "relationship_types": RELATIONSHIP_TYPES,
        "groups": groups,
        "stats": stats,
        "activity": activity,
        "tab": tab,
        "focus": focus,
    })


@router.get("/affiliations")
async def affiliations_redirect(tab: str = "affiliations"):
    return RedirectResponse(url=f"/connections?tab={tab}", status_code=302)


@router.get("/export/players", response_class=PlainTextResponse)
async def export_players(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    redirect = _require_auth(request)
    if redirect:
        return redirect

    players = await get_unique_players(db)
    return PlainTextResponse("\n".join(players))


@router.get("/export/characters", response_class=PlainTextResponse)
async def export_characters(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    redirect = _require_auth(request)
    if redirect:
        return redirect

    characters = await get_all_characters(db)
    return PlainTextResponse("\n".join(c.name for c in characters))


@router.get("/export/face-claims", response_class=PlainTextResponse)
async def export_face_claims(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    redirect = _require_auth(request)
    if redirect:
        return redirect

    claims = await get_all_claims(db)
    lines = [f"{c.name}\t{c.face_claim}" for c in claims if c.face_claim]
    return PlainTextResponse("\n".join(lines))


@router.get("/export/species", response_class=PlainTextResponse)
async def export_species(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    redirect = _require_auth(request)
    if redirect:
        return redirect

    claims = await get_all_claims(db)
    lines = [f"{c.name}\t{c.species}" for c in claims if c.species]
    return PlainTextResponse("\n".join(lines))


@router.get("/export/affiliations", response_class=PlainTextResponse)
async def export_affiliations(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    redirect = _require_auth(request)
    if redirect:
        return redirect

    affiliations = await get_unique_affiliations(db)
    return PlainTextResponse("\n".join(affiliations))


@router.get("/export/threads", response_class=PlainTextResponse)
async def export_threads(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    redirect = _require_auth(request)
    if redirect:
        return redirect

    threads, _ = await search_threads_global(db, per_page=10000)
    lines = [f"{t['char_name']}\t{t['title']}\t{t['char_category']}" for t in threads]
    return PlainTextResponse("\n".join(lines))


@router.get("/players", response_class=HTMLResponse)
async def players_page(
    request: Request,
    q: str | None = None,
    sort: str = "player",
    dir: str = "asc",
    page: int = 1,
    db: aiosqlite.Connection = Depends(get_db),
):
    redirect = _require_auth(request)
    if redirect:
        return redirect

    players, total = await search_players(db, q, sort, dir, page)
    stats = await get_dashboard_stats(db)
    activity = get_activity()

    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(request, "partials/player_table_rows.html", {
            "players": players,
            "total": total,
            "page": page,
            "per_page": 25,
            "q": q,
            "sort": sort,
            "dir": dir,
        })

    return templates.TemplateResponse(request, "pages/players.html", {
        "players": players,
        "total": total,
        "stats": stats,
        "activity": activity,
        "q": q,
        "sort": sort,
        "dir": dir,
        "page": page,
        "per_page": 25,
    })


@router.get("/player/{player_name}", response_class=HTMLResponse)
async def player_detail_page(
    request: Request,
    player_name: str,
    month: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
):
    redirect = _require_auth(request)
    if redirect:
        return redirect

    # Parse month filter (format: "YYYY-MM")
    month_start = None
    month_end = None
    if month:
        try:
            from datetime import datetime
            dt = datetime.strptime(month, "%Y-%m")
            month_start = dt.strftime("%Y-%m-01")
            if dt.month == 12:
                month_end = f"{dt.year + 1}-01-01"
            else:
                month_end = f"{dt.year}-{dt.month + 1:02d}-01"
        except ValueError:
            pass

    player = await get_player_detail(db, player_name, month_start, month_end)
    if not player:
        return HTMLResponse(status_code=404, content="Player not found")

    activity = get_activity()

    return templates.TemplateResponse(request, "pages/player_detail.html", {
        "player": player,
        "activity": activity,
        "month": month,
    })


# --- HTMX Partial Routes ---

@router.get("/htmx/characters", response_class=HTMLResponse)
async def htmx_characters(
    request: Request,
    q: str | None = None,
    affiliation: str | None = None,
    group: str | None = None,
    player: str | None = None,
    sort: str = "name",
    dir: str = "asc",
    page: int = 1,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    affiliations_list = affiliation.split(",") if affiliation else None
    characters, total = await search_characters(db, q, affiliations_list, group, player, sort, dir, page)
    return templates.TemplateResponse(request, "partials/character_table_rows.html", {
        "characters": characters,
        "total": total,
        "page": page,
        "per_page": 25,
        "q": q,
        "affiliation": affiliation,
        "group": group,
        "player": player,
        "sort": sort,
        "dir": dir,
    })


@router.get("/htmx/players", response_class=HTMLResponse)
async def htmx_players(
    request: Request,
    q: str | None = None,
    sort: str = "player",
    dir: str = "asc",
    page: int = 1,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    players, total = await search_players(db, q, sort, dir, page)
    return templates.TemplateResponse(request, "partials/player_table_rows.html", {
        "players": players,
        "total": total,
        "page": page,
        "per_page": 25,
        "q": q,
        "sort": sort,
        "dir": dir,
    })


@router.get("/htmx/activity", response_class=HTMLResponse)
async def htmx_activity(request: Request):
    activity = get_activity()
    return templates.TemplateResponse(request, "partials/activity_content.html", {
        "activity": activity,
    })


@router.get("/htmx/debug-log", response_class=HTMLResponse)
async def htmx_debug_log(request: Request):
    entries = get_debug_log()
    return templates.TemplateResponse(request, "partials/debug_log_content.html", {
        "entries": entries,
    })


@router.post("/htmx/debug-log/clear", response_class=HTMLResponse)
async def htmx_debug_log_clear(request: Request):
    clear_debug_log()
    return HTMLResponse('<div class="debug-entry"><span class="text-comment">Log cleared</span></div>')


@router.get("/htmx/stats", response_class=HTMLResponse)
async def htmx_stats(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    stats = await get_dashboard_stats(db)
    return templates.TemplateResponse(request, "partials/stats_values.html", {
        "stats": stats,
    })


@router.get("/htmx/overview-charts", response_class=HTMLResponse)
async def htmx_overview_charts(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    chart_data = await get_dashboard_chart_data(db)
    return templates.TemplateResponse(request, "partials/overview_charts.html", {
        "chart_data": chart_data,
    })


@router.get("/htmx/threads", response_class=HTMLResponse)
async def htmx_threads(
    request: Request,
    q: str | None = None,
    category: str | None = None,
    status: str | None = None,
    character_id: str | None = None,
    player: str | None = None,
    sort: str = "title",
    dir: str = "asc",
    page: int = 1,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    threads, total = await search_threads_global(db, q, category, status, character_id, player, sort, dir, page)
    return templates.TemplateResponse(request, "partials/thread_table_rows.html", {
        "threads": threads,
        "total": total,
        "page": page,
        "per_page": 25,
        "q": q,
        "category": category,
        "status": status,
        "character_id": character_id,
        "player": player,
        "sort": sort,
        "dir": dir,
    })


@router.get("/htmx/quotes", response_class=HTMLResponse)
async def htmx_quotes(
    request: Request,
    q: str | None = None,
    character_id: str | None = None,
    sort: str = "created_at",
    dir: str = "desc",
    page: int = 1,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    quotes, total = await search_quotes_global(db, q, character_id, sort, dir, page)
    return templates.TemplateResponse(request, "partials/quote_list_items.html", {
        "quotes": quotes,
        "total": total,
        "page": page,
        "per_page": 25,
        "q": q,
        "character_id": character_id,
        "sort": sort,
        "dir": dir,
    })


@router.get("/htmx/character/{character_id}/threads", response_class=HTMLResponse)
async def htmx_character_threads(
    request: Request,
    character_id: str,
    category: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    threads = await get_character_threads(db, character_id)
    return templates.TemplateResponse(request, "partials/character_threads_section.html", {
        "threads": threads,
        "category": category,
    })


@router.get("/htmx/character/{character_id}/quotes", response_class=HTMLResponse)
async def htmx_character_quotes(
    request: Request,
    character_id: str,
    q: str | None = None,
    page: int = 1,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    quotes = await get_all_quotes(db, character_id)
    if q:
        quotes = [quote for quote in quotes if q.lower() in quote.quote_text.lower()]
    total = len(quotes)
    per_page = 20
    start = (page - 1) * per_page
    quotes = quotes[start:start + per_page]
    return templates.TemplateResponse(request, "partials/character_quotes_section.html", {
        "quotes": quotes,
        "total": total,
        "q": q,
        "page": page,
        "per_page": per_page,
        "character_id": character_id,
    })


@router.post("/htmx/character/{character_id}/toggle-hidden", response_class=HTMLResponse)
async def htmx_toggle_hidden(
    request: Request,
    character_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    new_state = await toggle_character_hidden(db, character_id)
    if new_state is None:
        return HTMLResponse(status_code=404, content="Character not found")

    if new_state:
        return HTMLResponse(
            '<button class="btn btn-ghost btn-sm btn-hidden-active" '
            f'hx-post="/htmx/character/{character_id}/toggle-hidden" '
            'hx-target="#hide-toggle" hx-swap="innerHTML">'
            'Hidden</button>'
        )
    else:
        return HTMLResponse(
            '<button class="btn btn-ghost btn-sm" '
            f'hx-post="/htmx/character/{character_id}/toggle-hidden" '
            'hx-target="#hide-toggle" hx-swap="innerHTML">'
            'Hide</button>'
        )


@router.post("/htmx/character/{character_id}/approval-date", response_class=HTMLResponse)
async def htmx_set_approval_date(
    request: Request,
    character_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    form = await request.form()
    date_val = form.get("approval_date", "").strip() or None
    found = await set_approval_date(db, character_id, date_val)
    if not found:
        return HTMLResponse(status_code=404, content="Character not found")

    if date_val:
        return HTMLResponse(f'<span class="text-green">Approved: {date_val}</span>')
    else:
        return HTMLResponse('<span class="text-comment">Approval date cleared</span>')


@router.post("/htmx/bulk-approval-dates", response_class=HTMLResponse)
async def htmx_bulk_approval_dates(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    form = await request.form()
    raw = form.get("csv_data", "").strip()
    if not raw:
        return HTMLResponse('<span class="text-red">No data provided</span>')

    entries = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # Expect: "Character Name\tYYYY-MM-DD" or "Character Name,YYYY-MM-DD"
        parts = line.split("\t") if "\t" in line else line.rsplit(",", 1)
        if len(parts) >= 2:
            entries.append({"name": parts[-2].strip(), "approval_date": parts[-1].strip()})

    if not entries:
        return HTMLResponse('<span class="text-red">No valid entries found</span>')

    result = await set_approval_dates(db, entries)
    unmatched_msg = ""
    if result["unmatched"]:
        unmatched_msg = f'<br><span class="text-yellow">Unmatched: {", ".join(result["unmatched"])}</span>'
    return HTMLResponse(
        f'<span class="text-green">Updated {result["matched"]} characters</span>{unmatched_msg}'
    )


@router.post("/htmx/register", response_class=HTMLResponse)
async def htmx_register(
    request: Request,
    background_tasks: BackgroundTasks,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    form = await request.form()
    user_id = form.get("user_id", "").strip()
    if not user_id:
        return HTMLResponse('<span class="text-red">User ID is required</span>')

    existing = await get_character(db, user_id)
    if existing:
        return HTMLResponse(f'<span class="text-yellow">Already tracking {existing.name}</span>')

    background_tasks.add_task(register_character, user_id, settings.database_path)
    return HTMLResponse(f'<span class="text-green">Registration started for #{user_id}</span>')


@router.post("/htmx/crawl", response_class=HTMLResponse)
async def htmx_crawl(
    request: Request,
    background_tasks: BackgroundTasks,
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    form = await request.form()
    character_id = form.get("character_id", "").strip() or None
    crawl_type = form.get("crawl_type", "threads")

    if crawl_type in ("discover", "all-threads"):
        background_tasks.add_task(_crawl_all_characters)
    elif crawl_type == "all-profiles":
        background_tasks.add_task(crawl_all_profile_fields, settings.database_path)
    elif crawl_type == "sync-posts":
        background_tasks.add_task(sync_posts_from_acp, settings.database_path)
    elif crawl_type == "crawl-quotes":
        background_tasks.add_task(crawl_quotes_only, settings.database_path)
    elif character_id:
        if crawl_type == "threads":
            background_tasks.add_task(crawl_character_threads, character_id, settings.database_path)
        elif crawl_type == "profile":
            background_tasks.add_task(crawl_character_profile, character_id, settings.database_path)
        else:
            return HTMLResponse(f'<span class="text-red">Unknown crawl type: {crawl_type}</span>')
    else:
        return HTMLResponse('<span class="text-red">Character ID required for this crawl type</span>')

    return HTMLResponse(f'<span class="text-green">Crawl queued: {crawl_type}</span>')


@router.post("/htmx/acp-credentials", response_class=HTMLResponse)
async def htmx_save_acp_credentials(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    form = await request.form()
    username = form.get("acp_username", "").strip()
    password = form.get("acp_password", "").strip()

    if not username:
        return HTMLResponse('<span class="text-red">Username is required</span>')

    if not password:
        return HTMLResponse('<span class="text-red">Password is required</span>')

    await set_crawl_status(db, "acp_username", username)
    await set_crawl_status(db, "acp_password", password)

    return HTMLResponse(f'<span class="text-green">ACP credentials saved for {username}.</span>')


@router.post("/htmx/banner-album", response_class=HTMLResponse)
@router.post("/htmx/save-sync-url", response_class=HTMLResponse)
async def htmx_save_sync_url(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    form = await request.form()
    url = form.get("browser_sync_url", "").strip()
    await set_crawl_status(db, "browser_sync_url", url)
    return HTMLResponse(f'<span class="text-green">Saved</span>')


@router.post("/htmx/banner-album", response_class=HTMLResponse)
async def htmx_save_banner_album(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    form = await request.form()
    url = form.get("banner_album_url", "").strip()

    if not url:
        return HTMLResponse('<span class="text-red">Album URL is required</span>')

    await set_crawl_status(db, "banner_album_url", url)

    # Invalidate cache so next /api/banners call fetches from new URL
    from app.routes.character import _banner_cache
    _banner_cache["fetched_at"] = 0.0

    return HTMLResponse(f'<span class="text-green">Banner album URL saved. Next API call will re-scrape.</span>')


@router.post("/htmx/banner-refresh", response_class=HTMLResponse)
async def htmx_refresh_banners(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    from app.routes.character import _banner_cache, _fetch_all_banners, BANNER_ALBUM_URL_DEFAULT
    import httpx as _httpx

    album_url = await get_crawl_status(db, "banner_album_url") or BANNER_ALBUM_URL_DEFAULT
    try:
        urls = await _fetch_all_banners(album_url)
    except _httpx.HTTPError as e:
        return HTMLResponse(f'<span class="text-red">Fetch failed: {e}</span>')

    import time
    _banner_cache["urls"] = urls
    _banner_cache["album_url"] = album_url
    _banner_cache["fetched_at"] = time.time()

    return HTMLResponse(f'<span class="text-green">Cache refreshed — {len(urls)} banner images loaded.</span>')


@router.post("/htmx/acp-sync", response_class=HTMLResponse)
async def htmx_acp_sync(
    request: Request,
    background_tasks: BackgroundTasks,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    # Check if credentials are configured
    acp_user = await get_crawl_status(db, "acp_username") or settings.admin_username
    acp_pass = await get_crawl_status(db, "acp_password") or settings.admin_password
    if not acp_user or not acp_pass:
        return HTMLResponse('<span class="text-red">No ACP credentials configured — save them first</span>')

    background_tasks.add_task(sync_posts_from_acp, settings.database_path)
    return HTMLResponse('<span class="text-green">ACP post sync started — check activity indicator for progress</span>')


@router.post("/htmx/purge-recrawl", response_class=HTMLResponse)
async def htmx_purge_recrawl(
    request: Request,
    background_tasks: BackgroundTasks,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    await db.execute("DELETE FROM character_threads")
    await db.execute("DELETE FROM threads")
    await db.execute("DELETE FROM quotes")
    await db.execute("DELETE FROM quote_crawl_log")
    await db.execute("DELETE FROM posts")
    await db.commit()

    background_tasks.add_task(_crawl_all_characters)
    return HTMLResponse('<span class="text-green">Database purged. Re-crawling all characters (profile + threads + quotes).</span>')


@router.post("/htmx/nuke-rebuild", response_class=HTMLResponse)
async def htmx_nuke_rebuild(
    request: Request,
    background_tasks: BackgroundTasks,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    await db.execute("DELETE FROM character_threads")
    await db.execute("DELETE FROM threads")
    await db.execute("DELETE FROM quotes")
    await db.execute("DELETE FROM quote_crawl_log")
    await db.execute("DELETE FROM posts")
    await db.execute("DELETE FROM profile_fields")
    await db.execute("DELETE FROM characters")
    await db.commit()

    background_tasks.add_task(_crawl_all_characters)
    return HTMLResponse('<span class="text-green">Everything nuked. Re-crawling all user IDs from scratch.</span>')


# --- Relationships ---

@router.get("/relationships")
async def relationships_redirect(focus: str | None = None):
    url = "/connections?tab=relationships"
    if focus:
        url += f"&focus={focus}"
    return RedirectResponse(url=url, status_code=302)


@router.get("/api/relationships")
async def get_relationship_graph(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Returns graph data as JSON for Force-Graph."""
    characters = await get_all_characters(db)
    relationships = await get_all_relationships(db)
    affiliations = await get_unique_affiliations(db)

    connected_ids = set()
    for r in relationships:
        connected_ids.add(r.character_a_id)
        connected_ids.add(r.character_b_id)

    nodes = []
    for c in characters:
        node = {
            "id": c.id,
            "name": c.name,
            "affiliation": c.affiliation or "Unaffiliated",
            "connected": c.id in connected_ids,
        }
        avatar = getattr(c, "square_image", None) or c.avatar_url
        if avatar:
            node["avatar"] = avatar
        nodes.append(node)

    links = [
        {
            "source": r.character_a_id,
            "target": r.character_b_id,
            "type": r.relationship_type,
            "label": r.label or r.relationship_type,
        }
        for r in relationships
    ]
    return {"nodes": nodes, "links": links, "affiliations": affiliations}


@router.post("/htmx/relationship/add", response_class=HTMLResponse)
async def htmx_relationship_add(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    form = await request.form()
    char_a = form.get("character_a", "").strip()
    char_b = form.get("character_b", "").strip()
    rel_type = form.get("relationship_type", "other").strip()
    label = form.get("label", "").strip() or None

    if not char_a or not char_b:
        return HTMLResponse('<span class="text-red">Both characters are required</span>')
    if char_a == char_b:
        return HTMLResponse('<span class="text-red">Cannot create a relationship with the same character</span>')

    result = await create_relationship(db, char_a, char_b, rel_type, label)
    if result is None:
        return HTMLResponse('<span class="text-yellow">Relationship already exists</span>')

    relationships = await get_all_relationships(db)
    return templates.TemplateResponse(request, "partials/relationship_list.html", {
        "relationships": relationships,
    })


@router.post("/htmx/relationship/{rel_id}/edit", response_class=HTMLResponse)
async def htmx_relationship_edit(
    request: Request,
    rel_id: int,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    form = await request.form()
    rel_type = form.get("relationship_type", "other").strip()
    label = form.get("label", "").strip() or None

    await update_relationship(db, rel_id, rel_type, label)
    relationships = await get_all_relationships(db)
    return templates.TemplateResponse(request, "partials/relationship_list.html", {
        "relationships": relationships,
    })


@router.post("/htmx/relationship/{rel_id}/delete", response_class=HTMLResponse)
async def htmx_relationship_delete(
    request: Request,
    rel_id: int,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    await delete_relationship(db, rel_id)
    relationships = await get_all_relationships(db)
    return templates.TemplateResponse(request, "partials/relationship_list.html", {
        "relationships": relationships,
    })


@router.post("/htmx/relationships/seed", response_class=HTMLResponse)
async def htmx_relationships_seed(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    auth_err = _require_auth_htmx(request)
    if auth_err:
        return auth_err

    count = await seed_relationships_from_connections(db)
    return HTMLResponse(f'<span class="text-green">Imported {count} new relationship{"s" if count != 1 else ""} from connections data</span>')


@router.get("/htmx/relationship-list", response_class=HTMLResponse)
async def htmx_relationship_list(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    relationships = await get_all_relationships(db)
    return templates.TemplateResponse(request, "partials/relationship_list.html", {
        "relationships": relationships,
    })
