"""Tests for API endpoints."""
import pytest
from unittest.mock import AsyncMock, patch
import aiosqlite

from app.database import DATABASE_PATH
from app.models.operations import (
    upsert_character,
    upsert_profile_field,
    upsert_thread,
    link_character_thread,
)


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


class TestClaimsEndpoint:
    async def _seed_character(self, fields=None):
        """Helper: insert a character with profile fields."""
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            await upsert_character(db, "42", "Wanda Maximoff",
                                   "https://example.com/42", "Purple",
                                   "https://img.com/wanda.jpg")
            if fields:
                for key, value in fields.items():
                    await upsert_profile_field(db, "42", key, value)
            # Add some threads
            await upsert_thread(db, "100", "Thread A", "https://example.com/t/100",
                                None, None, "ongoing")
            await link_character_thread(db, "42", "100", "ongoing")
            await upsert_thread(db, "101", "Thread B", "https://example.com/t/101",
                                "49", "Complete", "complete")
            await link_character_thread(db, "42", "101", "complete")
            await db.commit()

    async def test_claims_empty(self, client):
        response = await client.get("/api/claims")
        assert response.status_code == 200
        assert response.json() == []

    async def test_claims_returns_character_with_fields(self, client):
        await self._seed_character({
            "face claim": "Elizabeth Olsen",
            "species": "mutant",
            "codename": "Scarlet Witch",
            "alias": "Kim",
            "affiliation": "Avengers",
            "connections": "Pietro (twin)",
        })
        response = await client.get("/api/claims")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        claim = data[0]
        assert claim["id"] == "42"
        assert claim["name"] == "Wanda Maximoff"
        assert claim["group_name"] == "Purple"
        assert claim["group_id"] == "11"
        assert claim["avatar_url"] == "https://img.com/wanda.jpg"
        assert claim["face_claim"] == "Elizabeth Olsen"
        assert claim["species"] == "mutant"
        assert claim["codename"] == "Scarlet Witch"
        assert claim["alias"] == "Kim"
        assert claim["affiliation"] == "Avengers"
        assert claim["connections"] == "Pietro (twin)"
        assert claim["thread_counts"]["ongoing"] == 1
        assert claim["thread_counts"]["complete"] == 1
        assert claim["thread_counts"]["total"] == 2

    async def test_claims_missing_fields_are_null(self, client):
        await self._seed_character()
        response = await client.get("/api/claims")
        data = response.json()
        claim = data[0]
        assert claim["face_claim"] is None
        assert claim["species"] is None
        assert claim["connections"] is None


class TestWebhookEndpoint:
    async def test_webhook_profile_edit(self, client):
        with patch("app.routes.character.crawl_character_profile", new_callable=AsyncMock):
            response = await client.post("/api/webhook/activity", json={
                "event": "profile_edit",
                "user_id": "42",
            })
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["action"] == "profile_recrawl"

    async def test_webhook_new_post_with_thread_id(self, client):
        """new_post with thread_id should trigger targeted single-thread crawl."""
        with patch("app.routes.character.crawl_single_thread", new_callable=AsyncMock):
            response = await client.post("/api/webhook/activity", json={
                "event": "new_post",
                "thread_id": "123",
                "user_id": "42",
            })
        assert response.status_code == 202
        data = response.json()
        assert data["action"] == "thread_recrawl"
        assert data["thread_id"] == "123"

    async def test_webhook_new_post_without_thread_id(self, client):
        """new_post without thread_id falls back to full thread crawl."""
        with patch("app.routes.character.crawl_character_threads", new_callable=AsyncMock):
            response = await client.post("/api/webhook/activity", json={
                "event": "new_post",
                "user_id": "42",
            })
        assert response.status_code == 202
        assert response.json()["action"] == "thread_recrawl"
        assert response.json()["user_id"] == "42"

    async def test_webhook_new_topic(self, client):
        with patch("app.routes.character.crawl_single_thread", new_callable=AsyncMock):
            response = await client.post("/api/webhook/activity", json={
                "event": "new_topic",
                "thread_id": "456",
                "forum_id": "5",
                "user_id": "42",
            })
        assert response.status_code == 202
        assert response.json()["action"] == "thread_recrawl"
        assert response.json()["thread_id"] == "456"

    async def test_webhook_no_user_id_no_thread_id(self, client):
        response = await client.post("/api/webhook/activity", json={
            "event": "new_post",
        })
        assert response.status_code == 202
        assert response.json()["action"] == "none"

    async def test_webhook_unknown_event(self, client):
        response = await client.post("/api/webhook/activity", json={
            "event": "unknown_thing",
            "user_id": "42",
        })
        assert response.status_code == 202
        assert response.json()["action"] == "none"

    async def test_webhook_missing_event(self, client):
        response = await client.post("/api/webhook/activity", json={})
        assert response.status_code == 422


