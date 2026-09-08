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
ROUND_GAP_DAYS = 3  # a bigger date gap than this between two games on the same scraped page means they're different real rounds

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


def split_by_date_gap(rows: list[dict], target_week: int) -> dict[int, list[dict]]:
    """ESPN can bundle an earlier real round into the week we asked for -- e.g.
    2026's actual Week 0 openers, since ESPN's own week=0 page is now an empty
    stub (see espn_week_param's docstring in scrape.py), so those games show up
    on the week=1 page instead. Detect that by clustering games by their actual
    date: a gap bigger than ROUND_GAP_DAYS between two dates means they're
    different rounds, and older clusters get assigned target_week - 1,
    target_week - 2, etc. A normal single-round week (games a day or two apart,
    Thu-Mon) never trips this.
    """
    dated = [r["Date"] for r in rows if r.get("Date") is not None]
    if not dated:
        return {target_week: rows}

    dates = sorted(set(dated))
    clusters = [[dates[0]]]
    for d in dates[1:]:
        if (d - clusters[-1][-1]).days > ROUND_GAP_DAYS:
            clusters.append([d])
        else:
            clusters[-1].append(d)

    if len(clusters) == 1:
        return {target_week: rows}

    n = len(clusters)
    week_for_date = {
        d: target_week - (n - 1 - i)
        for i, cluster in enumerate(clusters)
        for d in cluster
    }

    if min(week_for_date.values()) < 0:
        print(
            f"  WARNING: date clustering found {n} rounds on this page but that "
            f"would push a week number below 0 -- keeping everything under week "
            f"{target_week} instead of splitting (verify manually)."
        )
        return {target_week: rows}

    result: dict[int, list[dict]] = {}
    for r in rows:
        w = week_for_date.get(r.get("Date"), target_week)
        result.setdefault(w, []).append(r)

    if len(result) > 1:
        print(f"  Detected {n} distinct rounds by date on this page -- "
              f"splitting into weeks {sorted(result)} instead of lumping everything into week {target_week}.")
    return result


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


def write_previous_standings_csv(scored_by_week: dict[int, list[dict]], config: Config) -> None:
    """Rewrites previous-standings.csv from scratch: a 'Preseason' column (the
    Week 0 seed order) followed by one column per week showing that week's
    cumulative standings order (team names, rank 1 first). Recomputed in full
    every run, rather than just appending the latest week, so a week that gets
    backfilled after the fact -- e.g. real Week 0 games discovered bundled into
    what we'd scraped as Week 1 -- still gets its own correct snapshot column.
    """
    seed_order = load_seed_order()
    weeks_present = sorted(scored_by_week)

    out = {"Standings": list(range(1, len(seed_order) + 1)), "Preseason": seed_order}
    for through_week in weeks_present:
        cumulative = {w: rows for w, rows in scored_by_week.items() if w <= through_week}
        standings = compute_standings(cumulative, config, seed_order)
        out[f"Week {through_week}"] = [r["Team"] for r in standings]

    df = pd.DataFrame(out, columns=["Standings", "Preseason"] + [f"Week {w}" for w in weeks_present])
    df.to_csv(REPO_ROOT / "previous-standings.csv", index=False, encoding="utf-8")


def print_summary(
    standings: list[dict], previous_standings: list[dict] | None, unmatched: list[str],
    target_week: int, pending: int,
) -> None:
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

    if pending:
        print(
            f"\n=== {pending} week {target_week} game(s) not yet final ===\n"
            f"  Re-run `python scripts/update_week.py --week {target_week}` once they finish, "
            f"BEFORE running week {target_week + 1} -- once you move on, week {target_week} "
            "won't be revisited and these games will be silently missed."
        )


def run(target_week: int, year: int, dry_run: bool) -> None:
    config = Config()

    print(f"Scraping week {target_week}, {year} season...")
    raw_rows, unmatched, pending = scrape_week(target_week, year, config)
    print(f"  {len(raw_rows) // 2} games parsed, {pending} not yet final.")

    raw_by_week: dict[int, list[dict]] = {}
    for w in range(0, target_week):
        if week_csv_path(w).exists():
            raw_by_week[w] = load_raw_week(w)
    raw_by_week.update(split_by_date_gap(raw_rows, target_week))

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

    print_summary(standings, previous_standings, unmatched, target_week, pending)

    if dry_run:
        print("\n[dry run] No files were written.")
        return

    for w, rows in scored_by_week.items():
        write_week_csv(w, rows)
    write_all_games_csv(scored_by_week)
    write_current_standings_csv(standings)
    write_total_points_csv(scored_by_week, standings, config)
    write_previous_standings_csv(scored_by_week, config)

    written_weeks = ", ".join(f"week-{w}.csv" for w in sorted(scored_by_week))
    print(f"\nWrote {written_weeks}, all-games.csv, current-standings.csv, "
          f"total-points.csv, and previous-standings.csv.")
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
