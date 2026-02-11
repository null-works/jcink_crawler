/* HTMX configuration and sort/search helpers */

/* Sort direction toggle on sortable column headers */
document.addEventListener('click', function(e) {
    const th = e.target.closest('.sortable');
    if (!th) return;

    const currentUrl = new URL(th.getAttribute('hx-get'), window.location.origin);
    const currentDir = currentUrl.searchParams.get('dir') || 'asc';
    const newDir = currentDir === 'asc' ? 'desc' : 'asc';
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
