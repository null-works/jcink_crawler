"""Tests for dashboard routes (HTML pages + HTMX partials + auth)."""
import pytest
import os
from unittest.mock import patch, AsyncMock


class TestLoginFlow:
    async def test_login_page_renders(self, client):
        """Login page should render when password is set."""
        with patch("app.routes.dashboard.settings") as mock_settings:
            mock_settings.dashboard_password = "secret"
            mock_settings.dashboard_secret_key = "test-key"
            mock_settings.affiliation_field_key = "affiliation"
            mock_settings.excluded_name_set = set()
            mock_settings.database_path = os.environ.get("DATABASE_PATH", "/tmp/test.db")
            response = await client.get("/login")
            assert response.status_code == 200
            assert "The Watcher" in response.text
            assert "password" in response.text.lower()

    async def test_login_wrong_password(self, client):
        """Wrong password should show error."""
        with patch("app.routes.dashboard.settings") as mock_settings:
            mock_settings.dashboard_password = "secret"
            mock_settings.dashboard_secret_key = "test-key"
            response = await client.post("/login", data={"password": "wrong"})
            assert response.status_code == 200
            assert "Invalid password" in response.text

    async def test_login_correct_password_redirects(self, client):
        """Correct password should redirect to dashboard."""
        with patch("app.routes.dashboard.settings") as mock_settings:
            mock_settings.dashboard_password = "secret"
            mock_settings.dashboard_secret_key = "test-key"
            response = await client.post("/login", data={"password": "secret"}, follow_redirects=False)
            assert response.status_code == 302
            assert response.headers["location"] == "/dashboard"

    async def test_login_sets_cookie(self, client):
        """Successful login should set session cookie."""
        with patch("app.routes.dashboard.settings") as mock_settings:
            mock_settings.dashboard_password = "secret"
            mock_settings.dashboard_secret_key = "test-key"
            response = await client.post("/login", data={"password": "secret"}, follow_redirects=False)
            assert "watcher_session" in response.headers.get("set-cookie", "")

    async def test_logout_clears_cookie(self, client):
        """Logout should clear cookie and redirect to login."""
        response = await client.get("/logout", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/login"


class TestDashboardNoAuth:
    """Tests when no password is set (open access)."""

    async def test_root_redirects_to_dashboard(self, client):
        response = await client.get("/", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/dashboard"

    async def test_dashboard_returns_html(self, client):
        response = await client.get("/dashboard")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "The Watcher" in response.text

    async def test_dashboard_contains_stats(self, client):
        response = await client.get("/dashboard")
        assert "Characters" in response.text
        assert "Threads" in response.text
        assert "Quotes" in response.text

    async def test_dashboard_contains_charts(self, client):
        response = await client.get("/dashboard")
        assert "chart-card" in response.text

    async def test_characters_page_contains_table(self, client):
        response = await client.get("/characters")
        assert response.status_code == 200
        assert "data-table" in response.text

    async def test_threads_page(self, client):
        response = await client.get("/threads")
        assert response.status_code == 200
        assert "Thread Browser" in response.text

    async def test_quotes_page(self, client):
        response = await client.get("/quotes")
        assert response.status_code == 200
        assert "Quote Browser" in response.text

    async def test_admin_page(self, client):
        response = await client.get("/admin")
        assert response.status_code == 200
        assert "Admin" in response.text

    async def test_character_detail_404(self, client):
        response = await client.get("/character/nonexistent")
        assert response.status_code == 404

    async def test_login_redirects_when_no_password(self, client):
        """Login page should redirect to dashboard when no password is set."""
        response = await client.get("/login", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/dashboard"


class TestHTMXPartials:
    async def test_htmx_characters(self, client):
        response = await client.get("/htmx/characters", headers={"HX-Request": "true"})
        assert response.status_code == 200
        # Should be HTML fragment, not full page
        assert "<html" not in response.text

    async def test_htmx_activity(self, client):
        response = await client.get("/htmx/activity")
        assert response.status_code == 200
        assert "Idle" in response.text or "activity-dot" in response.text

    async def test_htmx_stats(self, client):
        response = await client.get("/htmx/stats")
        assert response.status_code == 200
        assert "stats-card" in response.text

    async def test_htmx_threads(self, client):
        response = await client.get("/htmx/threads")
        assert response.status_code == 200

    async def test_htmx_quotes(self, client):
        response = await client.get("/htmx/quotes")
        assert response.status_code == 200

    async def test_htmx_register_empty_id(self, client):
        response = await client.post("/htmx/register", data={"user_id": ""})
        assert response.status_code == 200
        assert "required" in response.text.lower()

    @patch("app.routes.dashboard.discover_characters", new_callable=AsyncMock)
    async def test_htmx_crawl_discover(self, mock_discover, client):
        response = await client.post("/htmx/crawl", data={"crawl_type": "discover"})
        assert response.status_code == 200
        assert "queued" in response.text.lower() or "Crawl" in response.text

    async def test_htmx_crawl_missing_character(self, client):
        response = await client.post("/htmx/crawl", data={"crawl_type": "threads"})
        assert response.status_code == 200
        assert "required" in response.text.lower()

    async def test_threads_page_htmx_returns_partial(self, client):
        """When HX-Request header is present, threads page should return partial."""
        response = await client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "<html" not in response.text
        assert "<!DOCTYPE" not in response.text

    async def test_quotes_page_htmx_returns_partial(self, client):
        """When HX-Request header is present, quotes page should return partial."""
        response = await client.get("/quotes", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "<html" not in response.text


class TestDashboardWithAuth:
    """Tests when password protection is enabled."""

    async def test_dashboard_redirects_to_login(self, client):
        with patch("app.routes.dashboard.settings") as mock_settings:
            mock_settings.dashboard_password = "secret"
            mock_settings.dashboard_secret_key = "test-key"
            mock_settings.affiliation_field_key = "affiliation"
            mock_settings.excluded_name_set = set()
            mock_settings.database_path = os.environ.get("DATABASE_PATH", "/tmp/test.db")
            response = await client.get("/dashboard", follow_redirects=False)
            assert response.status_code == 302
            assert response.headers["location"] == "/login"

    async def test_htmx_returns_401_without_auth(self, client):
        with patch("app.routes.dashboard.settings") as mock_settings:
            mock_settings.dashboard_password = "secret"
            mock_settings.dashboard_secret_key = "test-key"
            response = await client.get("/htmx/characters")
            assert response.status_code == 401

    async def test_api_routes_unaffected(self, client):
        """API routes should NOT be affected by dashboard auth."""
        with patch("app.routes.dashboard.settings") as mock_settings:
            mock_settings.dashboard_password = "secret"
            mock_settings.dashboard_secret_key = "test-key"
            # API routes use the original settings, not mocked
            response = await client.get("/api/status")
            assert response.status_code == 200

    async def test_health_unaffected(self, client):
        """Health check should NOT be affected by dashboard auth."""
        with patch("app.routes.dashboard.settings") as mock_settings:
            mock_settings.dashboard_password = "secret"
            mock_settings.dashboard_secret_key = "test-key"
            response = await client.get("/health")
            assert response.status_code == 200


class TestStaticFiles:
    async def test_css_served(self, client):
        response = await client.get("/static/css/dracula.css")
        assert response.status_code == 200
        assert "text/css" in response.headers["content-type"]

    async def test_js_served(self, client):
        response = await client.get("/static/js/app.js")
        assert response.status_code == 200

    async def test_favicon_served(self, client):
        response = await client.get("/static/img/favicon.svg")
        assert response.status_code == 200
