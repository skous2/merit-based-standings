# -*- coding: utf-8 -*-
"""Regression test: replay the archived 2025 season week-by-week through the new
scoring engine (scoring.py) and diff the result against the original current-
standings.csv / total-points.csv that came straight out of the Excel workbook.

This doesn't touch the scraper or the live root CSVs -- it's a pure check that the
Python scoring engine reproduces the spreadsheet's per-game math. Run with:
    python scripts/regression_test_2025.py

NOTE: the spreadsheet's SOS references got repointed to the latest Rankings column
via periodic manual find/replace, an operational quirk that isn't perfectly
reconstructible after the fact. This script simulates the CONFIRMED design intent
instead (each week's run grades the whole season so far using last week's finalized
standings as the single reference), so small differences vs the original file are
expected for the SOS column specifically -- this script quantifies exactly how much
that matters, rather than assuming a byte-perfect match.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from config import Config, REPO_ROOT
from scoring import build_rank_map, compute_standings, score_all_weeks

ARCHIVE_2025 = REPO_ROOT / "archive" / "2025"
RAW_COLUMNS = ["Team 1", "Team 2", "Result", "Margin", "Road?"]


def load_seed(path: Path) -> list[str]:
    df = pd.read_csv(path)
    return df.sort_values("Rank")["Team"].tolist()


def load_raw_week(week: int) -> list[dict]:
    path = ARCHIVE_2025 / f"week-{week}.csv"
    try:
        df = pd.read_csv(path, encoding="utf-8")
    except UnicodeDecodeError:
        # The original 2025 CSVs were exported from Excel inconsistently -- some as
        # UTF-8, some as Windows-1252 (this is the source of the "San Jos<e9> State"
        # mojibake). Fall back for the archived files only; the new pipeline always
        # writes UTF-8 going forward.
        df = pd.read_csv(path, encoding="cp1252")
    df = df.dropna(subset=["Team 1"])  # a couple archived files have a stray trailing blank row
    df["Team 1"] = df["Team 1"].replace({"San Jos� State": "San José State"})
    df["Team 2"] = df["Team 2"].replace({"San Jos� State": "San José State"})
    return df[RAW_COLUMNS].to_dict("records")


def main() -> None:
    config = Config()
    seed_order = load_seed(ARCHIVE_2025 / "week0_seed_2025.csv")

    reference = seed_order
    raw_by_week: dict[int, list[dict]] = {}
    standings = None

    for week in range(1, 16):
        raw_by_week[week] = load_raw_week(week)
        rank_map = build_rank_map(reference)
        scored_by_week = score_all_weeks(raw_by_week, rank_map, config)
        standings = compute_standings(scored_by_week, config, seed_order)
        reference = [r["Team"] for r in standings]
        print(f"week {week:>2} processed, {len(raw_by_week[week])} rows, "
              f"top team now: {standings[0]['Team']} ({standings[0]['Total Points']} pts)")

    print("\n=== Final (mine) top 15 ===")
    for r in standings[:15]:
        print(f"  {r['Standing']:>3}. {r['Team']:<22} {r['Total Points']:>4} pts  {r['AVG Points']:.2f} avg")

    try:
        original = pd.read_csv(ARCHIVE_2025 / "current-standings.csv", encoding="utf-8")
    except UnicodeDecodeError:
        original = pd.read_csv(ARCHIVE_2025 / "current-standings.csv", encoding="cp1252")
    original["Team"] = original["Team"].replace({"San Jos� State": "San José State"})
    orig_total_by_team = dict(zip(original["Team"], original["Total Points"]))
    orig_rank_by_team = dict(zip(original["Team"], original["Standing"]))

    mine_map = {r["Team"]: r for r in standings}

    print("\n=== Diff vs original current-standings.csv ===")
    total_abs_diff = 0
    max_diff = 0
    diffs = []
    for team, mine in mine_map.items():
        if team not in orig_total_by_team:
            print(f"  MISSING FROM ORIGINAL: {team}")
            continue
        orig_total = orig_total_by_team[team]
        orig_rank = orig_rank_by_team[team]
        diff = mine["Total Points"] - orig_total
        if diff != 0:
            diffs.append((abs(diff), team, mine["Total Points"], orig_total, mine["Standing"], orig_rank))
        total_abs_diff += abs(diff)
        max_diff = max(max_diff, abs(diff))

    diffs.sort(reverse=True)
    print(f"Teams with any Total Points difference: {len(diffs)} / {len(mine_map)}")
    print(f"Sum of |differences|: {total_abs_diff}   Max single-team difference: {max_diff}")
    print("\nTop differences (team: mine vs original total points, mine rank vs original rank):")
    for absdiff, team, mine_total, orig_total, mine_rank, orig_rank in diffs[:20]:
        print(f"  {team:<22} {mine_total:>4} vs {orig_total:>4}  (diff {mine_total-orig_total:+d})   "
              f"rank {mine_rank} vs {orig_rank}")

    # Top-10 order comparison (should be identical or near-identical since Win/Qual
    # Win/Conf dominate the point totals and SOS tiers are coarse).
    orig_sorted = original.sort_values("Standing")
    orig_top10 = orig_sorted["Team"].tolist()[:10]
    mine_top10 = [r["Team"] for r in standings[:10]]
    print(f"\nOriginal top 10: {orig_top10}")
    print(f"Mine top 10:     {mine_top10}")
    print(f"Top 10 identical (order-sensitive): {orig_top10 == mine_top10}")
    print(f"Top 10 same set (order-insensitive): {set(orig_top10) == set(mine_top10)}")


if __name__ == "__main__":
    main()
