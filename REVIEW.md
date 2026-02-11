# Codebase Review

Comprehensive review of the jcink_crawler project — a FastAPI-based server-side web crawler and caching service for JCink forum data.

**Project stats:** ~70 files, ~2,400 lines of core logic, 194 tests across 14 test files.

---

## Architecture Overview

The project follows a clean layered architecture:

```
Routes (character.py, dashboard.py)
  → Services (crawler.py, parser.py, fetcher.py, scheduler.py)
    → Models/Operations (operations.py, character.py)
      → Database (database.py, aiosqlite)
```

Additionally: a Rich-powered CLI (`cli.py`), a Textual TUI (`tui.py`), Jinja2 web dashboard, and Docker deployment.

The architecture is sound and the separation of concerns is well-maintained across the codebase. The code is readable and consistently structured.

---

## Critical Issues

### 1. Unauthenticated crawl trigger endpoints (`app/routes/character.py:151-189`)

The `POST /api/crawl/trigger` endpoint accepts `all-threads`, `all-profiles`, and `discover` crawl types with no authentication. Anyone who can reach the service can trigger resource-intensive background operations that make hundreds of HTTP requests to JCink.

### 2. Wildcard CORS with credentials (`app/main.py:30-33`)

```python
allow_origins=["*"],
allow_credentials=True,
```

When credentials are enabled, Starlette reflects the requesting origin rather than sending `*`, effectively allowing any origin to make credentialed requests. Origins should be restricted to the actual embedding domain(s).

### 3. Hardcoded external hostname in multiple files

`cli.py:31`, `tui.py:357`, and `install.sh:84` all default to `http://imagehut.ch:8943` instead of `localhost`. This means:
- Running the CLI/TUI without configuring `CRAWLER_URL` sends commands to a remote host
- The install script health-checks a remote server instead of the local deployment
- `CLAUDE.md` documents `localhost:8943` as the default, contradicting the actual code

### 4. Authentication always "succeeds" (`app/services/fetcher.py:88-91`)

After checking for session cookies and finding none, the code still sets `_authenticated = True` and returns `True`. Failed logins are completely silent — the crawler proceeds as a guest while believing it is authenticated, potentially getting restricted content or stricter rate limits.

---

## High-Priority Issues

### 5. Dashboard password stored as base64, not hashed (`app/config.py:27`, `setup_dashboard.py`)

The dashboard password is base64-encoded (reversible encoding) rather than hashed (bcrypt/argon2). Anyone with access to `.env` can decode it instantly. Additionally, `setup_dashboard.py` uses `input()` instead of `getpass.getpass()`, echoing the password to the terminal.

### 6. Default secret key is well-known (`app/config.py:28`)

```python
dashboard_secret_key: str = "change-me-in-production"
```

If unset, session tokens are signed with a publicly known key, making them trivially forgeable.

### 7. No WAL mode or foreign key enforcement (`app/database.py`)

SQLite runs in default journal mode (poor read concurrency during writes) and does not enable `PRAGMA foreign_keys = ON`, making all `FOREIGN KEY` constraints decorative — the database silently accepts orphan records.

### 8. Silent exception swallowing (`app/models/operations.py:248-256`)

```python
except Exception:
    return False
```

`add_quote` catches all exceptions and returns `False` silently. Database errors, connection issues, and programming bugs are all discarded, making production debugging extremely difficult.

### 9. N+1 query in `get_all_characters` (`app/models/operations.py:44-73`)

For every character in the list, a separate `get_thread_counts` query is executed. With 50 characters, this produces 51 queries. A single query with `LEFT JOIN` and `GROUP BY` would eliminate this.

### 10. Raw SQL in route handler (`app/routes/character.py:192-233`)

The `/api/status` endpoint contains five raw SQL queries directly in the route handler, violating the project's own pattern where all database operations live in `app/models/operations.py`.

---

## Medium-Priority Issues

### 11. No retry logic in HTTP fetcher (`app/services/fetcher.py`)

A single HTTP failure returns `None` with no retry mechanism. For a crawler fetching hundreds of pages, any transient network blip silently drops data.

### 12. Race condition on `avatar_cache` (`app/services/crawler.py:131, 166-174`)

The avatar cache dict is shared across concurrent `_process_thread` coroutines via `asyncio.gather`. Multiple coroutines can simultaneously check the cache and all proceed to fetch the same profile, defeating the cache's purpose.

### 13. Inconsistent commit behavior in operations.py

Some functions call `await db.commit()` internally (`upsert_character` at line 95, `update_character_crawl_time` at line 109) while others do not (`upsert_thread`, `link_character_thread`, `mark_thread_quote_scraped`). The inconsistency makes it unclear who is responsible for committing transactions.

### 14. Hardcoded excluded forum names in parser (`app/services/parser.py:111`)

```python
excluded_names = {"Guidebook", "OOC Archives"}
```

