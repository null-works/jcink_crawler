import pathlib
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import APP_VERSION, settings
from app.database import init_db
from app.routes import character_router, dashboard_router, game_router
from app.services.fetcher import close_client
from app.services.scheduler import start_scheduler, stop_scheduler

BASE_DIR = pathlib.Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database and scheduler on startup, cleanup on shutdown."""
    if settings.dashboard_secret_key == "change-me-in-production":
        print("[WARNING] DASHBOARD_SECRET_KEY is set to the insecure default. "
              "Session cookies can be forged. Set a random secret in your .env file.")
    await init_db()
    await start_scheduler()
    yield
    stop_scheduler()
    await close_client()


app = FastAPI(title="The Watcher", version=APP_VERSION, lifespan=lifespan)

# CORS for JCink embeds — allow both http and https origins for the forum
_cors_origins = [settings.forum_base_url]
# Also allow http:// variant in case forum is accessed without TLS
if settings.forum_base_url.startswith("https://"):
    _cors_origins.append(settings.forum_base_url.replace("https://", "http://", 1))
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


# Static files
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

# Register API routes
app.include_router(character_router, prefix="/api")

# Register dashboard routes (HTML)
app.include_router(dashboard_router)

# Register game routes (embed + dashboard page + game API)
app.include_router(game_router)
