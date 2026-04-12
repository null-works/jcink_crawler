import asyncio
import re
from urllib.parse import quote

import httpx
from app.config import settings

# Shared client for connection pooling
_client: httpx.AsyncClient | None = None
_authenticated: bool = False
_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    """Get or create the shared concurrency semaphore."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
    return _semaphore


def _is_cf_worker_enabled() -> bool:
    """Check if Cloudflare Worker proxy is configured."""
    return bool(settings.cf_worker_url and settings.cf_worker_key)


def _cf_proxy_url(target_url: str) -> str:
    """Rewrite a JCink URL to route through the Cloudflare Worker."""
    return f"{settings.cf_worker_url}/?key={quote(settings.cf_worker_key, safe='')}&url={quote(target_url, safe='')}"


async def get_client() -> httpx.AsyncClient:
    """Get or create the shared HTTP client."""
    global _client
    if _client is None or _client.is_closed:
        kwargs = {
            "timeout": 30.0,
            "follow_redirects": True,
            "headers": {
                "User-Agent": "Mozilla/5.0 (compatible; Watcher/1.0)",
            },
        }
        if settings.proxy_url:
            kwargs["proxy"] = settings.proxy_url
            print(f"[Fetcher] Using proxy: {settings.proxy_url}")
        if _is_cf_worker_enabled():
            print(f"[Fetcher] Using Cloudflare Worker proxy: {settings.cf_worker_url}")
        _client = httpx.AsyncClient(**kwargs)
    return _client


async def close_client() -> None:
    """Close the shared HTTP client and reset state."""
    global _client, _authenticated, _semaphore
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None
    _authenticated = False
    _semaphore = None


async def authenticate() -> bool:
    """Log in to JCink with the bot account.

    JCink uses a standard form POST for login. On success it sets
    session cookies that httpx will automatically persist on the client.

    Returns True if login succeeded.
    """
    global _authenticated

    if not settings.bot_username or not settings.bot_password:
        print("[Fetcher] No bot credentials configured, running as guest")
        return False

    client = await get_client()

    login_url = f"{settings.forum_base_url}/index.php?act=Login&CODE=01"
    login_data = {
        "UserName": settings.bot_username,
        "PassWord": settings.bot_password,
        "CookieDate": "1",  # Remember me
        "Privacy": "0",
    }

    try:
        if _is_cf_worker_enabled():
            actual_url = _cf_proxy_url(login_url)
            response = await client.post(actual_url, data=login_data)
            # Worker renames Set-Cookie to X-Proxied-Set-Cookie to avoid browser
            # interference; manually parse and inject into the httpx cookie jar.
            proxied_cookie = response.headers.get("x-proxied-set-cookie", "")
            if proxied_cookie:
                from http.cookies import SimpleCookie
                sc = SimpleCookie()
                sc.load(proxied_cookie)
                for name, morsel in sc.items():
                    client.cookies.set(name, morsel.value, domain=settings.forum_base_url.replace("https://", "").replace("http://", ""))
                print(f"[Fetcher] Parsed X-Proxied-Set-Cookie: {list(sc.keys())}")
        else:
            response = await client.post(login_url, data=login_data)

        # JCink redirects on successful login; check for session cookie
        has_session = any(
            "member_id" in name or "session_id" in name or "pass_hash" in name
            for name in client.cookies.keys()
        )

        if has_session:
            _authenticated = True
            print(f"[Fetcher] Authenticated as {settings.bot_username}")
            return True

        # Fallback: check if we got redirected (302/303) which indicates success
        if response.status_code in (302, 303) or response.history:
            _authenticated = True
            print(f"[Fetcher] Authenticated as {settings.bot_username} (via redirect)")
            return True

        print(f"[Fetcher] Login failed — status {response.status_code}, no session cookie found")
        print(f"[Fetcher] Cookies present: {list(client.cookies.keys())}")
        return False

    except Exception as e:
        print(f"[Fetcher] Login failed: {e}")
        return False


async def reauthenticate() -> bool:
    """Force a fresh login, clearing any stale session.

    Call this when a fetch returns a board message that might be caused
    by an expired JCink session rather than a genuinely restricted page.
    """
    global _authenticated
    _authenticated = False
    return await authenticate()


async def ensure_authenticated() -> None:
    """Ensure the client is authenticated if credentials are available.

    Authentication is required to see the modern (logged-in) JCink theme
    which has dl.profile-dossier with character fields. Guest view shows
    a different (legacy) template without those fields.
    """
    global _authenticated
    if not _authenticated and settings.bot_username and settings.bot_password:
        await authenticate()


async def fetch_page(url: str) -> str | None:
    """Fetch a page and return HTML content.

    If a Cloudflare Worker is configured, requests are routed through it
    so JCink sees Cloudflare's IP instead of the server's.

    Args:
        url: Full URL to fetch

    Returns:
        HTML string or None if fetch failed
    """
    await ensure_authenticated()
    try:
        client = await get_client()
        if _is_cf_worker_enabled():
            actual_url = _cf_proxy_url(url)
            # Manually pass JCink session cookies as Cookie header since they're
            # set for the JCink domain but we're requesting the Worker domain
            jcink_cookies = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
            headers = {"Cookie": jcink_cookies} if jcink_cookies else {}
            response = await client.get(actual_url, headers=headers)
        else:
            response = await client.get(url)
        response.raise_for_status()
        return response.text
    except Exception as e:
        print(f"[Fetcher] Failed to fetch {url}: {e}")
        return None


async def fetch_page_with_delay(url: str) -> str | None:
    """Fetch a page with a polite delay and concurrency control.

    Uses a semaphore to limit concurrent requests while maintaining
    a per-request delay for politeness.
    """
    async with _get_semaphore():
        await asyncio.sleep(settings.request_delay_seconds)
        return await fetch_page(url)


async def fetch_page_rendered(url: str, wait_selector: str = ".pf-a", timeout_ms: int = 15000) -> str | None:
    """Fetch a page using Playwright to execute JS and return rendered HTML.

    Used for profile pages where the power grid card is built client-side.
    Transfers auth cookies from the httpx client (which already handles login
    via a direct POST) into the Playwright browser context so it sees the
    authenticated skin with the custom profile template.
    Falls back to regular httpx fetch if Playwright fails.

    Args:
        url: Full URL to fetch
        wait_selector: CSS selector to wait for before capturing HTML
        timeout_ms: Max time to wait for the selector to appear
    """
    try:
        from playwright.async_api import async_playwright

        # Ensure httpx has authenticated so we can transfer its cookies
        await ensure_authenticated()
        client = await get_client()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()

            # Transfer auth cookies from httpx to Playwright
            pw_cookies = []
            for name, value in client.cookies.items():
                pw_cookies.append({
                    "name": name,
                    "value": value,
                    "url": settings.forum_base_url,
                })
            if pw_cookies:
                await context.add_cookies(pw_cookies)
                print(f"[Fetcher] Transferred {len(pw_cookies)} auth cookies to Playwright: {[c['name'] for c in pw_cookies]}")
            else:
                print("[Fetcher] No auth cookies to transfer, Playwright will browse as guest")

            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                # Wait for JS to render the custom profile template
                try:
                    await page.wait_for_selector(wait_selector, timeout=timeout_ms)
                    print(f"[Fetcher] Playwright found '{wait_selector}' on {url}")
                except Exception:
                    print(f"[Fetcher] Playwright: '{wait_selector}' not found on {url} after {timeout_ms}ms")

                # Wait a moment for any remaining JS to finish
                await page.wait_for_timeout(1000)
                html = await page.content()

                # Diagnostic: log what image-bearing elements exist
                from bs4 import BeautifulSoup
                diag_soup = BeautifulSoup(html, "html.parser")
                pf_c = 1 if diag_soup.select_one(".pf-c") else 0
                pf_p = 1 if diag_soup.select_one(".pf-p") else 0
                pf_w = 1 if diag_soup.select_one(".pf-w") else 0
                bg_count = len(diag_soup.find_all(style=re.compile(r"url\(", re.IGNORECASE)))
                print(f"[Fetcher] Rendered diagnostics for {url}: pf-c={pf_c} pf-p={pf_p} pf-w={pf_w} bg-url={bg_count}")

                return html
            finally:
                await browser.close()
    except Exception as e:
        print(f"[Fetcher] Playwright render failed for {url}: {e}, falling back to httpx")
        return await fetch_page(url)


async def fetch_pages_concurrent(urls: list[str]) -> list[str | None]:
    """Fetch multiple pages concurrently, respecting rate limits.

    Uses the shared semaphore to limit concurrent requests while
    fetching all URLs in parallel. Results are returned in the same
    order as the input URLs.
    """
    if not urls:
        return []
    return await asyncio.gather(*[fetch_page_with_delay(u) for u in urls])
