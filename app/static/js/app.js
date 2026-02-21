/* HTMX configuration and sort/search helpers */

/* Sort direction toggle on sortable column headers */
document.addEventListener('click', function(e) {
    var th = e.target.closest('.sortable');
    if (!th) return;

    var currentUrl = new URL(th.getAttribute('hx-get'), window.location.origin);
    var currentDir = currentUrl.searchParams.get('dir') || 'asc';
    var newDir = currentDir === 'asc' ? 'desc' : 'asc';
    currentUrl.searchParams.set('dir', newDir);
    th.setAttribute('hx-get', currentUrl.pathname + currentUrl.search);

    /* Update arrow indicators */
    th.closest('thead').querySelectorAll('.sortable').forEach(function(h) {
        h.classList.remove('sort-asc', 'sort-desc');
    });
    th.classList.add(newDir === 'asc' ? 'sort-asc' : 'sort-desc');

    htmx.process(th);
});

/* Search keyword highlighting for quotes after HTMX swap */
document.body.addEventListener('htmx:afterSwap', function(event) {
    var searchInput = document.querySelector('input[name="q"]');
    if (!searchInput || !searchInput.value) return;

    var query = searchInput.value.trim();
    if (query.length < 2) return;

    event.detail.target.querySelectorAll('.quote-text').forEach(function(el) {
        var text = el.textContent;
        var escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        var regex = new RegExp('(' + escaped + ')', 'gi');
        el.innerHTML = text.replace(regex, '<mark>$1</mark>');
    });
});

/* Chip toggle helper */
document.addEventListener('click', function(e) {
    var chip = e.target.closest('.chip[data-toggle]');
    if (!chip) return;
    chip.classList.toggle('active');
});

/* HTMX: handle 401 from partials by redirecting to login */
document.body.addEventListener('htmx:responseError', function(event) {
    if (event.detail.xhr && event.detail.xhr.status === 401) {
        window.location.href = '/login';
    }
});

/* === Debug Panel === */

function toggleDebugPanel() {
    var panel = document.getElementById('debug-panel');
    if (!panel) return;
    panel.classList.toggle('open');
    /* Auto-scroll to bottom when opening */
    if (panel.classList.contains('open')) {
        var log = document.getElementById('debug-log');
        if (log) {
            setTimeout(function() { log.scrollTop = log.scrollHeight; }, 100);
        }
    }
}

function exportDebugLog() {
    var log = document.getElementById('debug-log');
    if (!log) return;
    var entries = log.querySelectorAll('.debug-entry');
    var lines = ['Debug Log Export', '='.repeat(60), ''];
    entries.forEach(function(entry) {
        var time = entry.querySelector('.debug-time');
        var msg = entry.querySelector('.debug-msg');
        if (time && msg) {
            lines.push('[' + time.textContent.trim() + '] ' + msg.textContent.trim());
        }
    });
    var text = lines.join('\n');
    var blob = new Blob([text], { type: 'text/plain' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'debug-log-' + new Date().toISOString().slice(0, 19).replace(/:/g, '-') + '.txt';
    a.click();
    URL.revokeObjectURL(url);
}

function clearDebugLog() {
    fetch('/htmx/debug-log/clear', { method: 'POST' });
    var log = document.getElementById('debug-log');
    if (log) log.innerHTML = '<div class="debug-entry"><span class="text-comment">Log cleared</span></div>';
}

/* Auto-scroll debug log when new entries arrive */
document.body.addEventListener('htmx:afterSwap', function(event) {
    if (event.detail.target && event.detail.target.id === 'debug-log') {
        var panel = document.getElementById('debug-panel');
        if (panel && panel.classList.contains('open')) {
            var log = event.detail.target;
            log.scrollTop = log.scrollHeight;
        }
    }
});
