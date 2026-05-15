#!/usr/bin/env python3
"""Extract all image URLs from forum row descriptions on the JCink forum.

Fetches the main index page and all forum first pages, parses the HTML,
extracts any image URLs found in the forum description cells, and outputs
a detailed summary and CSV file.
"""

import asyncio
import csv
import os
import re
import sys
from urllib.parse import urlparse
from bs4 import BeautifulSoup

# Ensure the repository root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings
from app.services.fetcher import fetch_page_with_delay, close_client


# Match background image urls
BG_URL_RE = re.compile(r'url\([\'"]?([^\'"\)]+)[\'"]?\)', re.IGNORECASE)

# Extract any direct image link
def extract_urls_from_style(style_str: str) -> list[str]:
    if not style_str:
        return []
    return BG_URL_RE.findall(style_str)


def get_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.netloc.lower() or "unknown"
    except Exception:
        return "unknown"


async def main():
    base_url = settings.forum_base_url
    excluded_forums = settings.excluded_forum_ids

    print(f"==================================================")
    print(f"FORUM ROW IMAGE EXTRACTOR")
    print(f"==================================================")
    print(f"Target Forum: {base_url}")
    print(f"Excluding Forums: {sorted(list(excluded_forums))}")
    print()

    # Step 1: Fetch main index page
    print("[1/3] Fetching main forum index page...")
    index_html = await fetch_page_with_delay(f"{base_url}/index.php")
    if not index_html:
        print("ERROR: Failed to fetch forum index")
        await close_client()
        return

    # Extract initial list of forum IDs
    soup = BeautifulSoup(index_html, "html.parser")
    forum_ids = set()
    for link in soup.select('a[href*="showforum="]'):
        m = re.search(r"showforum=(\d+)", link.get("href", ""))
        if m:
            fid = m.group(1)
            if fid not in excluded_forums:
                forum_ids.add(fid)

    print(f"      Found {len(forum_ids)} forums to scan.")

    # Step 2: Scan main index + each forum's page for forum row HTML
    pages_to_scan = [("Index", index_html)]
    print("\n[2/3] Scanning forum pages for subforums and subforum descriptions...")

    for fid in sorted(list(forum_ids), key=int):
        url = f"{base_url}/index.php?showforum={fid}"
        print(f"      Fetching forum {fid}...")
        html = await fetch_page_with_delay(url)
        if html:
            pages_to_scan.append((f"Forum {fid}", html))

    # Close the fetcher client
    await close_client()

    # Step 3: Parse all fetched pages and extract image links from forum description contexts
    print(f"\n[3/3] Parsing HTML and extracting image URLs from all contexts...")
    
    extracted_urls = {}

    for name, html in pages_to_scan:
        soup_page = BeautifulSoup(html, "html.parser")

        # Approach A: Search inside common description class elements
        desc_elements = soup_page.select(".desc, .forum-desc, .forum-description, .description")
        for elem in desc_elements:
            # Check img tags
            for img in elem.select("img[src]"):
                src = img["src"].strip()
                if src and not src.startswith("data:"):
                    extracted_urls[src] = {
                        "url": src,
                        "source_type": "desc_img",
                        "context": f"{name} > desc",
                        "domain": get_domain(src),
                    }

            # Check background images
            for tag in elem.find_all(style=True):
                style = tag.get("style", "")
                urls = extract_urls_from_style(style)
                for u in urls:
                    u = u.strip()
                    if u and not u.startswith("data:"):
                        extracted_urls[u] = {
                            "url": u,
                            "source_type": "desc_bg",
                            "context": f"{name} > desc",
                            "domain": get_domain(u),
                        }

        # Approach B: Search parent cell of any forum link
        for a in soup_page.select('a[href*="showforum="]'):
            m = re.search(r"showforum=(\d+)", a.get("href", ""))
            if not m:
                continue
            fid = m.group(1)
            if fid in excluded_forums:
                continue

            # Move up 1 to 3 levels to find the forum row td / div
            parent = a.find_parent("td") or a.find_parent("div")
            if not parent:
                continue

            # Check img tags inside the parent cell
            for img in parent.select("img[src]"):
                src = img["src"].strip()
                if src and not src.startswith("data:"):
                    extracted_urls[src] = {
                        "url": src,
                        "source_type": "row_img",
                        "context": f"{name} > forum {fid} cell",
                        "domain": get_domain(src),
                    }

            # Check background images inside the parent cell
            for tag in parent.find_all(style=True):
                style = tag.get("style", "")
                urls = extract_urls_from_style(style)
                for u in urls:
                    u = u.strip()
                    if u and not u.startswith("data:"):
                        extracted_urls[u] = {
                            "url": u,
                            "source_type": "row_bg",
                            "context": f"{name} > forum {fid} cell",
                            "domain": get_domain(u),
                        }

    # Output to console
    if not extracted_urls:
        print("\nNo image URLs found in the forum row descriptions.")
        return

    print(f"\n{'='*60}")
    print(f"RESULTS: {len(extracted_urls)} unique images found in forum descriptions")
    print(f"{'='*60}")

    # Group by domain
    domain_counts = {}
    for item in extracted_urls.values():
        d = item["domain"]
        domain_counts[d] = domain_counts.get(d, 0) + 1

    print("\nBy Image Domain:")
    for d, count in sorted(domain_counts.items(), key=lambda x: -x[1]):
        print(f"  {d}: {count}")

    # Display some sample URLs
    print("\nSample URLs Found:")
    for item in sorted(extracted_urls.values(), key=lambda x: x["domain"])[:30]:
        print(f"  {item['url']}  [{item['context']}]")
    if len(extracted_urls) > 30:
        print(f"  ... and {len(extracted_urls) - 30} more")

    # Write CSV output
    csv_path = "forum_row_images.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["url", "domain", "source_type", "context"])
        for u in sorted(extracted_urls.values(), key=lambda x: x["domain"]):
            writer.writerow([u["url"], u["domain"], u["source_type"], u["context"]])

    print(f"\nCSV Report Written to: {csv_path}")


if __name__ == "__main__":
    asyncio.run(main())
