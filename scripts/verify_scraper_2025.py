# -*- coding: utf-8 -*-
"""One-off check: scrape a REAL, already-completed ESPN week (2025 week 15) and diff
the parsed raw rows against the known-correct archive/2025/week-15.csv, to confirm
scrape.py still parses ESPN's current markup correctly.

One-time setup, then run:
    pip install -r scripts/requirements.txt
    playwright install chromium
    python scripts/verify_scraper_2025.py

Run this from your own machine, not a cloud sandbox -- this couldn't be verified
end-to-end during development because ESPN's bot-protection (an AWS WAF JS
challenge on the main site, Akamai fingerprint-blocking on their JSON API) blocks
the cloud environment this was built from outright, headless browser or not.
"""
from __future__ import annotations

import pandas as pd

from config import Config, REPO_ROOT
from scrape import scrape_week

ARCHIVE_2025 = REPO_ROOT / "archive" / "2025"


def main() -> None:
    config = Config()
    print("Scraping ESPN for 2025 week 15 (a real, completed week)...")
    rows, unmatched, pending = scrape_week(15, 2025, config)
    print(f"Parsed {len(rows)} rows ({len(rows)//2} games, {pending} not final). Unmatched (-> FCS): {unmatched}")

    try:
        known = pd.read_csv(ARCHIVE_2025 / "week-15.csv", encoding="utf-8")
    except UnicodeDecodeError:
        known = pd.read_csv(ARCHIVE_2025 / "week-15.csv", encoding="cp1252")
    known = known.dropna(subset=["Team 1"])
    known["Team 1"] = known["Team 1"].replace({"San Jos� State": "San José State"})
    known["Team 2"] = known["Team 2"].replace({"San Jos� State": "San José State"})

    mine = pd.DataFrame(rows)

    mine_set = {(r["Team 1"], r["Team 2"], r["Result"], int(r["Margin"]), r["Road?"]) for r in rows}
    known_set = {
        (row["Team 1"], row["Team 2"], row["Result"], int(row["Margin"]), row["Road?"])
        for _, row in known.iterrows()
    }

    print(f"\nScraped rows: {len(mine_set)}   Archived rows: {len(known_set)}")
    only_in_scrape = mine_set - known_set
    only_in_archive = known_set - mine_set
    if not only_in_scrape and not only_in_archive:
        print("MATCH: freshly-scraped week 15 rows are identical to the archived week-15.csv.")
    else:
        print(f"{len(only_in_scrape)} rows only in fresh scrape, {len(only_in_archive)} only in archive:")
        for r in sorted(only_in_scrape):
            print("  + (scrape only)", r)
        for r in sorted(only_in_archive):
            print("  - (archive only)", r)


if __name__ == "__main__":
    main()
