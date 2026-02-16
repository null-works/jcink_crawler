import aiosqlite
from app.config import settings

DATABASE_PATH = settings.database_path


async def get_db():
    """Get database connection."""
    db = await aiosqlite.connect(DATABASE_PATH)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


async def init_db():
    """Initialize database tables."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
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

        # Crawl status - track overall crawl state
        await db.execute("""
            CREATE TABLE IF NOT EXISTS crawl_status (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

        # Add post_count to character_threads if it doesn't exist
        try:
            await db.execute("ALTER TABLE character_threads ADD COLUMN post_count INTEGER DEFAULT 0")
        except Exception:
            pass  # Column already exists

        # Add last_post_excerpt to threads if it doesn't exist
        try:
            await db.execute("ALTER TABLE threads ADD COLUMN last_post_excerpt TEXT")
        except Exception:
            pass  # Column already exists

        # Clean up posts with NULL dates — these are stale records from before
        # the date parser fix. Deleting them forces the next crawl to re-populate
        # with proper dates, which is needed for activity check queries.
        await db.execute("DELETE FROM posts WHERE post_date IS NULL")

        await db.commit()
