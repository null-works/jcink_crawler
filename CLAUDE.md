# JCink Crawler

Server-side web crawler and caching service for JCink forum data.
Companion service to `jcink_audio` — follows identical architectural patterns.

## Architecture

- **FastAPI** + **uvicorn** web server (port 8944 external, 8000 internal)
- **aiosqlite** for caching crawled data
- **httpx** + **BeautifulSoup4** for fetching and parsing JCink pages
- **Playwright** for JS-rendered profile pages (power grid extraction)
- **APScheduler** for periodic crawl jobs
- **Jinja2** + **HTMX** + **Chart.js** for the web dashboard
- **Docker** deployment

## What It Does

Replaces heavy client-side JS scraping with server-side crawling:
- Crawls character profiles for custom field data (58+ fields)
- Tracks threads per character (ongoing/comms/complete/incomplete) by forum ID
- Determines last poster per thread (fetches thread pages, handles pagination)
- Extracts dialog quotes from character posts (bold text matching `"..."` pattern)
- Extracts power grid stats (INT/STR/SPD/DUR/PWR/CMB on 1–7 scale) via Playwright
- Tracks individual post dates for activity analysis
- Syncs data from JCink ACP SQL dumps for accurate post dates
- Serves all data via REST API for instant client-side consumption
- Full web dashboard with character browser, thread browser, player analytics, and admin panel

## Forum Config

- Base URL: `https://therewasanidea.jcink.net`
- Forum IDs: Complete=49, Incomplete=59, Comms=31
- Excluded forums configured in docker-compose.yml (28 excluded by default)

## Key Files

### Core
- `app/main.py` — FastAPI app entry point, lifespan events (DB init, scheduler start/stop)
- `app/config.py` — Pydantic settings with env var mapping, version/build timestamp
- `app/database.py` — SQLite schema (8 tables, 6 indices)

### Models
- `app/models/character.py` — Pydantic response models (CharacterSummary, ClaimsSummary, ThreadInfo, Quote, etc.)
- `app/models/operations.py` — Database CRUD (upsert_character, get_character, add_quote, replace_thread_posts, etc.)
- `app/models/dashboard_queries.py` — Complex search/filter/aggregation queries for dashboard views

### Routes
- `app/routes/character.py` — REST API endpoints
- `app/routes/dashboard.py` — Dashboard HTML routes + HTMX partial endpoints

### Services
- `app/services/crawler.py` — Core crawl orchestration (profiles, threads, quotes, ACP sync)
- `app/services/parser.py` — HTML parsing with BeautifulSoup (700+ lines)
- `app/services/fetcher.py` — HTTP client abstraction (httpx + Playwright for JS rendering)
- `app/services/scheduler.py` — APScheduler periodic jobs
- `app/services/acp_client.py` — JCink ACP SQL dump client for accurate post date tracking
- `app/services/activity.py` — In-memory crawl activity state for live dashboard indicator

### Web Assets
- `app/static/css/dracula.css` — Color scheme variables (Dracula dark theme)
- `app/static/css/components.css` — Component styles (buttons, cards, tables, badges, power grid, etc.)
- `app/static/css/layout.css` — Grid/flexbox page structure
- `app/static/js/app.js` — Chart.js integration, HTMX enhancements
- `app/templates/` — 22 Jinja2 templates (base, 9 pages, 3 components, 9 HTMX partials)

### Other
- `cli.py` — Rich-powered CLI client
- `tui.py` — TUI dashboard
- `setup_dashboard.py` — Generate secure dashboard secret key

## Running

```bash
docker compose up --build
```

## Testing

```bash
pytest
```

## CSS Versioning

Static CSS files use query-string cache busting (`?v=N`) in `base.html`.
When editing CSS, bump the version number on the corresponding `<link>` tag.
Current versions: `dracula.css?v=12`, `components.css?v=15`, `layout.css?v=13`.

## CLI

The CLI talks to the running service over HTTP. Rich-powered tables and live dashboard.

