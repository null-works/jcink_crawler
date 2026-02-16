"""The Watcher TUI — interactive terminal dashboard for the crawler service.

Requires: textual

Usage:
    python tui.py                         # default URL
    python tui.py --url https://host:8943  # custom URL
    python tui.py --interval 10           # slower refresh
"""

import click
import httpx
from datetime import datetime
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Static
from textual import work

# Dracula palette
_BG = "#282a36"
_CURRENT = "#44475a"
_FG = "#f8f8f2"
_COMMENT = "#6272a4"
_CYAN = "#8be9fd"
_GREEN = "#50fa7b"
_ORANGE = "#ffb86c"
_PINK = "#ff79c6"
_PURPLE = "#bd93f9"
_RED = "#ff5555"
_YELLOW = "#f1fa8c"


def _format_time(ts: str | None) -> str:
    if not ts:
        return "Never"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        delta = datetime.now(dt.tzinfo) - dt if dt.tzinfo else datetime.now() - dt
        minutes = int(delta.total_seconds() / 60)
        if minutes < 1:
            return "Just now"
        elif minutes < 60:
            return f"{minutes}m ago"
        elif minutes < 1440:
            return f"{minutes // 60}h ago"
        else:
            return f"{minutes // 1440}d ago"
    except Exception:
        return ts[:19] if len(ts) > 19 else ts


