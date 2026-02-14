import pathlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import aiosqlite

from app.database import get_db
from app.config import settings, APP_VERSION
from app.models import (
    get_character,
    get_all_characters,
    get_character_threads,
    get_profile_fields,
    get_all_quotes,
    search_characters,
    search_threads_global,
    search_quotes_global,
    get_unique_affiliations,
    get_unique_groups,
    get_unique_players,
    search_players,
    get_player_detail,
    get_dashboard_stats,
    get_dashboard_chart_data,
)
from app.services import crawl_character_threads, crawl_character_profile, register_character
from app.services.crawler import discover_characters
from app.services.scheduler import _crawl_all_threads, _crawl_all_profiles
from app.services.activity import get_activity

router = APIRouter()

TEMPLATES_DIR = pathlib.Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.globals["app_version"] = APP_VERSION

COOKIE_NAME = "watcher_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


# --- Jinja2 custom filters ---

def format_time(ts) -> str:
    """Format a timestamp as relative time."""
    if not ts:
        return "Never"
    try:
        if isinstance(ts, str):
            ts_clean = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts_clean)
        else:
            dt = ts
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
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

    total_quotes = len(quotes)
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

    return templates.TemplateResponse(request, "pages/admin.html", {
        "stats": stats,
        "activity": activity,
        "characters": characters,
    })


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
    db: aiosqlite.Connection = Depends(get_db),
):
    redirect = _require_auth(request)
    if redirect:
        return redirect

    player = await get_player_detail(db, player_name)
    if not player:
        return HTMLResponse(status_code=404, content="Player not found")

    activity = get_activity()

    return templates.TemplateResponse(request, "pages/player_detail.html", {
        "player": player,
        "activity": activity,
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

    if crawl_type == "discover":
        background_tasks.add_task(discover_characters, settings.database_path)
    elif crawl_type == "all-threads":
        background_tasks.add_task(_crawl_all_threads)
    elif crawl_type == "all-profiles":
        background_tasks.add_task(_crawl_all_profiles)
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