class TestBatchFieldsEndpoint:
    async def _seed_characters(self):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            await upsert_character(db, "42", "Tony Stark", "https://example.com/42")
            await upsert_profile_field(db, "42", "square_image", "https://img.com/tony.jpg")
            await upsert_profile_field(db, "42", "short_quote", "I am Iron Man")
            await upsert_profile_field(db, "42", "species", "human")
            await upsert_character(db, "55", "Steve Rogers", "https://example.com/55")
            await upsert_profile_field(db, "55", "square_image", "https://img.com/steve.jpg")
            await upsert_profile_field(db, "55", "short_quote", "I can do this all day")
            await db.commit()

    async def test_batch_fields_all(self, client):
        await self._seed_characters()
        response = await client.get("/api/characters/fields?ids=42,55")
        assert response.status_code == 200
        data = response.json()
        assert "42" in data
        assert "55" in data
        assert data["42"]["square_image"] == "https://img.com/tony.jpg"
        assert data["55"]["short_quote"] == "I can do this all day"

    async def test_batch_fields_filtered(self, client):
        await self._seed_characters()
        response = await client.get("/api/characters/fields?ids=42&fields=square_image,short_quote")
        assert response.status_code == 200
        data = response.json()
        assert data["42"]["square_image"] == "https://img.com/tony.jpg"
        assert data["42"]["short_quote"] == "I am Iron Man"
        assert "species" not in data["42"]

    async def test_batch_fields_unknown_id(self, client):
        response = await client.get("/api/characters/fields?ids=99999")
        assert response.status_code == 200
        data = response.json()
        assert data["99999"] == {}

    async def test_batch_fields_missing_ids_param(self, client):
        response = await client.get("/api/characters/fields")
        assert response.status_code == 422

    async def test_batch_fields_hero_images(self, client):
        """Hero image fields (portrait_image, rectangle_gif, etc.) should be
        returned by the batch fields endpoint when stored via upsert_profile_field."""
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            await upsert_character(db, "91", "Aaron Fischer", "https://example.com/91")
            await upsert_profile_field(db, "91", "portrait_image", "https://i.imgur.com/portrait91.png")
            await upsert_profile_field(db, "91", "rectangle_gif", "https://i.imgur.com/rect91.gif")
            await upsert_profile_field(db, "91", "square_image", "https://i.imgur.com/sq91.png")
            await upsert_profile_field(db, "91", "secondary_square_image", "https://i.imgur.com/sq2_91.png")
            await upsert_profile_field(db, "91", "short_quote", "No more running.")
            await upsert_character(db, "44", "Adam Warlock", "https://example.com/44")
            await upsert_profile_field(db, "44", "portrait_image", "https://i.imgur.com/portrait44.png")
            await db.commit()

        response = await client.get(
            "/api/characters/fields?ids=91,44&fields=portrait_image,rectangle_gif,short_quote"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["91"]["portrait_image"] == "https://i.imgur.com/portrait91.png"
        assert data["91"]["rectangle_gif"] == "https://i.imgur.com/rect91.gif"
        assert data["91"]["short_quote"] == "No more running."
        assert data["44"]["portrait_image"] == "https://i.imgur.com/portrait44.png"
        assert "rectangle_gif" not in data["44"]


class TestCrawlTriggerAllProfiles:
    async def test_trigger_all_profiles(self, client):
        """all-profiles crawl type should trigger profile-only re-crawl."""
        with patch("app.routes.character._crawl_all_profiles", new_callable=AsyncMock):
            response = await client.post("/api/crawl/trigger", json={
                "crawl_type": "all-profiles",
            })
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "crawl_queued"
        assert data["crawl_type"] == "all-profiles"

    async def test_trigger_profiles_alias(self, client):
        """'profiles' should work as an alias for 'all-profiles'."""
        with patch("app.routes.character._crawl_all_profiles", new_callable=AsyncMock):
            response = await client.post("/api/crawl/trigger", json={
                "crawl_type": "profiles",
            })
        assert response.status_code == 200
        assert response.json()["crawl_type"] == "all-profiles"
