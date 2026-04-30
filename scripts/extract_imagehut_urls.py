#!/usr/bin/env python3
"""Extract all imagehut.ch image URLs from the Watcher's SQLite database.

Scans every text column across all tables for imagehut.ch URLs and produces
a structured report suitable for rebuilding the Chevereto database.

Usage:
    python scripts/extract_imagehut_urls.py [path/to/crawler.db]

Outputs:
    - Console summary grouped by source table
    - CSV file:  imagehut_urls.csv   (url, source_table, context)
    - JSON file: imagehut_urls.json  (full structured data)
"""

import csv
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

# Match any imagehut.ch URL (with or without protocol, with path)
URL_PATTERN = re.compile(
    r'https?://(?:www\.)?imagehut\.ch/([^\s\'"<>\)\],;]+)',
    re.IGNORECASE,
)

# Chevereto URL patterns — extract the image path components
# Typical Chevereto URLs:
#   https://imagehut.ch/images/2024/03/15/filename.png       (direct image)
#   https://imagehut.ch/image/slug.XXXX                       (image page)
#   https://imagehut.ch/album/slug.XXXX                       (album page)
#   https://imagehut.ch/images/2024/03/15/filename.md.png     (thumbnail)
IMAGE_PATH_RE = re.compile(
    r'^images/(\d{4})/(\d{2})/(\d{2})/(.+)$'
)


def extract_urls_from_text(text: str) -> list[str]:
    """Find all imagehut.ch URLs in a string."""
    if not text or "imagehut.ch" not in text.lower():
        return []
    matches = URL_PATTERN.findall(text)
    # Reconstruct full URLs
    return [f"https://imagehut.ch/{m}" for m in matches]


def parse_image_path(url: str) -> dict | None:
    """Parse a Chevereto direct image URL into components.
    
    Returns dict with year, month, day, filename, is_thumbnail
    or None if not a direct image URL.
    """
    parsed = urlparse(url)
    path = parsed.path.lstrip("/")
    m = IMAGE_PATH_RE.match(path)
    if not m:
        return None
    year, month, day, filename = m.groups()
    # Detect thumbnails (.md.ext or .th.ext)
    is_thumbnail = bool(re.search(r'\.(md|th)\.\w+$', filename))
    # Get the full-size filename
    full_filename = re.sub(r'\.(md|th)(\.\w+)$', r'\2', filename)
    return {
        "year": year,
        "month": month,
        "day": day,
        "filename": filename,
        "full_filename": full_filename,
        "is_thumbnail": is_thumbnail,
        "r2_path": f"images/{year}/{month}/{day}/{full_filename}",
    }


def get_all_tables(conn: sqlite3.Connection) -> list[str]:
    """Get all table names in the database."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    return [row[0] for row in cursor.fetchall()]


def get_text_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Get all TEXT columns for a table."""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return [
        row[1] for row in cursor.fetchall()
        if row[2].upper() in ("TEXT", "")  # SQLite is flexible with types
    ]


def scan_table(conn: sqlite3.Connection, table: str, text_cols: list[str]) -> list[dict]:
    """Scan a table for imagehut.ch URLs in all text columns."""
    results = []
    if not text_cols:
        return results

    # Get the primary key column(s) for context
    cursor = conn.execute(f"PRAGMA table_info({table})")
    columns_info = cursor.fetchall()
    pk_cols = [col[1] for col in columns_info if col[5] > 0]  # col[5] = pk flag
    if not pk_cols:
        pk_cols = [columns_info[0][1]] if columns_info else []

    # Build SELECT with pk + text columns
    select_cols = list(set(pk_cols + text_cols))
    query = f"SELECT {', '.join(select_cols)} FROM {table}"
    
    cursor = conn.execute(query)
    for row_tuple in cursor.fetchall():
        row = dict(zip(select_cols, row_tuple))
        pk_value = {k: row.get(k) for k in pk_cols}
        
        for col in text_cols:
            value = row.get(col)
            if not value or not isinstance(value, str):
                continue
            urls = extract_urls_from_text(value)
            for url in urls:
                parsed = parse_image_path(url)
                results.append({
                    "url": url,
                    "table": table,
                    "column": col,
                    "row_id": pk_value,
                    "r2_path": parsed["r2_path"] if parsed else None,
                    "filename": parsed["full_filename"] if parsed else None,
                    "is_thumbnail": parsed["is_thumbnail"] if parsed else False,
                    "url_type": "direct_image" if parsed else "page_or_album",
                })
    return results


