#!/usr/bin/env python3
"""The Watcher CLI — interface with the running crawler service.

Usage:
    python cli.py status
    python cli.py register 42
    python cli.py characters
    python cli.py threads 42
    python cli.py quotes 42
    python cli.py crawl 42 --type threads
    python cli.py watch
"""

import click
import httpx
import time
import sys
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich.live import Live
from rich.layout import Layout
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich import box

console = Console()

DEFAULT_BASE = "http://imagehut.ch:8943"


class CrawlerClient:
    """HTTP client wrapper for the crawler API."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=30.0)

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _get(self, path: str) -> dict | list | None:
        try:
            resp = self.client.get(self._url(path))
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            console.print("[red bold]✗ Cannot connect to crawler service[/]")
            console.print(f"  Is it running at [cyan]{self.base_url}[/]?")
            sys.exit(1)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            console.print(f"[red]HTTP {e.response.status_code}:[/] {e.response.text}")
            return None

    def _post(self, path: str, data: dict) -> dict | None:
        try:
            resp = self.client.post(self._url(path), json=data)
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            console.print("[red bold]✗ Cannot connect to crawler service[/]")
            console.print(f"  Is it running at [cyan]{self.base_url}[/]?")
            sys.exit(1)
        except httpx.HTTPStatusError as e:
            console.print(f"[red]HTTP {e.response.status_code}:[/] {e.response.text}")
            return None

    def health(self) -> bool:
        try:
            resp = self.client.get(self._url("/health"))
            return resp.status_code == 200
        except Exception:
            return False

    def status(self) -> dict | None:
        return self._get("/api/status")

    def characters(self) -> list | None:
        return self._get("/api/characters")

    def character(self, cid: str) -> dict | None:
        return self._get(f"/api/character/{cid}")

    def threads(self, cid: str) -> dict | None:
        return self._get(f"/api/character/{cid}/threads")

    def thread_counts(self, cid: str) -> dict | None:
        return self._get(f"/api/character/{cid}/thread-counts")

    def quotes(self, cid: str) -> list | None:
        return self._get(f"/api/character/{cid}/quotes")

    def random_quote(self, cid: str) -> dict | None:
        return self._get(f"/api/character/{cid}/quote")

    def quote_count(self, cid: str) -> dict | None:
        return self._get(f"/api/character/{cid}/quote-count")

    def register(self, user_id: str) -> dict | None:
        return self._post("/api/character/register", {"user_id": user_id})

    def trigger_crawl(self, cid: str, crawl_type: str) -> dict | None:
        return self._post("/api/crawl/trigger", {
            "character_id": cid,
            "crawl_type": crawl_type,
        })


# --- CLI Group ---

@click.group()
@click.option("--url", default=DEFAULT_BASE, envvar="CRAWLER_URL",
              help="Crawler service URL (default: http://imagehut.ch:8943)")
@click.pass_context
def cli(ctx, url):
    """The Watcher CLI — manage and inspect the crawler service."""
    ctx.ensure_object(dict)
    ctx.obj["client"] = CrawlerClient(url)


# --- Status ---

@cli.command()
@click.pass_context
def status(ctx):
    """Show service status and stats."""
    client: CrawlerClient = ctx.obj["client"]

    healthy = client.health()
    data = client.status()

    if not healthy or not data:
        console.print("[red bold]✗ Service unavailable[/]")
        return

    table = Table(title="Crawler Service Status", box=box.ROUNDED, show_header=False)
    table.add_column("Key", style="bold cyan")
    table.add_column("Value", style="white")

    table.add_row("Status", "[green bold]● Online[/]")
    table.add_row("Characters Tracked", str(data.get("characters_tracked", 0)))
    table.add_row("Total Threads", str(data.get("total_threads", 0)))
    table.add_row("Total Quotes", str(data.get("total_quotes", 0)))
    table.add_row("Last Thread Crawl", data.get("last_thread_crawl") or "Never")
    table.add_row("Last Profile Crawl", data.get("last_profile_crawl") or "Never")

    console.print(table)


# --- Characters ---

@cli.command()
@click.pass_context
def characters(ctx):
    """List all tracked characters."""
    client: CrawlerClient = ctx.obj["client"]
    data = client.characters()

    if not data:
        console.print("[yellow]No characters registered yet.[/]")
        console.print("Register one with: [cyan]python cli.py register <user_id>[/]")
        return

    table = Table(title="Tracked Characters", box=box.ROUNDED)
    table.add_column("ID", style="dim")
    table.add_column("Name", style="bold white")
    table.add_column("Group", style="cyan")
    table.add_column("Ongoing", justify="right", style="green")
    table.add_column("Comms", justify="right", style="blue")
    table.add_column("Complete", justify="right", style="magenta")
    table.add_column("Incomplete", justify="right", style="yellow")
    table.add_column("Total", justify="right", style="bold white")
    table.add_column("Last Crawl", style="dim")

    for char in data:
        counts = char.get("thread_counts", {})
        table.add_row(
            char["id"],
            char["name"],
            char.get("group_name") or "—",
            str(counts.get("ongoing", 0)),
            str(counts.get("comms", 0)),
            str(counts.get("complete", 0)),
            str(counts.get("incomplete", 0)),
            str(counts.get("total", 0)),
            _format_time(char.get("last_thread_crawl")),
        )

    console.print(table)


# --- Character Detail ---

@cli.command()
@click.argument("character_id")
@click.pass_context
def character(ctx, character_id):
    """Show detailed info for a character."""
    client: CrawlerClient = ctx.obj["client"]
    data = client.character(character_id)

    if not data:
        console.print(f"[red]Character {character_id} not found.[/]")
        return

    char = data["character"]
    fields = data.get("fields", {})
    threads = data.get("threads", {})

    # Character header
    header = Text()
    header.append(char["name"], style="bold white")
    if char.get("group_name"):
        header.append(f"  [{char['group_name']}]", style="dim cyan")
    header.append(f"\n  ID: {char['id']}", style="dim")
    header.append(f"\n  {char['profile_url']}", style="dim blue underline")

    console.print(Panel(header, title="Character", box=box.ROUNDED))

    # Thread counts
    if threads:
        counts = threads.get("counts", {})
        count_parts = []
        for cat, color in [("ongoing", "green"), ("comms", "blue"), ("complete", "magenta"), ("incomplete", "yellow")]:
            c = counts.get(cat, 0)
            if c > 0:
                count_parts.append(f"[{color}]{cat}: {c}[/]")
        if count_parts:
            console.print(f"\n  Threads: {' · '.join(count_parts)} · [bold]total: {counts.get('total', 0)}[/]")

    # Profile fields
    if fields:
        ft = Table(title="Profile Fields", box=box.SIMPLE, show_header=True)
        ft.add_column("Field", style="cyan")
        ft.add_column("Value", style="white", max_width=60)
        for key, val in sorted(fields.items()):
            display_val = val[:80] + "..." if len(val) > 80 else val
            ft.add_row(key, display_val)
        console.print(ft)

    # Quote count
    qc = client.quote_count(character_id)
    if qc:
        console.print(f"\n  Quotes stored: [bold]{qc.get('count', 0)}[/]")


# --- Threads ---

@cli.command()
@click.argument("character_id")
@click.option("--category", "-c", type=click.Choice(["ongoing", "comms", "complete", "incomplete", "all"]),
              default="all", help="Filter by category")
@click.pass_context
def threads(ctx, character_id, category):
    """Show threads for a character."""
    client: CrawlerClient = ctx.obj["client"]
    data = client.threads(character_id)

    if not data:
        console.print(f"[red]Character {character_id} not found.[/]")
        return

    console.print(f"\n  [bold]{data['character_name']}[/]'s Threads\n")

    categories = ["ongoing", "comms", "complete", "incomplete"] if category == "all" else [category]

    for cat in categories:
        thread_list = data.get(cat, [])
        if not thread_list:
            continue

        color = {"ongoing": "green", "comms": "blue", "complete": "magenta", "incomplete": "yellow"}[cat]

        table = Table(title=f"{cat.title()} ({len(thread_list)})", box=box.SIMPLE,
                      title_style=f"bold {color}")
        table.add_column("Title", style="white", max_width=50)
        table.add_column("Forum", style="dim")
        table.add_column("Last Poster", style="cyan")
        table.add_column("Status", justify="center")

        for t in thread_list:
            status_icon = "[green]✓[/] Replied" if t.get("is_user_last_poster") else "[yellow]⏳[/] Awaiting"
            table.add_row(
                t["title"][:50],
                t.get("forum_name") or "—",
                t.get("last_poster_name") or "—",
                status_icon,
            )

        console.print(table)


# --- Quotes ---

@cli.command()
@click.argument("character_id")
@click.option("--random", "-r", "show_random", is_flag=True, help="Show one random quote")
@click.option("--limit", "-n", default=20, help="Max quotes to show")
@click.pass_context
def quotes(ctx, character_id, show_random, limit):
    """Show quotes for a character."""
    client: CrawlerClient = ctx.obj["client"]

    if show_random:
        q = client.random_quote(character_id)
        if not q:
            console.print("[yellow]No quotes found for this character.[/]")
            return
        console.print(Panel(
            f'[italic]"{q["quote_text"]}"[/]\n\n[dim]— from: {q.get("source_thread_title", "Unknown")}[/]',
            title="Random Quote",
            box=box.ROUNDED,
        ))
        return

    data = client.quotes(character_id)
    if not data:
        console.print("[yellow]No quotes found for this character.[/]")
        return

    console.print(f"\n  [bold]Quotes[/] ({len(data)} total)\n")

    for i, q in enumerate(data[:limit]):
        quote_text = q["quote_text"]
        if len(quote_text) > 100:
            quote_text = quote_text[:100] + "..."
        source = q.get("source_thread_title") or "Unknown"
        console.print(f'  [dim]{i+1:3}.[/] [italic]"{quote_text}"[/]')
        console.print(f'       [dim]— {source}[/]\n')

    if len(data) > limit:
        console.print(f"  [dim]... and {len(data) - limit} more. Use --limit to show more.[/]")


# --- Register ---

@cli.command()
@click.argument("user_id")
@click.pass_context
def register(ctx, user_id):
    """Register a character for tracking by JCink user ID."""
    client: CrawlerClient = ctx.obj["client"]

    console.print(f"Registering user [cyan]{user_id}[/]...")

    result = client.register(user_id)
    if not result:
        console.print("[red]Registration failed.[/]")
        return

    status = result.get("status")
    if status == "already_registered":
        char = result.get("character", {})
        console.print(f"[yellow]Already registered:[/] [bold]{char.get('name', user_id)}[/]")
    elif status == "registering":
        console.print(f"[green]✓ Registration started for user {user_id}[/]")
        console.print("  Profile and thread crawl running in background.")
        console.print(f"  Check progress with: [cyan]python cli.py character {user_id}[/]")
    else:
        console.print(f"Result: {result}")


# --- Crawl Trigger ---

@cli.command()
@click.argument("character_id")
@click.option("--type", "crawl_type", type=click.Choice(["threads", "profile"]),
              default="threads", help="Type of crawl to trigger")
@click.pass_context
def crawl(ctx, character_id, crawl_type):
    """Manually trigger a crawl for a character."""
    client: CrawlerClient = ctx.obj["client"]

    console.print(f"Triggering [cyan]{crawl_type}[/] crawl for character [cyan]{character_id}[/]...")

    result = client.trigger_crawl(character_id, crawl_type)
    if not result:
        console.print("[red]Crawl trigger failed.[/]")
        return

    console.print(f"[green]✓ Crawl queued[/] — {crawl_type} for {character_id}")
    console.print(f"  Check progress with: [cyan]python cli.py character {character_id}[/]")


# --- Watch (live dashboard) ---

@cli.command()
@click.option("--interval", "-i", default=5, help="Refresh interval in seconds")
@click.pass_context
def watch(ctx, interval):
    """Live dashboard — auto-refreshing status view."""
    client: CrawlerClient = ctx.obj["client"]

    def _build_dashboard():
        """Build a compact dashboard that fits in a standard terminal."""
        from rich.console import Group

        data = client.status()
        if not data:
            return Text("[Service unavailable]", style="red bold")

        # Compact header line
        header = Text.assemble(
            ("● ", "green bold"),
            (f"{data.get('characters_tracked', 0)}", "bold"), " chars  ",
            (f"{data.get('total_threads', 0)}", "bold"), " threads  ",
            (f"{data.get('total_quotes', 0)}", "bold"), " quotes",
        )

        chars = client.characters()
        if not chars:
            return Group(header, Text("  No characters registered.", style="dim"))

        # Compact table: Name + thread counts combined + last crawl
        table = Table(box=None, show_header=True, pad_edge=False, padding=(0, 1))
        table.add_column("Name", style="bold white", no_wrap=True)
        table.add_column("Threads", justify="right", no_wrap=True)
        table.add_column("Crawled", style="dim", no_wrap=True)

        for char in chars:
            counts = char.get("thread_counts", {})
            og = counts.get("ongoing", 0)
            cm = counts.get("comms", 0)
            cp = counts.get("complete", 0)
            ic = counts.get("incomplete", 0)
            tot = counts.get("total", 0)

            # Compact thread counts: "12 (3/2/5/2)"
            thread_str = Text.assemble(
                (str(tot), "bold"),
                (" ", ""),
                (f"{og}", "green"), ("/", "dim"),
                (f"{cm}", "blue"), ("/", "dim"),
                (f"{cp}", "magenta"), ("/", "dim"),
                (f"{ic}", "yellow"),
            )

            table.add_row(
                char["name"][:18],
                thread_str,
                _format_time(char.get("last_thread_crawl")),
            )

        footer = Text(f"  {interval}s refresh | Ctrl+C to exit", style="dim")
        return Group(header, table, footer)

    try:
        with Live(_build_dashboard(), console=console, refresh_per_second=1, screen=True) as live:
            while True:
                time.sleep(interval)
                live.update(_build_dashboard())
    except KeyboardInterrupt:
        pass


# --- Helpers ---

def _format_time(ts: str | None) -> str:
    """Format a timestamp for display."""
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


if __name__ == "__main__":
    cli()