class CharacterDetailScreen(Screen):
    """Detail view for a single character — threads + info."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
    ]

    CSS = """
    CharacterDetailScreen {
        layout: vertical;
        background: #282a36;
    }
    #detail-header {
        height: auto;
        max-height: 5;
        padding: 0 1;
        background: #6272a4;
        color: #f8f8f2;
        border-bottom: solid #bd93f9;
    }
    #thread-table {
        height: 1fr;
        background: #282a36;
        scrollbar-background: #44475a;
        scrollbar-color: #bd93f9;
        scrollbar-color-hover: #ff79c6;
        scrollbar-color-active: #ff79c6;
    }
    """

    def __init__(self, base_url: str, char_id: str):
        super().__init__()
        self.base_url = base_url
        self.char_id = char_id

    def compose(self) -> ComposeResult:
        yield Static("Loading...", id="detail-header")
        yield DataTable(id="thread-table")
        yield Footer()

    def on_mount(self):
        table = self.query_one("#thread-table", DataTable)
        table.add_columns("Title", "Category", "Last Poster", "Status")
        table.cursor_type = "row"
        self.load_detail()

    @work(thread=True)
    def load_detail(self):
        try:
            with httpx.Client(timeout=15.0) as client:
                char_data = client.get(f"{self.base_url}/api/character/{self.char_id}").json()
                threads_data = client.get(f"{self.base_url}/api/character/{self.char_id}/threads").json()
                quote_data = client.get(f"{self.base_url}/api/character/{self.char_id}/quote-count").json()
            self.app.call_from_thread(self._update_detail, char_data, threads_data, quote_data)
        except Exception as e:
            self.app.call_from_thread(self._show_error, str(e))

    def _update_detail(self, char_data, threads_data, quote_data):
        char = char_data.get("character", {})
        counts = threads_data.get("counts", {})

        header = self.query_one("#detail-header", Static)
        header.update(
            f"[bold #f8f8f2]{char.get('name', '?')}[/]  "
            f"[#6272a4]{char.get('group_name', '')}[/]  "
            f"ID: {char.get('id', '')}  "
            f"Quotes: [bold #ff79c6]{quote_data.get('count', 0)}[/]\n"
            f"Threads: [bold #f8f8f2]{counts.get('total', 0)}[/]  "
            f"[#50fa7b]{counts.get('ongoing', 0)} ongoing[/]  "
            f"[#8be9fd]{counts.get('comms', 0)} comms[/]  "
            f"[#ff79c6]{counts.get('complete', 0)} complete[/]  "
            f"[#f1fa8c]{counts.get('incomplete', 0)} incomplete[/]"
        )

        table = self.query_one("#thread-table", DataTable)
        table.clear()

        for cat in ["ongoing", "comms", "complete", "incomplete"]:
            for t in threads_data.get(cat, []):
                status = "Replied" if t.get("is_user_last_poster") else "Awaiting"
                table.add_row(
                    t.get("title", "")[:50],
                    cat,
                    t.get("last_poster_name") or "—",
                    status,
                )

    def _show_error(self, msg):
        header = self.query_one("#detail-header", Static)
        header.update(f"[#ff5555]Error: {msg}[/]")


class WatcherApp(App):
    """Main dashboard — character list with live stats."""

    TITLE = "The Watcher"
    SUB_TITLE = "Loading..."

    CSS = """
    Screen {
        background: #282a36;
    }
    Header {
        dock: top;
        background: #bd93f9;
        color: #282a36;
    }
    HeaderTitle {
        color: #282a36;
        text-style: bold;
    }
    #filter-input {
        margin: 0 1;
        background: #44475a;
        color: #f8f8f2;
        border: solid #bd93f9;
    }
    #filter-input:focus {
        border: solid #ff79c6;
    }
    #char-table {
        height: 1fr;
        background: #282a36;
        scrollbar-background: #44475a;
        scrollbar-color: #bd93f9;
        scrollbar-color-hover: #ff79c6;
        scrollbar-color-active: #ff79c6;
    }
    DataTable > .datatable--header {
        background: #6272a4;
        color: #bd93f9;
        text-style: bold;
    }
    DataTable > .datatable--cursor {
        background: #44475a;
    }
    DataTable:focus > .datatable--cursor {
        background: #6272a4;
    }
    DataTable > .datatable--even-row {
        background: #282a36;
    }
    DataTable > .datatable--odd-row {
        background: #2d2f3d;
    }
    #activity-bar {
        height: 1;
        width: 100%;
        background: #44475a;
        color: #50fa7b;
        padding: 0 2;
        text-style: bold;
    }
    #activity-bar.idle {
        background: #44475a;
        color: #6272a4;
    }
    #activity-bar.active {
        background: #44475a;
        color: #50fa7b;
    }
    Footer {
        background: #bd93f9;
        color: #282a36;
    }
    Footer > .footer--highlight {
        background: #ff79c6;
        color: #282a36;
    }
    Footer > .footer--highlight-key {
        background: #ff79c6;
        color: #282a36;
    }
    Footer > .footer--key {
        background: #44475a;
        color: #bd93f9;
    }
    Footer > .footer--description {
        color: #282a36;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("slash", "focus_filter", "/Filter"),
        Binding("r", "refresh", "Refresh"),
        Binding("escape", "clear_filter", "Clear"),
    ]

    def __init__(self, base_url: str, interval: int = 5):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.interval = interval
        self.all_chars: list = []
        self.filter_text = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Input(placeholder="Type to filter by name or affiliation...", id="filter-input")
        yield DataTable(id="char-table")
        yield Static("Idle", id="activity-bar", classes="idle")
        yield Footer()

    def on_mount(self):
        table = self.query_one("#char-table", DataTable)
        table.add_columns("ID", "Name", "Affiliation", "Tot", "OG", "CM", "CP", "IC", "Crawled")
        table.cursor_type = "row"
        table.cursor_foreground_priority = "renderable"
        table.focus()
        self.refresh_data()
        self.set_interval(self.interval, self.refresh_data)

    @work(thread=True)
    def refresh_data(self):
        try:
            with httpx.Client(timeout=10.0) as client:
                status = client.get(f"{self.base_url}/api/status").json()
                chars = client.get(f"{self.base_url}/api/characters").json()
            self.call_from_thread(self._update_ui, status, chars)
        except Exception:
            pass

    def _update_ui(self, status, chars):
        self.sub_title = (
            f"Characters: {status.get('characters_tracked', 0)}   "
            f"Threads: {status.get('total_threads', 0)}   "
            f"Quotes: {status.get('total_quotes', 0)}"
        )

        activity_bar = self.query_one("#activity-bar", Static)
        activity = status.get("current_activity")
        if activity:
            activity_bar.update(f"\u25b6 {activity['activity']}")
            activity_bar.set_classes("active")
        else:
            activity_bar.update("Idle — waiting for next scheduled crawl")
            activity_bar.set_classes("idle")

        self.all_chars = chars or []
        self._rebuild_table()

    def _rebuild_table(self):
        table = self.query_one("#char-table", DataTable)

        try:
            cursor_row = table.cursor_row
        except Exception:
            cursor_row = 0

        table.clear()

        filtered = self.all_chars
        if self.filter_text:
            ft = self.filter_text.lower()
            filtered = [
                c for c in filtered
                if ft in c["name"].lower()
                or ft in (c.get("affiliation") or "").lower()
            ]

        for char in filtered:
            counts = char.get("thread_counts", {})
            table.add_row(
                Text.from_markup(f"[bold bright_magenta]{char['id']}[/]"),
                Text.from_markup(f"[bold white]{char['name'][:22]}[/]"),
                Text.from_markup(f"[italic bright_cyan]{(char.get('affiliation') or '—')[:20]}[/]"),
                Text.from_markup(f"[bold yellow]{counts.get('total', 0)}[/]"),
                Text.from_markup(f"[bold bright_green]{counts.get('ongoing', 0)}[/]"),
                Text.from_markup(f"[bold bright_cyan]{counts.get('comms', 0)}[/]"),
                Text.from_markup(f"[bold bright_magenta]{counts.get('complete', 0)}[/]"),
                Text.from_markup(f"[bold bright_yellow]{counts.get('incomplete', 0)}[/]"),
                Text.from_markup(f"[bright_yellow]{_format_time(char.get('last_thread_crawl'))}[/]"),
                key=char["id"],
            )

        if filtered:
            safe_row = min(cursor_row, len(filtered) - 1)
            if safe_row >= 0:
                table.move_cursor(row=safe_row)

    def on_input_changed(self, event: Input.Changed):
        if event.input.id == "filter-input":
            self.filter_text = event.value
            self._rebuild_table()

    def action_focus_filter(self):
        self.query_one("#filter-input", Input).focus()

    def action_clear_filter(self):
        inp = self.query_one("#filter-input", Input)
        inp.value = ""
        self.filter_text = ""
        self._rebuild_table()
        self.query_one("#char-table", DataTable).focus()

    def action_refresh(self):
        self.refresh_data()

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        char_id = str(event.row_key.value)
        self.push_screen(CharacterDetailScreen(self.base_url, char_id))


DEFAULT_BASE = "https://imagehut.ch:8943"


@click.command()
@click.option("--url", default=DEFAULT_BASE, envvar="CRAWLER_URL",
              help="Crawler service URL")
@click.option("--interval", "-i", default=5, help="Refresh interval in seconds")
def main(url, interval):
    """The Watcher — interactive TUI dashboard."""
    app = WatcherApp(base_url=url, interval=interval)
    app.run()


if __name__ == "__main__":
    main()