```bash
# Point at a different URL (default: http://localhost:8943)
export CRAWLER_URL=http://localhost:8943

# Service status
python cli.py status

# Register a character by JCink user ID
python cli.py register 42

# List all tracked characters with thread counts
python cli.py characters

# Detailed character view (profile fields, threads, quotes)
python cli.py character 42

# Thread list with categories
python cli.py threads 42
python cli.py threads 42 --category complete

# Quotes
python cli.py quotes 42              # List all
python cli.py quotes 42 --random     # One random quote
python cli.py quotes 42 --limit 50   # Show more

# Manually trigger a crawl
python cli.py crawl 42 --type threads
python cli.py crawl 42 --type profile

# Live auto-refreshing dashboard
python cli.py watch
python cli.py watch --interval 10
```

Inside Docker:
```bash
docker exec -it jcink-crawler python cli.py status
docker exec -it jcink-crawler python cli.py register 42
docker exec -it jcink-crawler python cli.py watch
```

## Dashboard

Full web dashboard at `/dashboard` (dark Dracula theme, HTMX-powered):

- **Overview** — Stats cards, post-activity line graphs (Chart.js), live crawl indicator
- **Characters** — Browse/filter/sort all characters (by affiliation, group, player)
- **Character Detail** — Profile fields, power grid bars, categorized threads, quotes
- **Threads** — Global thread browser with category/status/character/player filters
- **Quotes** — Searchable quote browser
- **Players** — Player list with character counts, thread counts, activity status
- **Player Detail** — Per-month activity timeline
- **Admin** — Registration, crawl triggers, ACP sync credentials and controls
- **Login** — Optional session auth (if `DASHBOARD_PASSWORD_B64` is set)

HTMX partials auto-refresh stats (30s) and charts (60s). Tables use HTMX for pagination.

## API Endpoints

### REST API
- `GET /health` — Health check
- `GET /api/status` — Service stats (character/thread/quote counts, last crawl times, current activity)
- `GET /api/characters` — List all tracked characters with thread counts
- `GET /api/claims` — Bulk claims data (all characters with face_claim, species, codename, alias, affiliation, connections, thread_counts)
- `GET /api/characters/fields?ids=42,55&fields=square_image,short_quote` — Batch profile fields for multiple characters
- `GET /api/character/{id}` — Full character profile + threads + fields
- `GET /api/character/{id}/threads` — Categorized thread list
- `GET /api/character/{id}/thread-counts` — Lightweight counts only
- `GET /api/character/{id}/quote` — Random quote
- `GET /api/character/{id}/quotes` — All quotes
- `GET /api/character/{id}/quote-count` — Total quote count
- `POST /api/character/register` — Register a character for tracking
- `POST /api/crawl/trigger` — Manually trigger a crawl (threads, profile, discover, all-threads, all-profiles, sync-posts, crawl-quotes)
- `POST /api/webhook/activity` — Theme webhook for real-time updates (new_post, new_topic, profile_edit)

### Dashboard HTML Routes
- `GET /dashboard` — Overview page
- `GET /characters` — Character list
- `GET /character/{id}` — Character detail
- `GET /threads` — Thread browser
- `GET /quotes` — Quote browser
- `GET /players` — Player list
- `GET /player/{name}` — Player detail
- `GET /admin` — Admin panel
- `GET /login`, `POST /login`, `GET /logout` — Session auth

### HTMX Partials
- `GET /htmx/characters`, `/htmx/players`, `/htmx/threads`, `/htmx/quotes` — Table rows
- `GET /htmx/activity`, `/htmx/stats`, `/htmx/overview-charts` — Dashboard widgets
- `GET /htmx/character/{id}/threads`, `/htmx/character/{id}/quotes` — Detail sections
- `POST /htmx/register`, `/htmx/crawl`, `/htmx/acp-credentials`, `/htmx/acp-sync` — Admin actions

## Database Schema

