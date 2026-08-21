# -*- coding: utf-8 -*-
"""The weekly 'one button': scrape this week's ESPN scoreboard, recompute every
team's points for the whole season so far, and rewrite the CSVs the site serves.

One-time setup:
    pip install -r scripts/requirements.txt
    playwright install chromium

Usage:
    python scripts/update_week.py                # auto-detects the next week
    python scripts/update_week.py --week 3        # explicit override (e.g. redo a week)
    python scripts/update_week.py --dry-run        # scrape + compute, don't write files

After this finishes, review the printed summary, then:
    git add -A && git commit -m "Week N update" && git push
    (then your existing manual `gcloud run deploy`)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
from pathlib import Path

import pandas as pd

from config import Config, REPO_ROOT
from scoring import build_rank_map, compute_standings, score_all_weeks
from scrape import scrape_week

RAW_COLUMNS = ["Team 1", "Team 2", "Result", "Margin", "Road?"]
FULL_COLUMNS = RAW_COLUMNS + ["Win", "Qual Win", "SOS", "Conf", "Qual Loss", "Total"]
MAX_WEEK_SCAN = 25

WEEK0_SEED_PATH = Path(__file__).resolve().parent / "week0_seed_2026.csv"


def default_season_year() -> int:
    today = dt.date.today()
    return today.year if today.month >= 6 else today.year - 1


def week_csv_path(week: int) -> Path:
    return REPO_ROOT / f"week-{week}.csv"


def load_seed_order(path: Path = WEEK0_SEED_PATH) -> list[str]:
    df = pd.read_csv(path)
    return df.sort_values("Rank")["Team"].tolist()


def detect_next_week() -> int:
    last_populated = 0
    for n in range(1, MAX_WEEK_SCAN + 1):
        p = week_csv_path(n)
        if p.exists():
            df = pd.read_csv(p)
            if len(df) > 0:
                last_populated = n
    return last_populated + 1


def load_raw_week(week: int) -> list[dict]:
    df = pd.read_csv(week_csv_path(week))
    df = df.dropna(subset=["Team 1"])
    return df[RAW_COLUMNS].to_dict("records")


def load_reference_ranking(target_week: int, config: Config) -> list[str]:
    """Last week's finalized standings order, or the Week 0 seed if this is the
    season's first processed week."""
    current_path = REPO_ROOT / "current-standings.csv"
    if target_week > 1 and current_path.exists():
        df = pd.read_csv(current_path)
        return df.sort_values("Standing")["Team"].tolist()
    return load_seed_order()


def write_week_csv(week: int, rows: list[dict]) -> None:
    df = pd.DataFrame(rows, columns=FULL_COLUMNS)
    df.to_csv(week_csv_path(week), index=False, encoding="utf-8")


def write_all_games_csv(scored_by_week: dict[int, list[dict]]) -> None:
    all_rows = []
    for week in sorted(scored_by_week):
        for row in scored_by_week[week]:
            r = {"Week": week}
            r.update(row)
            all_rows.append(r)
    df = pd.DataFrame(all_rows, columns=["Week"] + FULL_COLUMNS)
    df.to_csv(REPO_ROOT / "all-games.csv", index=False, encoding="utf-8")


def write_current_standings_csv(standings: list[dict]) -> None:
    rows = []
    for r in standings:
        rows.append({
            "Standing": r["Standing"],
            "Team": r["Team"],
            "Total Points": r["Total Points"],
            "AVG Points": f"{r['AVG Points']:.2f}",
            "Initial Standing": r["Initial Standing"],
            "Conference": r["Conference"],
        })
    df = pd.DataFrame(rows, columns=[
        "Standing", "Team", "Total Points", "AVG Points", "Initial Standing", "Conference",
    ])
    df.to_csv(REPO_ROOT / "current-standings.csv", index=False, encoding="utf-8")


def write_total_points_csv(
    scored_by_week: dict[int, list[dict]], standings: list[dict], config: Config
) -> None:
    weekly_totals: dict[str, dict[int, int]] = {team: {} for team in config.roster}
    for week, rows in scored_by_week.items():
        for row in rows:
            weekly_totals[row["Team 1"]][week] = row["Total"]

    weeks_present = sorted(scored_by_week.keys())
    out_rows = []
    for r in standings:
        team = r["Team"]
        row = {
            "Standing": r["Standing"],
            "Team": team,
            "Conference": r["Conference"],
        }
        for w in weeks_present:
            row[f"Week {w}"] = weekly_totals[team].get(w, 0)
        row["Total Points"] = r["Total Points"]
        out_rows.append(row)

    columns = ["Standing", "Team", "Conference"] + [f"Week {w}" for w in weeks_present] + ["Total Points"]
    df = pd.DataFrame(out_rows, columns=columns)
    df.to_csv(REPO_ROOT / "total-points.csv", index=False, encoding="utf-8")


