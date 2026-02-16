from datetime import datetime
from enum import Enum
from pydantic import BaseModel


class ThreadCategory(str, Enum):
    ONGOING = "ongoing"
    COMMS = "comms"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class CrawlStatus(str, Enum):
    PENDING = "pending"
    CRAWLING = "crawling"
    COMPLETE = "complete"
    ERROR = "error"


# --- API Response Models ---

class CharacterSummary(BaseModel):
    id: str
    name: str
    profile_url: str
    group_name: str | None = None
    avatar_url: str | None = None
    affiliation: str | None = None
    thread_counts: dict[str, int] = {}
    last_profile_crawl: datetime | None = None
    last_thread_crawl: datetime | None = None


class ThreadInfo(BaseModel):
    id: str
    title: str
    url: str
    forum_id: str | None = None
    forum_name: str | None = None
    category: ThreadCategory = ThreadCategory.ONGOING
    last_poster_id: str | None = None
    last_poster_name: str | None = None
    last_poster_avatar: str | None = None
    is_user_last_poster: bool = False


class CharacterThreads(BaseModel):
    character_id: str
    character_name: str
    ongoing: list[ThreadInfo] = []
    comms: list[ThreadInfo] = []
    complete: list[ThreadInfo] = []
    incomplete: list[ThreadInfo] = []
    counts: dict[str, int] = {}


class Quote(BaseModel):
    id: int | None = None
    character_id: str
    quote_text: str
    source_thread_id: str | None = None
    source_thread_title: str | None = None
    created_at: datetime | None = None


class ClaimsSummary(BaseModel):
    """Character data for the claims page — enriched with face_claim, species, etc."""
    id: str
    name: str
    profile_url: str
    group_id: str | None = None
    group_name: str | None = None
    avatar_url: str | None = None
    face_claim: str | None = None
    species: str | None = None
    codename: str | None = None
    alias: str | None = None
    affiliation: str | None = None
    connections: str | None = None
    thread_counts: dict[str, int] = {}


class CharacterProfile(BaseModel):
    character: CharacterSummary
    fields: dict[str, str] = {}
    threads: CharacterThreads | None = None


class CrawlStatusResponse(BaseModel):
    characters_tracked: int = 0
    total_threads: int = 0
    total_quotes: int = 0
    last_thread_crawl: str | None = None
    last_profile_crawl: str | None = None
    current_activity: dict | None = None


# --- Request Models ---

class CharacterRegister(BaseModel):
    """Register a character for tracking by user ID."""
    user_id: str


class CrawlTrigger(BaseModel):
    """Manually trigger a crawl for a specific character."""
    character_id: str | None = None
    crawl_type: str = "threads"  # "threads", "profile", "discover"


class WebhookActivity(BaseModel):
    """Webhook payload from the theme for real-time updates."""
    event: str  # "new_post", "new_topic", "profile_edit"
    thread_id: str | None = None
    forum_id: str | None = None
    user_id: str | None = None
