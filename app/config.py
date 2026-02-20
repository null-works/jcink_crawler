import base64
from datetime import datetime, timezone

from pydantic import ConfigDict
from pydantic_settings import BaseSettings

APP_VERSION = "2.7.23"
APP_BUILD_TIME = datetime.now(timezone.utc).strftime("%Y%m%d.%H%M%S")


class Settings(BaseSettings):
    model_config = ConfigDict(env_prefix="")

    forum_base_url: str = "https://therewasanidea.jcink.net"
    forum_complete_id: str = "49"
    forum_incomplete_id: str = "59"
    forum_comms_id: str = "31"
    forums_excluded: str = "4,5,6,7,8,9,10,11,12,14,15,16,18,52,56,57,58,60,63,69,85,86,87,88,90,91,92,95"
    crawl_threads_interval_minutes: int = 60
    crawl_profiles_interval_minutes: int = 1440
    crawl_discovery_interval_minutes: int = 1440
    crawl_quotes_batch_size: int = 0  # 0 = unlimited, process all unscraped threads
    quote_min_words: int = 3
    request_delay_seconds: float = 2.0
    max_concurrent_requests: int = 5
    database_path: str = "/app/data/crawler.db"
    bot_username: str = ""
    bot_password: str = ""
    admin_username: str = ""
    admin_password: str = ""
    acp_sync_interval_minutes: int = 0  # 0 = disabled
    affiliation_field_key: str = "affiliation"
    player_field_key: str = "player"
    excluded_names: str = "Watcher,Null,Spider,Kat,Randompercision"
    dashboard_password_b64: str = ""
    dashboard_secret_key: str = "change-me-in-production"

    @property
    def dashboard_password(self) -> str:
        if self.dashboard_password_b64:
            return base64.b64decode(self.dashboard_password_b64).decode("utf-8")
        return ""

    @property
    def excluded_forum_ids(self) -> set[str]:
        return set(self.forums_excluded.split(","))

    @property
    def excluded_name_set(self) -> set[str]:
        return {n.strip().lower() for n in self.excluded_names.split(",") if n.strip()}


settings = Settings()
