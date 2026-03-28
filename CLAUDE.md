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
- **Deployment:** Single instance on `imagehut.ch:8943` (VPSdime), Docker Compose, SQLite persisted to `./data/`
- **Version:** `app/config.py` → `APP_VERSION`
- **Proxy:** All server-side JCink requests route through a Cloudflare Worker (`cloudflare-worker/`) to avoid IP bans

## Architecture

```
Browser / CLI / Forum Theme JS
    │
    ▼
FastAPI (app/main.py)
├── Routes: character.py (REST API + webhooks), dashboard.py (HTML + HTMX partials), game.py (quote games)
├── Services: crawler.py (orchestration), fetcher.py (HTTP + CF Worker proxy), parser.py (HTML extraction), acp_client.py (ACP SQL dump)
├── Models: operations.py (DB CRUD), character.py (Pydantic schemas), dashboard_queries.py (search/pagination)
└── Database: database.py (SQLite schema, aiosqlite, WAL mode, busy_timeout)
```

### Data flow — ACP Sync (primary, one-click)

The ACP Sync is the primary data pipeline. One button click does everything:

```
Dashboard "ACP Sync" button
    │
    ▼
Phase 1: ACP Dump ──→ CF Worker ──→ JCink ACP ──→ 145 pages ──→ SQL file (~41MB)
    │
    ▼
Phase 2: Parse SQL ──→ Extract topics, posts (with bodies), forums, members
    │
    ▼
Phase 3: Match ──→ Cross-reference posts to tracked characters
    │
    ▼
Phase 4: DB Write ──→ Upsert threads, link characters, store posts
    │
    ▼
Phase 5: Quotes ──→ Extract dialog quotes directly from post bodies in SQL dump
    │
    ▼
Done (~80 seconds total)
```

**Quote extraction happens from the SQL dump itself** — no HTTP thread-page fetching required. The `extract_quotes_from_post_body()` function in `parser.py` parses bold/strong and color-styled dialog from raw post HTML stored in the dump.

### Other data paths

| Path | Purpose | Notes |
|---|---|---|
| **Browser Sync** | Profile data (avatars, custom fields, power grids) | Runs from user's browser, bypasses server IP |
| **Webhook** | Real-time updates on new posts/profile edits | Theme JS fires on form submit |
| **HTTP Quote Crawl (Legacy)** | Fetches thread pages via HTTP for quote extraction | Slow (~45min), available as manual fallback button |

### Cloudflare Worker Proxy

All server-side requests to JCink route through a Cloudflare Worker to avoid IP bans:

```
Server (VPSdime) ──→ CF Worker (cloudflare-worker.storycraftink-sys.workers.dev) ──→ JCink
```

- Config: `CF_WORKER_URL` and `CF_WORKER_KEY` in `docker-compose.yml`
- Worker code: `cloudflare-worker/worker.js`
- Setup guide: `cloudflare-worker/SETUP.md`
- The Worker streams response bodies (no buffering) to handle 41MB+ SQL files
- **Do NOT run Tor or similar on VPSdime** — it's forbidden by their TOS and will get the server suspended

## Development

```bash
# Run tests (asyncio_mode=auto, in-memory SQLite, mocked HTTP)
pytest

# Run server locally
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Run a specific test file or class
pytest tests/test_parser.py
pytest tests/test_acp_client.py::TestHtmlBodyParsing
```

## Key Files

| File | Purpose |
|---|---|
| `app/config.py` | Version, Pydantic settings (all env-var driven) |
| `app/main.py` | FastAPI entry, lifespan events, router mounting |
| `app/database.py` | SQLite schema, `init_db()`, `connect_db()` (WAL + busy_timeout) |
| `app/services/crawler.py` | Crawl orchestration — `sync_posts_from_acp()`, `process_acp_raw_data()`, `crawl_quotes_only()`, `crawl_character_threads()` |
| `app/services/acp_client.py` | ACP SQL dump client, SQL parser (`parse_sql_dump`, `_parse_sql_values`), record extractors |
| `app/services/parser.py` | HTML parsing — pagination, profiles, quotes (`extract_quotes_from_post_body`), posts, search results |
| `app/services/fetcher.py` | HTTP client (httpx), CF Worker proxy routing, auth, rate limiting |
| `app/services/scheduler.py` | Manual crawl trigger functions (no automatic scheduling) |
| `app/models/operations.py` | All database CRUD (upsert, link, query) |
| `app/routes/character.py` | REST API + webhook endpoint |
| `app/routes/dashboard.py` | HTMX dashboard routes + HTML rendering |
| `cloudflare-worker/worker.js` | CF Worker proxy — streams requests to JCink |
| `cli.py` | Rich-powered CLI client |

## JCink-Specific Knowledge

These are platform behaviors that have caused bugs — preserve this knowledge.

### SQL Dump Parsing

- JCink's ACP SQL dump uses `\'` (backslash-quote) for escaping single quotes in string values
- **Post bodies contain unescaped `'` in HTML attributes** (e.g. `border='0'`) — this is a JCink bug that causes the SQL parser to split rows into too many columns
- ~80% of post rows parse with wrong column counts due to this. The `extract_post_records()` and `extract_topic_records()` functions handle this by reading `thread_id` and `forum_id` from the END of the row (count-from-end workaround)
- **Do NOT replace `\'` with `'` before parsing** — it breaks quote delimiter detection in the CSV parser. The unescape happens after parsing, per-value.
- JCink move-redirect stubs have titles starting with `From:` — filter these out in `extract_topic_records()`
- The `post_topic_id` auto-detection can pick a boolean flag column (cardinality ~0.002). A sanity check rejects columns with <5% distinct values and falls back to the default (column 12).
- `parse_sql_dump()` runs in a thread executor (`run_in_executor`) to avoid blocking the async event loop during the ~8-second parse of 41MB

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
8. **~80% of post rows mis-parsed** — SQL parser can't handle unescaped HTML quotes in post bodies. Count-from-end workaround handles thread_id/forum_id but post body reconstruction uses lossy comma-joining.

### Docker/deployment

- No `.dockerignore` (`.git/`, `.env`, `__pycache__` may leak into images)
- Test deps (`pytest`) installed in production image
- `deploy.sh` uses `--no-cache` unconditionally
- Debug files (`chr4.html`, `debug_snapshot.html`, `jcink_crawler.zip`) tracked in git