def main():
    # Find the database
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    else:
        # Try common locations
        candidates = [
            "./data/crawler.db",
            "../data/crawler.db",
            "/app/data/crawler.db",
        ]
        db_path = None
        for c in candidates:
            if os.path.exists(c):
                db_path = c
                break
        if not db_path:
            print("Usage: python scripts/extract_imagehut_urls.py <path/to/crawler.db>")
            print("\nCould not find crawler.db in default locations.")
            sys.exit(1)

    print(f"Scanning: {db_path}")
    print(f"Size: {os.path.getsize(db_path) / 1024 / 1024:.1f} MB")
    print()

    conn = sqlite3.connect(db_path)
    
    all_results = []
    tables = get_all_tables(conn)
    
    for table in tables:
        text_cols = get_text_columns(conn, table)
        if not text_cols:
            continue
        results = scan_table(conn, table, text_cols)
        if results:
            all_results.extend(results)
            print(f"  {table}: {len(results)} URLs found across {len(text_cols)} text columns")

    conn.close()

    if not all_results:
        print("\nNo imagehut.ch URLs found in the database.")
        return

    # Deduplicate by URL
    unique_urls = {}
    for r in all_results:
        url = r["url"]
        if url not in unique_urls:
            unique_urls[url] = {
                "url": url,
                "r2_path": r["r2_path"],
                "filename": r["filename"],
                "is_thumbnail": r["is_thumbnail"],
                "url_type": r["url_type"],
                "sources": [],
            }
        unique_urls[url]["sources"].append({
            "table": r["table"],
            "column": r["column"],
            "row_id": r["row_id"],
        })

    # Summary
    print(f"\n{'='*60}")
    print(f"RESULTS")
    print(f"{'='*60}")
    print(f"  Total URL occurrences: {len(all_results)}")
    print(f"  Unique URLs: {len(unique_urls)}")
    
    # Break down by type
    direct = [u for u in unique_urls.values() if u["url_type"] == "direct_image"]
    pages = [u for u in unique_urls.values() if u["url_type"] == "page_or_album"]
    thumbnails = [u for u in unique_urls.values() if u["is_thumbnail"]]
    
    print(f"  Direct image URLs: {len(direct)}")
    print(f"  Page/album URLs: {len(pages)}")
    print(f"  Thumbnails: {len(thumbnails)}")

    # Break down by source table
    print(f"\n  By source table:")
    table_counts = defaultdict(int)
    for r in all_results:
        table_counts[r["table"]] += 1
    for table, count in sorted(table_counts.items(), key=lambda x: -x[1]):
        print(f"    {table}: {count}")

    # Break down by column
    print(f"\n  By column:")
    col_counts = defaultdict(int)
    for r in all_results:
        col_counts[f"{r['table']}.{r['column']}"] += 1
    for col, count in sorted(col_counts.items(), key=lambda x: -x[1]):
        print(f"    {col}: {count}")

    # Show R2 path mappings for direct images
    if direct:
        print(f"\n  R2 path mappings (direct images):")
        for u in sorted(direct, key=lambda x: x["r2_path"] or "")[:20]:
            print(f"    {u['url']}")
            print(f"      → R2: {u['r2_path']}")
        if len(direct) > 20:
            print(f"    ... and {len(direct) - 20} more")

    # ── Character association ──
    # Build a per-character image ownership map from profile_fields + characters
    print(f"\n{'='*60}")
    print(f"CHARACTER → IMAGE ASSOCIATIONS")
    print(f"{'='*60}")

    conn2 = sqlite3.connect(db_path)
    conn2.row_factory = sqlite3.Row

    # Get character names for display
    char_names = {}
    for row in conn2.execute("SELECT id, name FROM characters"):
        char_names[row["id"]] = row["name"]

    # Image fields we care about from profile_fields
    image_field_keys = [
        "square_image", "portrait_image", "secondary_square_image",
        "rectangle_gif", "avatar_url", "header_image", "banner_image",
        "face_claim_image",
    ]
    # Also grab any field whose value contains imagehut.ch
    cursor = conn2.execute(
        "SELECT character_id, field_key, field_value FROM profile_fields "
        "WHERE field_value LIKE '%imagehut.ch%'"
    )
    
    char_images: dict[str, list[dict]] = defaultdict(list)
    for row in cursor.fetchall():
        char_id = row["character_id"]
        field_key = row["field_key"]
        field_value = row["field_value"]
        urls = extract_urls_from_text(field_value)
        for url in urls:
            parsed = parse_image_path(url)
            char_images[char_id].append({
                "url": url,
                "field_key": field_key,
                "r2_path": parsed["r2_path"] if parsed else None,
                "filename": parsed["full_filename"] if parsed else None,
                "is_thumbnail": parsed["is_thumbnail"] if parsed else False,
            })

    # Also check characters.avatar_url
    for row in conn2.execute(
        "SELECT id, avatar_url FROM characters WHERE avatar_url LIKE '%imagehut.ch%'"
    ):
        char_id = row["id"]
        urls = extract_urls_from_text(row["avatar_url"])
        for url in urls:
            parsed = parse_image_path(url)
            char_images[char_id].append({
                "url": url,
                "field_key": "_avatar_url",
                "r2_path": parsed["r2_path"] if parsed else None,
                "filename": parsed["full_filename"] if parsed else None,
                "is_thumbnail": parsed["is_thumbnail"] if parsed else False,
            })

    conn2.close()

    # Print per-character summary
    total_char_images = 0
    chars_with_images = 0
    for char_id in sorted(char_images.keys(), key=lambda x: int(x) if x.isdigit() else 0):
        images = char_images[char_id]
        char_name = char_names.get(char_id, f"Unknown ({char_id})")
        chars_with_images += 1
        total_char_images += len(images)
        # Dedupe by URL for display
        unique = {img["url"]: img for img in images}
        print(f"\n  [{char_id}] {char_name} — {len(unique)} images")
        for img in unique.values():
            field = img["field_key"]
            print(f"    {field}: {img['url']}")
            if img["r2_path"]:
                print(f"      → R2: {img['r2_path']}")

    print(f"\n  Characters with imagehut.ch images: {chars_with_images}")
    print(f"  Total character-image associations: {total_char_images}")

    # ── Write outputs ──
    output_dir = Path(db_path).parent if Path(db_path).parent.exists() else Path(".")

    # CSV — all URL occurrences with character association where available
    csv_path = output_dir / "imagehut_urls.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "url", "r2_path", "filename", "url_type", "is_thumbnail",
            "source_table", "source_column", "row_id",
            "character_id", "character_name", "field_key"
        ])
        for r in all_results:
            # Try to resolve character association
            char_id = ""
            char_name = ""
            field_key = ""
            row_id = r["row_id"]
            if r["table"] == "profile_fields" and "character_id" in row_id:
                char_id = row_id["character_id"]
                char_name = char_names.get(char_id, "")
                field_key = row_id.get("field_key", r["column"])
            elif r["table"] == "characters" and "id" in row_id:
                char_id = row_id["id"]
                char_name = char_names.get(char_id, "")
                field_key = r["column"]

            writer.writerow([
                r["url"], r["r2_path"], r["filename"], r["url_type"],
                r["is_thumbnail"], r["table"], r["column"],
                json.dumps(row_id),
                char_id, char_name, field_key,
            ])
    print(f"\n  CSV written: {csv_path}")

    # Character-images CSV — clean per-character image map for Chevereto rebuild
    char_csv_path = output_dir / "imagehut_by_character.csv"
    with open(char_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "character_id", "character_name", "field_key",
            "url", "r2_path", "filename", "is_thumbnail"
        ])
        for char_id in sorted(char_images.keys(), key=lambda x: int(x) if x.isdigit() else 0):
            char_name = char_names.get(char_id, "")
            seen = set()
            for img in char_images[char_id]:
                if img["url"] in seen:
                    continue
                seen.add(img["url"])
                writer.writerow([
                    char_id, char_name, img["field_key"],
                    img["url"], img["r2_path"], img["filename"],
                    img["is_thumbnail"],
                ])
    print(f"  Character CSV written: {char_csv_path}")

    # JSON — full structured data
    json_path = output_dir / "imagehut_urls.json"

    # Build per-character structure for JSON
    char_json = {}
    for char_id, images in char_images.items():
        unique = {}
        for img in images:
            if img["url"] not in unique:
                unique[img["url"]] = img
        char_json[char_id] = {
            "name": char_names.get(char_id, ""),
            "images": list(unique.values()),
        }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": {
                "total_occurrences": len(all_results),
                "unique_urls": len(unique_urls),
                "direct_images": len(direct),
                "page_or_album_urls": len(pages),
                "thumbnails": len(thumbnails),
                "characters_with_images": chars_with_images,
                "total_character_image_associations": total_char_images,
            },
            "urls": list(unique_urls.values()),
            "by_character": char_json,
        }, f, indent=2)
    print(f"  JSON written: {json_path}")


if __name__ == "__main__":
    main()