8 tables:
1. **characters** — Core entity (id, name, profile_url, group_name, avatar_url, crawl timestamps)
2. **profile_fields** — Key/value store for 58+ custom fields (UNIQUE character_id + field_key)
3. **threads** — Thread metadata (id, title, url, forum_id, category, last_poster info)
4. **character_threads** — Many-to-many with category and post_count
5. **quotes** — Dialog quotes (UNIQUE character_id + quote_text)
6. **quote_crawl_log** — Tracks which threads have been quote-scraped
7. **posts** — Individual post records (character_id, thread_id, post_date) for activity tracking
8. **crawl_status** — Key/value store for crawl state (ACP credentials, last sync time)

## Power Grid

6-stat system (Intelligence, Strength, Speed, Durability, Energy Projection, Fighting Skills) on a 1–7 scale.

**Extraction pipeline:**
1. Profile page fetched via **Playwright** (waits for `.profile-stat` selector)
2. Parser reads `.profile-stat-fill[data-value]` for numeric values
3. If not found on profile, falls back to application thread (`.sa-n`/`.sa-o`/`.sa-q` elements, converts width % to 1–7 scale)
4. Stored as `profile_fields` with keys `"power grid - {int|str|spd|dur|pwr|cmb}"`

**Display:** Inline colored bars on character detail header, absolutely positioned at card center.

## ACP Sync

Pulls accurate post dates from JCink ACP SQL dumps instead of scraping HTML.

**Workflow:** Login to ACP → request MySQL dump (paginated) → parse `REPLACE INTO` statements → extract posts/topics/members → update database with accurate dates.

Admin panel provides credential input and manual trigger. Configurable interval via `ACP_SYNC_INTERVAL_MINUTES` (0 = disabled).

## Known Considerations

This is a single-deployment service for `imagehut.ch` targeting one JCink forum.
Many "best practice" concerns (scalability, hardcoded values, connection pooling) are
intentionally accepted given that context.

**Unauthenticated API endpoints:** The crawl trigger (`POST /api/crawl/trigger`) and
register (`POST /api/character/register`) endpoints have no authentication. This is a
known tradeoff — the port is not publicly advertised and the semaphore caps concurrent
requests, so the blast radius is low. If unexpected crawl activity shows up in the
dashboard NOC, revisit this by either adding auth or binding the port to `127.0.0.1`
behind a reverse proxy.

**CORS is wide open:** `allow_origins=["*"]` with `allow_credentials=True`. Acceptable
for now since the API serves an embedded widget on a known site. Tighten if the dashboard
auth ever matters more.

**Dashboard secret key:** Defaults to `"change-me-in-production"` in `config.py`. Run
`python setup_dashboard.py` on the host (not inside Docker) to generate a real one.
Not urgent unless dashboard auth is being relied on for something sensitive.

## Learned Patterns & Gotchas

**Power grid centering:** Flexbox `flex: 1` with `justify-content: center` does NOT
visually center the power grid in the card because the left side (avatar + info) is much
wider than the right side (buttons), skewing the available gap. Solution: absolute
positioning at `left: 50%; transform: translateX(-50%)` to center relative to the card
itself, not the remaining flex gap. Mobile breakpoint reverts to `position: static`.

**JCink search cooldown:** JCink throttles search requests. The crawler detects cooldown
responses and retries with exponential backoff rather than failing.

**Playwright fallback:** Playwright is only used for power grid extraction (JS-rendered
profile stats). If Playwright fails, the system falls back to httpx. The application
thread fallback provides a secondary extraction path if the profile page doesn't have
stats rendered.

**Post date accuracy:** HTML-scraped post dates use relative formats ("Today", "Yesterday")
which are unreliable. ACP SQL dumps provide Unix timestamps for accurate dates. The system
purges stale NULL-date posts on startup to keep the activity data clean.

**CSS cache busting:** Static CSS uses `?v=N` query strings. Always bump the version
when editing CSS or users will see stale styles. The `APP_BUILD_TIME` in config.py also
serves as a cache key for dynamic content.

**Activity bar overlap:** The fixed-position activity bar at the bottom of the dashboard
can overlap content. The version tag positioning needed explicit margin to avoid being
hidden behind it.
