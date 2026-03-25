/**
 * Browser Sync — JCink board page script.
 *
 * This runs inside a hidden iframe on jcink.net, loaded from a custom
 * board page. Because it's same-origin with jcink.net, it can access
 * the ACP and profile pages without CORS issues. It communicates with
 * the parent Watcher dashboard via postMessage.
 *
 * Flow:
 * 1. Parent sends {action: "sync", ...} via postMessage
 * 2. This script logs into ACP, runs dump, fetches profiles
 * 3. Posts data to the Watcher server API (allowed via CORS)
 * 4. Sends status updates back to parent via postMessage
 */
(function () {
    'use strict';

    let serverBase = '';
    let parentOrigin = '';

    // ── Communication with parent window ──

    function sendStatus(phase, message, detail) {
        if (window.parent && parentOrigin) {
            window.parent.postMessage({
                type: 'watcher-sync-status',
                phase: phase,
                message: message,
                detail: detail || null
            }, parentOrigin);
        }
    }

    function sendComplete(summary) {
        if (window.parent && parentOrigin) {
            window.parent.postMessage({
                type: 'watcher-sync-complete',
                summary: summary
            }, parentOrigin);
        }
    }

    function sendError(message) {
        if (window.parent && parentOrigin) {
            window.parent.postMessage({
                type: 'watcher-sync-error',
                message: message
            }, parentOrigin);
        }
    }

    // ── ACP Dump ──

    const NEXT_LINK_RE = /admin\.php\?(?=[^'"]*\bact=mysql\b)(?=[^'"]*\bcode=dump\b)(?=[^'"]*\bline=(\d+))(?=[^'"]*\bpart=(\d+))(?=[^'"]*\badsess=([a-f0-9]+))/gi;

    async function acpLogin(username, password) {
        const forumBase = window.location.origin;
        const loginUrl = forumBase + '/admin.php?login=yes&username=' +
            encodeURIComponent(username) + '&password=' + encodeURIComponent(password);

        const resp = await fetch(loginUrl);
        const html = await resp.text();

        const match = html.match(/adsess=([a-f0-9]+)/);
        if (!match) return null;
        return match[1];
    }

    async function acpDump(adsess) {
        const forumBase = window.location.origin;
        const forumMatch = window.location.hostname.match(/^(\w+)\.jcink\.net$/);
        const forumName = forumMatch ? forumMatch[1] : 'unknown';

        // Clear old backup
        sendStatus('acp', 'Clearing old backup...');
        await fetch(forumBase + '/admin.php?act=mysql&code=backup&erase=1&adsess=' + adsess);
        await sleep(1000);

        // Start dump
        sendStatus('acp', 'Starting database dump...');
        let resp = await fetch(forumBase + '/admin.php?act=mysql&code=dump&step1=1&adsess=' + adsess);
        let html = await resp.text();

        let totalPages = 1;
        NEXT_LINK_RE.lastIndex = 0;
        let match = NEXT_LINK_RE.exec(html);

        while (match && totalPages < 2000) {
            const line = match[1], part = match[2];
            const url = forumBase + '/admin.php?act=mysql&adsess=' + adsess +
                '&code=dump&line=' + line + '&part=' + part;
            resp = await fetch(url);
            html = await resp.text();
            totalPages++;
            if (totalPages % 5 === 0) {
                sendStatus('acp', 'Dumping database...', totalPages + ' pages');
            }
            NEXT_LINK_RE.lastIndex = 0;
            match = NEXT_LINK_RE.exec(html);
        }

        sendStatus('acp', 'Dump complete (' + totalPages + ' pages). Fetching SQL file...');

        // Fetch SQL file
        const sqlUrl = forumBase + '/sqls/' + adsess + '-' + forumName + '_.sql';
        let sqlContent = null;

        for (const waitSecs of [2, 5, 10, 15, 30]) {
            await sleep(waitSecs * 1000);
            sendStatus('acp', 'Waiting for SQL file...', waitSecs + 's');
            try {
                const sqlResp = await fetch(sqlUrl);
                if (sqlResp.ok) {
                    const text = await sqlResp.text();
                    if (text.length > 100) {
                        sqlContent = text;
                        break;
                    }
                }
            } catch (e) { /* retry */ }
        }

        return sqlContent;
    }

    // ── Profile Fetch ──

    async function fetchProfiles(characterIds) {
        const forumBase = window.location.origin;
        const results = [];
        const total = characterIds.length;

        for (let i = 0; i < total; i++) {
            const cid = characterIds[i];
            sendStatus('profiles', 'Fetching profile ' + (i + 1) + '/' + total, cid);

            try {
                const url = forumBase + '/index.php?showuser=' + cid;
                const resp = await fetch(url);
                const html = await resp.text();
                results.push({ character_id: cid, html: html });
            } catch (e) {
                sendStatus('profiles', 'Failed to fetch profile ' + cid, e.message);
            }

            // Polite delay between requests
            if (i < total - 1) {
                await sleep(1500);
            }
        }

        return results;
    }

    // ── Upload to Watcher ──

    async function uploadAcpDump(sqlContent) {
        sendStatus('upload', 'Uploading ACP data to server...');
        const resp = await fetch(serverBase + '/api/acp/upload-dump', {
            method: 'POST',
            headers: { 'Content-Type': 'text/plain' },
            body: sqlContent
        });
        return await resp.json();
    }

    async function uploadProfiles(profiles) {
        if (!profiles.length) return { count: 0 };

        sendStatus('upload', 'Uploading ' + profiles.length + ' profiles to server...');

        // Send in batches of 10 to avoid huge payloads
        const batchSize = 10;
        let totalUploaded = 0;

        for (let i = 0; i < profiles.length; i += batchSize) {
            const batch = profiles.slice(i, i + batchSize);
            sendStatus('upload', 'Uploading profiles...', (i + batch.length) + '/' + profiles.length);

            const resp = await fetch(serverBase + '/api/profiles/upload-html', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ profiles: batch })
            });
            if (resp.ok) {
                totalUploaded += batch.length;
            }
        }

        return { count: totalUploaded };
    }

    // ── Main Sync Orchestrator ──

    async function runSync(config) {
        const summary = { acp: null, profiles: null, errors: [] };

        try {
            // Phase 1: ACP dump (if credentials provided)
            if (config.acp_username && config.acp_password) {
                sendStatus('acp', 'Logging into ACP...');
                const adsess = await acpLogin(config.acp_username, config.acp_password);
                if (!adsess) {
                    summary.errors.push('ACP login failed — check credentials');
                    sendStatus('acp', 'ACP login failed');
                } else {
                    const sql = await acpDump(adsess);
                    if (sql) {
                        const result = await uploadAcpDump(sql);
                        summary.acp = {
                            size_kb: Math.round(sql.length / 1024),
                            server_response: result
                        };
                        sendStatus('acp', 'ACP data uploaded (' + Math.round(sql.length / 1024) + ' KB)');
                    } else {
                        summary.errors.push('SQL file not generated');
                        sendStatus('acp', 'SQL file not generated');
                    }
                }
            }

            // Phase 2: Profile sync (if character IDs provided)
            if (config.character_ids && config.character_ids.length > 0) {
                sendStatus('profiles', 'Starting profile sync...', config.character_ids.length + ' characters');
                const profiles = await fetchProfiles(config.character_ids);
                if (profiles.length > 0) {
                    const result = await uploadProfiles(profiles);
                    summary.profiles = {
                        fetched: profiles.length,
                        uploaded: result.count
                    };
                    sendStatus('profiles', 'Profiles uploaded', result.count + ' of ' + profiles.length);
                }
            }

        } catch (e) {
            summary.errors.push(e.message);
            sendError(e.message);
        }

        sendComplete(summary);
    }

    // ── Message Handler ──

    window.addEventListener('message', function (event) {
        const data = event.data;
        if (!data || data.type !== 'watcher-sync-start') return;

        // Store parent origin for responses
        parentOrigin = event.origin;
        serverBase = data.server_base || '';

        if (!serverBase) {
            sendError('No server URL provided');
            return;
        }

        runSync({
            acp_username: data.acp_username || '',
            acp_password: data.acp_password || '',
            character_ids: data.character_ids || [],
        });
    });

    // ── Utility ──

    function sleep(ms) {
        return new Promise(function (r) { setTimeout(r, ms); });
    }

    // Signal that the iframe is ready
    if (window.parent !== window) {
        // Broadcast ready to any parent — they'll filter by type
        window.parent.postMessage({ type: 'watcher-sync-ready' }, '*');
    }
})();
