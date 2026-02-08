import pytest
import os
import tempfile
from httpx import AsyncClient, ASGITransport

# Set test environment variables before importing app
os.environ["FORUM_BASE_URL"] = "https://therewasanidea.jcink.net"

_test_db = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_PATH"] = _test_db

from app.main import app
from app.database import init_db


@pytest.fixture(autouse=True)
async def setup_db():
    """Ensure DB tables exist before each test."""
    await init_db()
    yield
    # Clean up DB file after all tests
    if os.path.exists(_test_db):
        try:
            os.unlink(_test_db)
        except OSError:
            pass


@pytest.fixture
async def client():
    """Create a test client."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        yield ac
