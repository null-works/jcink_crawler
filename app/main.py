import pathlib
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import APP_VERSION
from app.database import init_db
from app.routes import character_router, dashboard_router, game_router
from app.services.fetcher import close_client
from app.services.scheduler import start_scheduler, stop_scheduler

BASE_DIR = pathlib.Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database and scheduler on startup, cleanup on shutdown."""
    await init_db()
    start_scheduler()
    yield
    stop_scheduler()
    await close_client()


app = FastAPI(title="The Watcher", version=APP_VERSION, lifespan=lifespan)

# CORS for JCink embeds
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
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
