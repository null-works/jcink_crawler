from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.routes import character_router
from app.services.fetcher import close_client
from app.services.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database and scheduler on startup, cleanup on shutdown."""
    await init_db()
    start_scheduler()
    yield
    stop_scheduler()
    await close_client()


app = FastAPI(title="The Watcher", version="1.0.0", lifespan=lifespan)

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


# Register API routes
app.include_router(character_router, prefix="/api")
