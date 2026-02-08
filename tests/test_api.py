"""Tests for API endpoints."""
import pytest


class TestHealthCheck:
    async def test_health(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestStatusEndpoint:
    async def test_status(self, client):
        response = await client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert "characters_tracked" in data
        assert "total_threads" in data
        assert "total_quotes" in data


class TestCharacterEndpoints:
    async def test_list_empty(self, client):
        response = await client.get("/api/characters")
        assert response.status_code == 200
        assert response.json() == []

    async def test_get_missing_character(self, client):
        response = await client.get("/api/character/999999")
        assert response.status_code == 404
