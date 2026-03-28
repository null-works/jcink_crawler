/**
 * Cloudflare Worker — Forward proxy for JCink crawling.
 *
 * Receives requests from the Watcher server, forwards them to JCink
 * using Cloudflare's IP, and returns the response. This keeps the
 * server's IP off JCink entirely.
 *
 * Supports:
 *   - GET and POST requests
 *   - Cookie forwarding (for authenticated JCink sessions)
 *   - Shared secret authentication
 *
 * Deploy: npx wrangler deploy
 * Config: set CF_PROXY_KEY secret via `npx wrangler secret put CF_PROXY_KEY`
 */

export default {
	async fetch(request, env) {
		// Only allow GET and POST
		if (request.method !== 'GET' && request.method !== 'POST') {
			return new Response('Method not allowed', { status: 405 });
		}

		const url = new URL(request.url);

		// Authenticate via shared secret
		const key = url.searchParams.get('key') || '';
		if (!env.CF_PROXY_KEY || key !== env.CF_PROXY_KEY) {
			return new Response('Forbidden', { status: 403 });
		}

		// Target URL to proxy
		const target = url.searchParams.get('url');
		if (!target) {
			return new Response('Missing "url" query parameter', { status: 400 });
		}

		// Validate target is a JCink URL (safety measure)
		let targetUrl;
		try {
			targetUrl = new URL(target);
		} catch {
			return new Response('Invalid target URL', { status: 400 });
		}
		if (!targetUrl.hostname.endsWith('.jcink.net')) {
			return new Response('Target must be a jcink.net domain', { status: 403 });
		}

		// Build forwarded request headers
		const forwardHeaders = new Headers();
		forwardHeaders.set('User-Agent', request.headers.get('User-Agent') || 'Mozilla/5.0 (compatible; Watcher/1.0)');

		// Forward cookies if present (needed for authenticated JCink sessions)
		const cookies = request.headers.get('Cookie');
		if (cookies) {
			forwardHeaders.set('Cookie', cookies);
		}

		// Forward content-type for POST requests
		const contentType = request.headers.get('Content-Type');
		if (contentType) {
			forwardHeaders.set('Content-Type', contentType);
		}

		// Build fetch options
		const fetchOpts = {
			method: request.method,
			headers: forwardHeaders,
			redirect: 'follow',
		};

		// Forward body for POST requests
		if (request.method === 'POST') {
			fetchOpts.body = await request.text();
		}

		try {
			const resp = await fetch(target, fetchOpts);

			// Build response with relevant headers
			const responseHeaders = new Headers();
			responseHeaders.set('Content-Type', resp.headers.get('Content-Type') || 'text/html');

			// Forward set-cookie headers so the caller can maintain JCink sessions
			const setCookie = resp.headers.get('Set-Cookie');
			if (setCookie) {
				responseHeaders.set('X-Proxied-Set-Cookie', setCookie);
			}

			// Stream the response body instead of buffering — handles 40MB+ SQL files
			// without hitting the Worker's 128MB memory limit
			return new Response(resp.body, {
				status: resp.status,
				headers: responseHeaders,
			});
		} catch (e) {
			return new Response('Proxy fetch failed: ' + e.message, { status: 502 });
		}
	},
};
