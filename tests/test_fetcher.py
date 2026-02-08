"""Tests for app/services/fetcher.py — HTTP fetching abstraction."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx


class TestGetClient:
    async def test_creates_client(self):
        from app.services import fetcher
        # Reset state
        fetcher._client = None
        client = await fetcher.get_client()
        assert isinstance(client, httpx.AsyncClient)
        assert not client.is_closed
        await fetcher.close_client()

    async def test_reuses_existing_client(self):
        from app.services import fetcher
        fetcher._client = None
        c1 = await fetcher.get_client()
        c2 = await fetcher.get_client()
        assert c1 is c2
        await fetcher.close_client()

    async def test_recreates_closed_client(self):
        from app.services import fetcher
        fetcher._client = None
        c1 = await fetcher.get_client()
        await c1.aclose()
        c2 = await fetcher.get_client()
        assert c2 is not c1
        assert not c2.is_closed
        await fetcher.close_client()


class TestCloseClient:
    async def test_closes_open_client(self):
        from app.services import fetcher
        fetcher._client = None
        await fetcher.get_client()
        assert fetcher._client is not None
        await fetcher.close_client()
        assert fetcher._client is None
        assert fetcher._authenticated is False

    async def test_handles_already_closed(self):
        from app.services import fetcher
        fetcher._client = None
        fetcher._authenticated = False
        # Should not raise
        await fetcher.close_client()


class TestAuthenticate:
    async def test_no_credentials_returns_false(self):
        from app.services import fetcher
        fetcher._client = None
        fetcher._authenticated = False

        with patch.object(fetcher.settings, "bot_username", ""), \
             patch.object(fetcher.settings, "bot_password", ""):
            result = await fetcher.authenticate()
            assert result is False
        await fetcher.close_client()

    async def test_successful_auth_with_session_cookie(self):
        from app.services import fetcher
        fetcher._client = None
        fetcher._authenticated = False

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.history = []

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.cookies = MagicMock()
        mock_client.cookies.keys = MagicMock(return_value=["member_id", "pass_hash"])

        with patch.object(fetcher.settings, "bot_username", "Watcher"), \
             patch.object(fetcher.settings, "bot_password", "secret"), \
             patch.object(fetcher, "get_client", return_value=mock_client):
            result = await fetcher.authenticate()
            assert result is True
            assert fetcher._authenticated is True

        fetcher._authenticated = False
        fetcher._client = None

    async def test_auth_via_redirect(self):
        from app.services import fetcher
        fetcher._client = None
        fetcher._authenticated = False

        mock_response = MagicMock()
        mock_response.status_code = 302
        mock_response.history = [MagicMock()]

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.cookies = MagicMock()
        mock_client.cookies.keys = MagicMock(return_value=[])

        with patch.object(fetcher.settings, "bot_username", "Watcher"), \
             patch.object(fetcher.settings, "bot_password", "secret"), \
             patch.object(fetcher, "get_client", return_value=mock_client):
            result = await fetcher.authenticate()
            assert result is True

        fetcher._authenticated = False
        fetcher._client = None

    async def test_auth_exception_returns_false(self):
        from app.services import fetcher
        fetcher._client = None
        fetcher._authenticated = False

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(side_effect=Exception("Network error"))
        mock_client.cookies = MagicMock()

        with patch.object(fetcher.settings, "bot_username", "Watcher"), \
             patch.object(fetcher.settings, "bot_password", "secret"), \
             patch.object(fetcher, "get_client", return_value=mock_client):
            result = await fetcher.authenticate()
            assert result is False

        fetcher._authenticated = False
        fetcher._client = None


class TestFetchPage:
    async def test_returns_html_on_success(self):
        from app.services import fetcher
        fetcher._authenticated = True  # Skip auth

        mock_response = MagicMock()
        mock_response.text = "<html><body>Hello</body></html>"
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(fetcher, "get_client", return_value=mock_client):
            result = await fetcher.fetch_page("https://example.com")
            assert result == "<html><body>Hello</body></html>"

        fetcher._authenticated = False
        fetcher._client = None

    async def test_returns_none_on_error(self):
        from app.services import fetcher
        fetcher._authenticated = True

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.get = AsyncMock(side_effect=httpx.HTTPError("fail"))

        with patch.object(fetcher, "get_client", return_value=mock_client):
            result = await fetcher.fetch_page("https://example.com")
            assert result is None

        fetcher._authenticated = False
        fetcher._client = None


class TestFetchPageWithDelay:
    async def test_applies_delay_before_fetch(self):
        from app.services import fetcher
        fetcher._authenticated = True

        mock_response = MagicMock()
        mock_response.text = "<html>OK</html>"
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(fetcher, "get_client", return_value=mock_client), \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
             patch.object(fetcher.settings, "request_delay_seconds", 0.01):
            result = await fetcher.fetch_page_with_delay("https://example.com")
            mock_sleep.assert_awaited_once_with(0.01)
            assert result == "<html>OK</html>"

        fetcher._authenticated = False
        fetcher._client = None


class TestEnsureAuthenticated:
    async def test_authenticates_when_not_yet(self):
        from app.services import fetcher
        fetcher._authenticated = False

        with patch.object(fetcher.settings, "bot_username", "Watcher"), \
             patch.object(fetcher, "authenticate", new_callable=AsyncMock) as mock_auth:
            await fetcher.ensure_authenticated()
            mock_auth.assert_awaited_once()

        fetcher._authenticated = False

    async def test_skips_when_already_authenticated(self):
        from app.services import fetcher
        fetcher._authenticated = True

        with patch.object(fetcher, "authenticate", new_callable=AsyncMock) as mock_auth:
            await fetcher.ensure_authenticated()
            mock_auth.assert_not_awaited()

        fetcher._authenticated = False

    async def test_skips_when_no_username(self):
        from app.services import fetcher
        fetcher._authenticated = False

        with patch.object(fetcher.settings, "bot_username", ""), \
             patch.object(fetcher, "authenticate", new_callable=AsyncMock) as mock_auth:
            await fetcher.ensure_authenticated()
            mock_auth.assert_not_awaited()
