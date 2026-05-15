import pathlib
from contextlib import asynccontextmanager

import aiosqlite
from app.config import settings

DATABASE_PATH = settings.database_path

# Default busy timeout in milliseconds — how long SQLite waits for a lock
# before raising "database is locked".  5 seconds is enough for the brief
# writes this application performs.
BUSY_TIMEOUT_MS = 30000  # 30s — covers long-running batched writes (ACP sync)


@asynccontextmanager
async def connect_db(path: str | None = None):
    """Open a database connection with WAL mode and busy timeout.

    Use as ``async with connect_db(db_path) as db: ...``

    Every production call site should use this instead of raw
    ``aiosqlite.connect()`` so that concurrent writers wait rather
    than immediately failing with "database is locked".
    """
    db = await aiosqlite.connect(path or DATABASE_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    try:
        yield db
    finally:
        await db.close()


async def get_db():
    """Get database connection (FastAPI dependency)."""
    async with connect_db() as db:
        yield db


async def init_db():
    """Initialize database tables."""
    db_dir = pathlib.Path(DATABASE_PATH).parent
    try:
        db_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        raise RuntimeError(
            f"Cannot create database directory '{db_dir}'. "
            f"If running in Docker, ensure the host volume is writable by UID 1000: "
            f"sudo chown 1000:1000 ./data"
        )
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Enable WAL mode for better concurrent read performance
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        # Enforce foreign key constraints
        await db.execute("PRAGMA foreign_keys=ON")

        # Characters - the core entity (JCink user accounts that are IC characters)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS characters (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                profile_url TEXT NOT NULL,
                group_name TEXT,
                avatar_url TEXT,
                last_profile_crawl TIMESTAMP,
                last_thread_crawl TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Profile fields - key/value store for all 58+ custom profile fields
        await db.execute("""
            CREATE TABLE IF NOT EXISTS profile_fields (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id TEXT NOT NULL,
                field_key TEXT NOT NULL,
                field_value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (character_id) REFERENCES characters(id),
                UNIQUE(character_id, field_key)
            )
        """)

        # Threads - tracked per character with forum categorization
        await db.execute("""
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                forum_id TEXT,
                forum_name TEXT,
                category TEXT NOT NULL DEFAULT 'ongoing',
                last_poster_id TEXT,
                last_poster_name TEXT,
                last_poster_avatar TEXT,
                is_user_last_poster INTEGER DEFAULT 0,
                last_crawled TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Character-thread association (many-to-many)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS character_threads (
                character_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'ongoing',
                is_user_last_poster INTEGER DEFAULT 0,
                PRIMARY KEY (character_id, thread_id),
                FOREIGN KEY (character_id) REFERENCES characters(id),
                FOREIGN KEY (thread_id) REFERENCES threads(id)
            )
        """)

        # Quotes - every qualifying dialog quote per character
        await db.execute("""
            CREATE TABLE IF NOT EXISTS quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id TEXT NOT NULL,
                quote_text TEXT NOT NULL,
                source_thread_id TEXT,
                source_thread_title TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (character_id) REFERENCES characters(id),
                FOREIGN KEY (source_thread_id) REFERENCES threads(id),
                UNIQUE(character_id, quote_text)
            )
        """)

        # Track which threads have been quote-scraped
        await db.execute("""
            CREATE TABLE IF NOT EXISTS quote_crawl_log (
                thread_id TEXT NOT NULL,
                character_id TEXT NOT NULL,
                crawled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (thread_id, character_id)
            )
        """)

        # Individual post records — tracks who posted where and when
        await db.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                post_date TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (character_id) REFERENCES characters(id),
                FOREIGN KEY (thread_id) REFERENCES threads(id)
            )
        """)

        # User activity - tracks when users were last seen for "online recently"
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_activity (
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                last_seen TIMESTAMP NOT NULL,
                source TEXT NOT NULL DEFAULT 'webhook',
                PRIMARY KEY (user_id)
            )
        """)

        # Crawl status - track overall crawl state
        await db.execute("""
            CREATE TABLE IF NOT EXISTS crawl_status (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Profile changes — audit log for field edits
        await db.execute("""
            CREATE TABLE IF NOT EXISTS profile_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id TEXT NOT NULL,
                field_key TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                changed_at TEXT NOT NULL,
                dismissed INTEGER DEFAULT 0,
                FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_profile_changes_char
            ON profile_changes(character_id)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_profile_changes_date
            ON profile_changes(changed_at)
        """)

        # Relationships - character-to-character connections
        await db.execute("""
            CREATE TABLE IF NOT EXISTS relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_a_id TEXT NOT NULL,
                character_b_id TEXT NOT NULL,
                relationship_type TEXT NOT NULL DEFAULT 'other',
                label TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (character_a_id) REFERENCES characters(id) ON DELETE CASCADE,
                FOREIGN KEY (character_b_id) REFERENCES characters(id) ON DELETE CASCADE,
                UNIQUE(character_a_id, character_b_id)
            )
        """)

        # Indexes
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_profile_fields_character
            ON profile_fields(character_id)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_character_threads_character
            ON character_threads(character_id)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_character_threads_thread
            ON character_threads(thread_id)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_quotes_character
            ON quotes(character_id)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_threads_category
            ON threads(category)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_posts_character_date
            ON posts(character_id, post_date)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_posts_thread
            ON posts(thread_id)
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_activity_last_seen
            ON user_activity(last_seen)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_relationships_a
            ON relationships(character_a_id)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_relationships_b
            ON relationships(character_b_id)
        """)

        # Add post_count to character_threads if it doesn't exist
        try:
            await db.execute("ALTER TABLE character_threads ADD COLUMN post_count INTEGER DEFAULT 0")
        except Exception:
            pass  # Column already exists

        # Add hidden flag to characters if it doesn't exist
        try:
            await db.execute("ALTER TABLE characters ADD COLUMN hidden INTEGER DEFAULT 0")
        except Exception:
            pass  # Column already exists

        # Add approval_date to characters if it doesn't exist
        try:
            await db.execute("ALTER TABLE characters ADD COLUMN approval_date TEXT")
        except Exception:
            pass  # Column already exists

        # Clean up posts with NULL dates — these are stale records from before
        # the date parser fix. Deleting them forces the next crawl to re-populate
        # with proper dates, which is needed for activity check queries.
        await db.execute("DELETE FROM posts WHERE post_date IS NULL")

        await db.commit()
