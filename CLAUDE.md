# CLAUDE.md — Instructions for AI Agents

## Version Bump Requirement (MANDATORY)

**Every branch/PR that changes application code MUST bump `APP_VERSION` in `app/config.py`.**

- The version follows `MAJOR.MINOR.PATCH` (e.g. `3.9.18`)
- Increment the **patch** (3rd digit) for bug fixes, small features, and scaffolding changes
- Increment the **minor** (2nd digit) for new features or significant behavior changes
- Increment the **major** (1st digit) only for breaking changes

If you are making any commit that touches code under `app/`, `scripts/`, `cli.py`, or `templates/`, you MUST also bump the version. Do not forget this. It is not optional.

## Project Overview

FastAPI web crawler and caching service for a single JCink forum (`therewasanidea.jcink.net`). Replaces heavy client-side JS scraping with server-side crawling — serves profile fields, categorized threads, last-poster info, and dialog quotes via a REST API and an HTMX dashboard.

- **Stack:** Python 3.11+, FastAPI, aiosqlite (SQLite), httpx, BeautifulSoup4, Jinja2/HTMX, Docker
- **Deployment:** Single instance on `imagehut.ch:8943`, Docker Compose, SQLite persisted to `/opt/jcink-crawler/data/`
- **Version:** `app/config.py` → `APP_VERSION`

## Architecture

```
Browser / CLI / Forum Theme JS
    │
    ▼
FastAPI (app/main.py)
├── Routes: character.py (REST API + webhooks), dashboard.py (HTML + HTMX partials), game.py (quote games)
├── Services: crawler.py (orchestration), fetcher.py (HTTP + auth), parser.py (HTML extraction), scheduler.py (periodic jobs)
├── Models: operations.py (DB CRUD), character.py (Pydantic schemas), dashboard_queries.py (search/pagination)
└── Database: database.py (SQLite schema, aiosqlite)
```

### Data flow

1. **Webhook** — Forum theme JS sends `POST /api/webhook/activity` on new_post/new_topic/profile_edit
2. **Background task** queued with configurable delay (`webhook_crawl_delay_seconds`)
3. **Crawler** fetches JCink pages → **Parser** extracts data → **Operations** writes to SQLite
4. **Dashboard/API** serves cached data from SQLite
5. **Scheduler** runs periodic full crawls as a safety net

### Three crawl triggers

| Trigger | Entry point | Notes |
|---|---|---|
| Webhook (real-time) | `POST /api/webhook/activity` | Theme JS fires on form submit |
| Scheduled (periodic) | `scheduler.py` jobs | Threads 60min, profiles 24h, quotes 30min |
| Manual (dashboard/CLI) | `POST /api/crawl/trigger` | Admin-triggered |

## Development

```bash
# Run tests (194+ tests, asyncio_mode=auto, in-memory SQLite, mocked HTTP)
pytest

# Run server locally
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Run a specific test file or class
pytest tests/test_parser.py
pytest tests/test_operations.py::TestAddQuote
```

## Key Files

| File | Purpose |
|---|---|
| `app/config.py` | Version, Pydantic settings (all env-var driven) |
| `app/main.py` | FastAPI entry, lifespan events, router mounting |
| `app/database.py` | SQLite schema, `init_db()` |
| `app/services/crawler.py` | Crawl orchestration — `crawl_character_threads()`, `crawl_single_thread()`, `crawl_quotes_only()`, `crawl_recent_threads()` |
| `app/services/parser.py` | HTML parsing — pagination, profiles, quotes, posts, search results |
| `app/services/fetcher.py` | HTTP client (httpx), auth, rate limiting, concurrency semaphore |
| `app/services/scheduler.py` | APScheduler periodic jobs |
| `app/models/operations.py` | All database CRUD (upsert, link, query) |
| `app/routes/character.py` | REST API + webhook endpoint |
| `app/routes/dashboard.py` | HTMX dashboard routes + HTML rendering |
| `cli.py` | Rich-powered CLI client |

## JCink-Specific Knowledge

These are platform behaviors that have caused bugs — preserve this knowledge.

### Pagination

- JCink uses `&st=N` query params for pagination (not `&page=N`)
- **Posts per page is variable** — typically 15, but can be configured per-forum. Never hardcode a step size.
- `parse_thread_pagination()` returns `(max_st, [list of all st offsets])` extracted from actual pagination links
- Always iterate over the real offsets, never `range(25, max_st+1, 25)` or any hardcoded step

### Form submissions

- JCink (IPB 1.3) puts `act`, `CODE`, `f`, `t` in **hidden `<input>` fields**, NOT in the form action URL
- `form.action` is just `https://domain/index.php` with no query params
- Theme webhook JS must check hidden fields first, URL second (see `getParam()` helper in wrapper.html)

### Search results

- JCink's "posts by user" search shows the user's own last post in the "Last Post" column, NOT the thread's actual last poster
- For accurate last-poster info, always fetch the real last page of the thread

### Board messages

- JCink returns "Board Message" pages for rate limiting, expired sessions, etc.
- The crawler checks `is_board_message()` and retries after re-authentication

## Known Issues & Technical Debt

### Bugs to fix

1. **Auth always "succeeds"** — `fetcher.py:88-91` sets `_authenticated = True` even when login verification fails
2. **Quote endpoints return 200 for missing characters** — should return 404 like other character endpoints
3. **Board message break discards fetched pages** — `crawler.py` search pagination breaks on board message, discarding already-fetched concurrent results
4. **Dead schema column** — `threads.is_user_last_poster` exists in schema but is never read (operations use `character_threads.is_user_last_poster`)

### Code quality

5. **Silent exception swallowing** — `add_quote` in operations.py catches all exceptions and returns `False`
6. **Inconsistent commit behavior** — some operations.py functions call `db.commit()` internally, others don't
7. **Raw SQL in route handler** — `/api/status` has 5 raw queries in the route instead of operations.py
8. **Race condition on `avatar_cache`** — shared dict across concurrent coroutines causes duplicate fetches
9. **No WAL mode or foreign key enforcement** in database.py
10. **Scheduler jobs can overlap** — thread/profile/discovery jobs run concurrently, competing for resources

### Test suite (B+ overall)

- **Strengths:** Good breadth, realistic HTML fixtures, proper async testing
- **Gaps:** `discover_characters()`, `parse_member_list()`, `fetch_pages_concurrent()` untested; shallow API happy-path coverage; duplicate autouse fixtures in conftest vs test files
- **Weak assertions** in test_cli.py (OR-conditions that pass trivially)

### Docker/deployment

- Container runs as root (no non-root user in Dockerfile)
- No `.dockerignore` (`.git/`, `.env`, `__pycache__` may leak into images)
- Test deps (`pytest`) installed in production image
- `deploy.sh` uses `--no-cache` unconditionally
