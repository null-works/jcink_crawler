"""Tests for API endpoints."""
import pytest
from unittest.mock import AsyncMock, patch
import aiosqlite


class TestHealthCheck:
    async def test_health(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    async def test_health_returns_json(self, client):
        response = await client.get("/health")
        assert response.headers["content-type"] == "application/json"


class TestStatusEndpoint:
    async def test_status(self, client):
        response = await client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert "characters_tracked" in data
        assert "total_threads" in data
        assert "total_quotes" in data

    async def test_status_initial_values(self, client):
        response = await client.get("/api/status")
        data = response.json()
        assert data["characters_tracked"] == 0
        assert data["total_threads"] == 0
        assert data["total_quotes"] == 0
        assert data["last_thread_crawl"] is None
        assert data["last_profile_crawl"] is None


class TestCharacterEndpoints:
    async def test_list_empty(self, client):
        response = await client.get("/api/characters")
        assert response.status_code == 200
        assert response.json() == []

    async def test_get_missing_character(self, client):
        response = await client.get("/api/character/999999")
        assert response.status_code == 404

    async def test_get_missing_character_error_detail(self, client):
        response = await client.get("/api/character/999999")
        assert response.json()["detail"] == "Character not found"


class TestThreadEndpoints:
    async def test_threads_for_missing_character(self, client):
        response = await client.get("/api/character/999999/threads")
        assert response.status_code == 404

    async def test_thread_counts_for_missing_character(self, client):
        response = await client.get("/api/character/999999/thread-counts")
        assert response.status_code == 404


class TestQuoteEndpoints:
    async def test_random_quote_returns_null_when_empty(self, client):
        """Random quote for a nonexistent character should return null (not 404)."""
        response = await client.get("/api/character/999999/quote")
        assert response.status_code == 200
        assert response.json() is None

    async def test_all_quotes_empty(self, client):
        response = await client.get("/api/character/999999/quotes")
        assert response.status_code == 200
        assert response.json() == []

    async def test_quote_count_for_missing(self, client):
        response = await client.get("/api/character/999999/quote-count")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["character_id"] == "999999"


class TestRegisterEndpoint:
    async def test_register_queues_background_task(self, client):
        """Registering a new character should return 'registering' status."""
        with patch("app.routes.character.register_character", new_callable=AsyncMock):
            response = await client.post("/api/character/register", json={"user_id": "42"})

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "registering"
        assert data["character_id"] == "42"

    async def test_register_missing_user_id(self, client):
        """Missing user_id should return 422 validation error."""
        response = await client.post("/api/character/register", json={})
        assert response.status_code == 422


class TestCrawlTriggerEndpoint:
    async def test_trigger_threads_crawl(self, client):
        with patch("app.routes.character.crawl_character_threads", new_callable=AsyncMock):
            response = await client.post("/api/crawl/trigger", json={
                "character_id": "42",
                "crawl_type": "threads",
            })
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "crawl_queued"
        assert data["crawl_type"] == "threads"

    async def test_trigger_profile_crawl(self, client):
        with patch("app.routes.character.crawl_character_profile", new_callable=AsyncMock):
            response = await client.post("/api/crawl/trigger", json={
                "character_id": "42",
                "crawl_type": "profile",
            })
        assert response.status_code == 200
        assert response.json()["crawl_type"] == "profile"

    async def test_trigger_invalid_crawl_type(self, client):
        response = await client.post("/api/crawl/trigger", json={
            "character_id": "42",
            "crawl_type": "invalid",
        })
        assert response.status_code == 400
        assert "Invalid crawl_type" in response.json()["detail"]

    async def test_trigger_missing_character_id(self, client):
        response = await client.post("/api/crawl/trigger", json={
            "crawl_type": "threads",
        })
        assert response.status_code == 422
