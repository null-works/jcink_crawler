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

    // ── Verbose logging (sent to parent as log messages) ──

    function log(level, msg, detail) {
        console.log('[BrowserSync]', level.toUpperCase(), msg, detail || '');
        if (window.parent && parentOrigin) {
            window.parent.postMessage({
                type: 'watcher-sync-log',
                level: level,
                message: msg,
                detail: detail || null
            }, parentOrigin);
        }
    }

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

        log('info', 'ACP login', loginUrl.replace(/password=[^&]+/, 'password=***'));
        const resp = await fetch(loginUrl);
        log('info', 'ACP login response', 'status=' + resp.status);
        const html = await resp.text();
        log('info', 'ACP login page', html.length + ' bytes, contains adsess=' + (html.includes('adsess=') ? 'yes' : 'no'));

        const match = html.match(/adsess=([a-f0-9]+)/);
        if (!match) {
            log('err', 'ACP login failed — no adsess token found in response');
            return null;
        }
        log('ok', 'ACP login success', 'adsess=' + match[1].substring(0, 8) + '...');
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
        const concurrency = 5;
        log('info', 'Starting profile fetch', total + ' characters from ' + forumBase + ' (concurrency=' + concurrency + ')');

        for (let i = 0; i < total; i += concurrency) {
            const batch = characterIds.slice(i, i + concurrency);
            sendStatus('profiles', 'Fetching profiles ' + (i + 1) + '-' + Math.min(i + concurrency, total) + '/' + total);

            const promises = batch.map(function(cid) {
                const url = forumBase + '/index.php?showuser=' + cid;
                log('info', 'Fetching profile', url);
                return fetch(url).then(function(resp) {
                    log('info', 'Profile response', 'id=' + cid + ' status=' + resp.status);
                    return resp.text().then(function(html) {
                        log('info', 'Profile fetched', 'id=' + cid + ' size=' + html.length + ' bytes');
                        return { character_id: cid, html: html };
                    });
                }).catch(function(e) {
                    log('err', 'Profile fetch failed', 'id=' + cid + ' error=' + e.message);
                    sendStatus('profiles', 'Failed to fetch profile ' + cid, e.message);
                    return null;
                });
            });

            var batchResults = await Promise.all(promises);
            for (var j = 0; j < batchResults.length; j++) {
                if (batchResults[j]) results.push(batchResults[j]);
            }

            // Polite delay between batches
            if (i + concurrency < total) {
                await sleep(1500);
            }
        }

        return results;
    }

    // ── Upload to Watcher ──

    async function uploadAcpDump(sqlContent) {
        const url = serverBase + '/api/acp/upload-dump';
        log('info', 'Uploading ACP dump', url + ' (' + Math.round(sqlContent.length/1024) + ' KB)');
        sendStatus('upload', 'Uploading ACP data to server...');
        try {
            const resp = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'text/plain' },
                body: sqlContent
            });
            log('info', 'ACP upload response', 'status=' + resp.status);
            if (!resp.ok) {
                const text = await resp.text();
                log('err', 'ACP upload failed', 'status=' + resp.status + ' body=' + text.substring(0, 200));
                throw new Error('Upload failed: HTTP ' + resp.status);
            }
            const result = await resp.json();
            log('ok', 'ACP upload success', JSON.stringify(result).substring(0, 200));
            return result;
        } catch (e) {
            log('err', 'ACP upload error', e.message + ' (payload was ' + Math.round(sqlContent.length/1024) + ' KB)');
            log('err', 'If "Failed to fetch", likely nginx client_max_body_size exceeded or CORS preflight failed');
            throw e;
        }
    }

    async function uploadProfiles(profiles) {
        if (!profiles.length) return { count: 0 };

        sendStatus('upload', 'Uploading ' + profiles.length + ' profiles to server...');
        log('info', 'Uploading profiles', profiles.length + ' profiles to ' + serverBase);

        // Send in batches of 10 to avoid huge payloads
        const batchSize = 10;
        let totalUploaded = 0;

        for (let i = 0; i < profiles.length; i += batchSize) {
            const batch = profiles.slice(i, i + batchSize);
            sendStatus('upload', 'Uploading profiles...', (i + batch.length) + '/' + profiles.length);

            const url = serverBase + '/api/profiles/upload-html';
            try {
                log('info', 'Uploading batch', (i + 1) + '-' + (i + batch.length) + ' of ' + profiles.length + ' to ' + url);
                const resp = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ profiles: batch })
                });
                log('info', 'Batch response', 'status=' + resp.status);
                if (resp.ok) {
                    totalUploaded += batch.length;
                } else {
                    const text = await resp.text();
                    log('err', 'Batch upload failed', 'status=' + resp.status + ' body=' + text.substring(0, 200));
                }
            } catch (e) {
                log('err', 'Batch upload error', e.message);
            }
        }

        return { count: totalUploaded };
    }

    // ── Main Sync Orchestrator ──

    async function testCorsConnectivity() {
        log('info', '── CORS connectivity test ──', serverBase + '/health');
        try {
            const resp = await fetch(serverBase + '/health', { mode: 'cors' });
            const body = await resp.text();
            log('ok', 'CORS test passed', 'status=' + resp.status + ' body=' + body.substring(0, 100));
            return true;
        } catch (e) {
            log('err', 'CORS test FAILED', e.message + ' — the server may not allow requests from ' + window.location.origin);
            log('err', 'Check that CORS allows origin: ' + window.location.origin);
            return false;
        }
    }

    async function runSync(config) {
        const summary = { acp: null, profiles: null, errors: [] };
        log('info', 'runSync started', 'server=' + serverBase + ' acp=' + !!config.acp_username + ' profiles=' + (config.character_ids || []).length);
        log('info', 'Bridge origin', window.location.origin);

        // Test CORS before doing real work
        const corsOk = await testCorsConnectivity();
        if (!corsOk) {
            summary.errors.push('Cannot reach server from JCink (CORS blocked). Server must allow origin: ' + window.location.origin);
            sendError('CORS blocked — server at ' + serverBase + ' does not allow requests from ' + window.location.origin);
            return;
        }

        try {
            // Phase 1: ACP dump (if credentials provided)
            if (config.acp_username && config.acp_password) {
                log('info', '── Phase 1: ACP Dump ──');
                sendStatus('acp', 'Logging into ACP...');
                const adsess = await acpLogin(config.acp_username, config.acp_password);
                if (!adsess) {
                    summary.errors.push('ACP login failed — check credentials');
                    sendStatus('acp', 'ACP login failed');
                } else {
                    const sql = await acpDump(adsess);
                    if (sql) {
                        log('info', 'SQL dump obtained', sql.length + ' bytes');
                        const result = await uploadAcpDump(sql);
                        summary.acp = {
                            size_kb: Math.round(sql.length / 1024),
                            server_response: result
                        };
                        sendStatus('acp', 'ACP data uploaded (' + Math.round(sql.length / 1024) + ' KB)');
                    } else {
                        log('err', 'SQL file was null — dump may have failed or file not ready');
                        summary.errors.push('SQL file not generated');
                        sendStatus('acp', 'SQL file not generated');
                    }
                }
            }

            // Phase 2: Profile sync (if character IDs provided)
            if (config.character_ids && config.character_ids.length > 0) {
                log('info', '── Phase 2: Profile Sync ──', config.character_ids.length + ' characters');
                sendStatus('profiles', 'Starting profile sync...', config.character_ids.length + ' characters');
                const profiles = await fetchProfiles(config.character_ids);
                if (profiles.length > 0) {
                    const result = await uploadProfiles(profiles);
                    summary.profiles = {
                        fetched: profiles.length,
                        uploaded: result.count
                    };
                    log('ok', 'Profile sync done', 'fetched=' + profiles.length + ' uploaded=' + result.count);
                    sendStatus('profiles', 'Profiles uploaded', result.count + ' of ' + profiles.length);
                } else {
                    log('warn', 'No profiles were fetched successfully');
                }
            }

        } catch (e) {
            log('err', 'runSync caught exception', e.message + '\n' + e.stack);
            summary.errors.push(e.message);
            sendError(e.message);
        }

        log('info', 'Sync complete', JSON.stringify(summary).substring(0, 300));
        sendComplete(summary);
    }

    // ── Message Handler ──

    window.addEventListener('message', function (event) {
        const data = event.data;
        if (!data || data.type !== 'watcher-sync-start') return;

        // Store parent origin for responses
        parentOrigin = event.origin;
        serverBase = data.server_base || '';

        console.log('[BrowserSync] Received sync-start from', event.origin, 'server_base=' + serverBase);
        log('info', 'Received sync-start command', 'from=' + event.origin + ' server=' + serverBase);

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
        console.log('[BrowserSync] Bridge loaded on', window.location.href, '— sending ready signal');
        // Broadcast ready to any parent — they'll filter by type
        window.parent.postMessage({ type: 'watcher-sync-ready' }, '*');
    }
})();