def append_previous_standings_column(target_week: int, standings: list[dict]) -> None:
    path = REPO_ROOT / "previous-standings.csv"
    new_col_name = f"Week {target_week}"
    new_col_values = [r["Team"] for r in standings]  # row i => team at rank i+1

    if path.exists():
        df = pd.read_csv(path)
        if new_col_name in df.columns:
            df[new_col_name] = new_col_values
        else:
            df[new_col_name] = new_col_values
    else:
        seed_order = load_seed_order()
        df = pd.DataFrame({
            "Standings": list(range(1, len(seed_order) + 1)),
            "Week 0": seed_order,
            new_col_name: new_col_values,
        })

    df.to_csv(path, index=False, encoding="utf-8")


def print_summary(standings: list[dict], previous_standings: list[dict] | None, unmatched: list[str]) -> None:
    print("\n=== Top 10 ===")
    for r in standings[:10]:
        print(f"  {r['Standing']:>3}. {r['Team']:<22} {r['Total Points']:>4} pts  ({r['AVG Points']:.2f} avg)")

    if previous_standings:
        prev_rank = {r["Team"]: r["Standing"] for r in previous_standings}
        deltas = []
        for r in standings:
            if r["Team"] in prev_rank:
                deltas.append((prev_rank[r["Team"]] - r["Standing"], r["Team"], r["Standing"]))
        deltas.sort(reverse=True)
        movers_up = [d for d in deltas if d[0] > 0][:5]
        movers_down = sorted([d for d in deltas if d[0] < 0])[:5]
        if movers_up:
            print("\n=== Biggest movers up ===")
            for delta, team, rank in movers_up:
                print(f"  {team:<22} +{delta} -> #{rank}")
        if movers_down:
            print("\n=== Biggest movers down ===")
            for delta, team, rank in movers_down:
                print(f"  {team:<22} {delta} -> #{rank}")

    if unmatched:
        print("\n=== Scraped names treated as FCS (verify none should be FBS) ===")
        for n in unmatched:
            print(f"  - {n}")


def run(target_week: int, year: int, dry_run: bool) -> None:
    config = Config()

    print(f"Scraping week {target_week}, {year} season...")
    raw_rows, unmatched = scrape_week(target_week, year, config)
    print(f"  {len(raw_rows) // 2} games parsed.")

    raw_by_week: dict[int, list[dict]] = {}
    for w in range(1, target_week):
        if week_csv_path(w).exists():
            raw_by_week[w] = load_raw_week(w)
    raw_by_week[target_week] = raw_rows

    reference_order = load_reference_ranking(target_week, config)
    rank_map = build_rank_map(reference_order)

    scored_by_week = score_all_weeks(raw_by_week, rank_map, config)
    seed_order = load_seed_order()
    standings = compute_standings(scored_by_week, config, seed_order)

    previous_standings = None
    current_path = REPO_ROOT / "current-standings.csv"
    if current_path.exists():
        prev_df = pd.read_csv(current_path)
        previous_standings = prev_df.rename(columns={"Standing": "Standing"}).to_dict("records")

    print_summary(standings, previous_standings, unmatched)

    if dry_run:
        print("\n[dry run] No files were written.")
        return

    for w, rows in scored_by_week.items():
        write_week_csv(w, rows)
    write_all_games_csv(scored_by_week)
    write_current_standings_csv(standings)
    write_total_points_csv(scored_by_week, standings, config)
    append_previous_standings_column(target_week, standings)

    print(f"\nWrote week-{target_week}.csv, all-games.csv, current-standings.csv, "
          f"total-points.csv, and appended 'Week {target_week}' to previous-standings.csv.")
    print("Next: review the numbers above, then `git add -A && git commit && git push`, "
          "then your usual `gcloud run deploy`.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--week", type=int, default=None, help="Week number to process (default: auto-detect next).")
    parser.add_argument("--year", type=int, default=None, help="Season year (default: inferred from today's date).")
    parser.add_argument("--dry-run", action="store_true", help="Scrape + compute, but don't write any files.")
    args = parser.parse_args()

    target_week = args.week or detect_next_week()
    year = args.year or default_season_year()
    run(target_week, year, args.dry_run)


if __name__ == "__main__":
    main()
