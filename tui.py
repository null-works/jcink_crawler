"""The Watcher TUI — interactive terminal dashboard for the crawler service.

Requires: textual

Usage:
    python tui.py                         # default URL
    python tui.py --url http://host:8943  # custom URL
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
    }
    #detail-header {
        height: auto;
        max-height: 5;
        padding: 0 1;
        background: $boost;
    }
    #thread-table {
        height: 1fr;
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
            f"[bold]{char.get('name', '?')}[/]  "
            f"[dim]{char.get('group_name', '')}[/]  "
            f"ID: {char.get('id', '')}  "
            f"Quotes: [bold]{quote_data.get('count', 0)}[/]\n"
            f"Threads: [bold]{counts.get('total', 0)}[/]  "
            f"[green]{counts.get('ongoing', 0)} ongoing[/]  "
            f"[blue]{counts.get('comms', 0)} comms[/]  "
            f"[magenta]{counts.get('complete', 0)} complete[/]  "
            f"[yellow]{counts.get('incomplete', 0)} incomplete[/]"
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
        header.update(f"[red]Error: {msg}[/]")


class WatcherApp(App):
    """Main dashboard — character list with live stats."""

    TITLE = "The Watcher"
    SUB_TITLE = "Loading..."

    CSS = """
    #filter-input {
        margin: 0 1;
    }
    #char-table {
        height: 1fr;
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
        yield Footer()

    def on_mount(self):
        table = self.query_one("#char-table", DataTable)
        table.add_columns("ID", "Name", "Affiliation", "Tot", "OG", "CM", "CP", "IC", "Crawled")
        table.cursor_type = "row"
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
            f"Quotes: {status.get('total_quotes', 0)}   "
            f"(every {self.interval}s)"
        )
        self.all_chars = chars or []
        self._rebuild_table()

    def _rebuild_table(self):
        table = self.query_one("#char-table", DataTable)

        # Preserve cursor position
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
                Text(char["id"], style="dim"),
                Text(char["name"][:22], style="bold"),
                Text((char.get("affiliation") or "—")[:20], style="cyan"),
                Text(str(counts.get("total", 0)), style="bold white"),
                Text(str(counts.get("ongoing", 0)), style="green"),
                Text(str(counts.get("comms", 0)), style="dodger_blue1"),
                Text(str(counts.get("complete", 0)), style="magenta"),
                Text(str(counts.get("incomplete", 0)), style="yellow"),
                Text(_format_time(char.get("last_thread_crawl")), style="dim"),
                key=char["id"],
            )

        # Restore cursor
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


DEFAULT_BASE = "http://imagehut.ch:8943"


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