Forum names are hardcoded while forum IDs are configurable. Renaming these forums on JCink would silently break filtering.

### 15. No connection pooling (`app/database.py:7-14`)

Every API request creates a new SQLite connection and closes it afterward. The crawler also opens multiple separate connections per crawl. A connection pool or shared connection would reduce overhead.

### 16. Docker container runs as root

The Dockerfile does not create or switch to a non-root user. A compromised application would have root privileges inside the container.

### 17. Test dependencies in production image

`pytest` and `pytest-asyncio` are in `requirements.txt` and installed in the production Docker image, increasing attack surface and image size.

### 18. No `.dockerignore`

Without a `.dockerignore`, the build context may include `.git/`, `.env`, `__pycache__/`, and `data/`, potentially baking secrets into image layers.

---

## Low-Priority Issues

### 19. Quote endpoints return 200 for nonexistent characters (`app/routes/character.py:121-136`)

Unlike other character endpoints that return 404 for missing characters, the quote endpoints return `null`/`[]` with a 200 status, making it impossible for clients to distinguish "character not found" from "no quotes".

### 20. Dead schema column (`app/database.py:60`)

`threads.is_user_last_poster` is defined in the schema but never used — the operations code reads this field from `character_threads` instead.

### 21. `print()` used throughout instead of logging

Every module uses `print(f"[Module] ...")` for logging. There is no log level control, structured logging, timestamps, or ability to filter/route logs.

### 22. `_format_time` timezone handling is fragile (`cli.py:423-440`, `tui.py:40-50`)

Mixing timezone-aware and naive datetimes based on whether `tzinfo` is present. Silent `except Exception` fallbacks mask errors.

### 23. F-string SQL column interpolation (`app/models/operations.py:104-108`)

```python
f"UPDATE characters SET {column} = CURRENT_TIMESTAMP ..."
```

Currently safe (binary choice between two hardcoded values), but the pattern of building SQL via f-strings is fragile and would become a SQL injection vector if the guard logic changed.

### 24. Scheduler has no mutual exclusion between jobs (`app/services/scheduler.py`)

Thread crawl, profile crawl, and discovery jobs can run concurrently, competing for HTTP semaphore slots and modifying the same database tables simultaneously.

### 25. `discover_characters()` has zero test coverage

The entire auto-discovery function (~50 lines including `parse_member_list` and `parse_member_list_pagination`) is untested.

---

## Test Suite Assessment

**Overall rating: B+**

### Strengths
- 194 tests with good breadth across all layers
- Parser tests are excellent — realistic HTML fixtures, edge cases (curly quotes, guest posters, truncation)
- Database operation tests are thorough — upsert semantics, deduplication, isolation
- Good mix of unit tests and integration tests (real SQLite, real FastAPI ASGI transport)
- Async testing correctly configured with `asyncio_mode = auto`

### Gaps
- **Untested code:** `discover_characters()`, `parse_member_list()`, `parse_member_list_pagination()`, `extract_thread_authors()`, `fetch_pages_concurrent()`
- **Shallow API tests:** Most API endpoint tests only cover the 404/empty case, not the happy path with populated data
- **No validation tests:** No tests for malformed input, SQL injection resistance, or boundary conditions
- **Duplicate autouse fixtures:** `conftest.py` defines `setup_db` as autouse, and 4 test files define their own `fresh_db` also as autouse — double initialization per test
- **Global state in test_fetcher.py:** Tests directly mutate `fetcher._client` and `fetcher._authenticated` without fixture-based cleanup
- **Deprecated API:** `tempfile.mktemp()` is used in conftest.py (deprecated due to TOCTOU race)

### Missing test categories
- End-to-end tests (crawl → parse → store → serve)
- Concurrency/race condition tests
- Performance/load tests
- Malformed HTML input tests

---

## Recommendations (prioritized)

1. **Add authentication** to crawl trigger and register endpoints (or at minimum, rate limiting)
2. **Restrict CORS origins** to the actual embedding domain(s)
3. **Fix hardcoded hostnames** — replace `imagehut.ch` with `localhost` in cli.py, tui.py, and install.sh
4. **Fix authentication logic** in fetcher.py — return `False` when login verification fails
5. **Enable WAL mode and foreign keys** in database.py
6. **Hash dashboard password** with bcrypt instead of base64
7. **Generate a random secret key** on first startup if not configured
8. **Add retry logic** to the HTTP fetcher for transient failures
9. **Move SQL out of route handlers** into operations.py
10. **Fix the N+1 query** in `get_all_characters` with a JOIN-based approach
11. **Add logging** via Python's `logging` module with configurable levels
12. **Add `.dockerignore`** and run as non-root in Docker
13. **Separate test dependencies** from production requirements
14. **Add tests** for `discover_characters`, `parse_member_list`, and `fetch_pages_concurrent`
15. **Consolidate duplicate test fixtures** and fix global state management in tests
