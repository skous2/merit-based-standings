# -*- coding: utf-8 -*-
"""Scrape ESPN's college football scoreboard for a given week/year.

Fetches the page with a real headless Chromium browser (Playwright) instead of a
plain HTTP request -- ESPN added bot-protection (an AWS WAF JS challenge on the
main site, Akamai fingerprint-blocking on their JSON API) since the original
espn-score-scraper.ipynb last worked, and a real browser executing real JS is what
gets through it.

Team names/scores are read directly via Playwright element queries (locator.all_
text_contents()) rather than dumping page.content() and parsing it with
BeautifulSoup: ESPN renders the score cards inside Shadow DOM, which Playwright's
locators pierce automatically but a plain HTML serialization does not, so the
BeautifulSoup route was silently seeing only a fraction of the games.

One-time setup:
    pip install -r scripts/requirements.txt
    playwright install chromium
"""
from __future__ import annotations

import re
import unicodedata

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from config import Config, FCS_LABEL

ESPN_URL_TEMPLATE = (
    "https://www.espn.com/college-football/scoreboard/_/week/{espn_week}"
    "/year/{year}/seasontype/2/group/80"
)


def espn_week_param(week: int) -> int:
    """Our week-N.csv numbering is offset by one from ESPN's own week parameter,
    because ESPN's real 'Week 0' (the preseason exempt slate) occupies week-1.csv:
    week-1.csv = ESPN's real Week 0, week-2.csv = real Week 1, ..., week-15.csv =
    real Week 14 (rivalry week). Confirmed against the original notebook (which
    fetched url week=14 to populate its 'Week 15' sheet) and a live scrape."""
    return week - 1

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

NAME_SELECTOR = "div.ScoreCell__TeamName"
SCORE_SELECTOR = "div.ScoreCell__Score"


class ScrapeBlockedError(RuntimeError):
    """Raised when ESPN returns a bot-challenge response instead of the real page."""


def _normalize_key(name: str) -> str:
    name = name.strip()
    name = name.replace("’", "'").replace("‘", "'").replace("`", "'")
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_ish = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", ascii_ish).strip().lower()


def build_lookup(config: Config) -> dict:
    lookup = {}
    for team in config.roster:
        lookup[_normalize_key(team)] = team
    for alias, canonical in config.aliases.items():
        lookup[_normalize_key(alias)] = canonical
    return lookup


def canonicalize(name: str, lookup: dict) -> str | None:
    """Returns the canonical roster name, or None if this is (presumably) an FCS team."""
    return lookup.get(_normalize_key(name))


def _wait_for_all_games_loaded(page, settle_ms: int = 9000, confirm_checks: int = 3, confirm_interval_ms: int = 1500) -> int:
    """The scoreboard finishes populating asynchronously in bursts after the first
    score cell appears -- wait a fixed, generous settle period first (confirmed
    empirically: ~4s to fully populate, so 9s is a safe margin), then confirm the
    count really is done growing with a few widely-spaced checks.
    """
    page.wait_for_timeout(settle_ms)
    last_count = page.locator(NAME_SELECTOR).count()
    for _ in range(confirm_checks):
        page.wait_for_timeout(confirm_interval_ms)
        count = page.locator(NAME_SELECTOR).count()
        if count > last_count:
            last_count = count  # still growing -- keep confirming
    return last_count


def fetch_week_names_and_scores(week: int, year: int, attempts: int = 2) -> tuple[list[str], list[str]]:
    """Loads the scoreboard page with a real headless browser (so ESPN's bot-
    challenge JS actually executes) and returns the raw (team_names, scores) text
    lists, read straight from the live DOM via Playwright locators. Retries once
    (with a fresh page load) if content doesn't show up in time.
    """
    url = ESPN_URL_TEMPLATE.format(espn_week=espn_week_param(week), year=year)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1400, "height": 2200})
            page = context.new_page()
            last_error = None
            for attempt in range(1, attempts + 1):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_selector(NAME_SELECTOR, timeout=30000)
                    count = _wait_for_all_games_loaded(page)
                    if count <= 0:
                        raise PlaywrightTimeoutError("no score cells found after waiting")
                    names = page.locator(NAME_SELECTOR).all_text_contents()
                    scores = page.locator(SCORE_SELECTOR).all_text_contents()
                    return names, scores
                except PlaywrightTimeoutError as e:
                    last_error = e
            raise ScrapeBlockedError(
                f"Timed out waiting for scoreboard content at {url} after {attempts} "
                f"attempt(s). ESPN's bot-challenge page may not have resolved, or "
                "their markup changed (expected selector: "
                f"'{NAME_SELECTOR}'). Original error: {last_error}"
            )
        finally:
            browser.close()


def build_rows(names: list[str], scores: list[str], config: Config) -> tuple[list[dict], list[str]]:
    """Returns (raw_rows, unmatched_names). raw_rows have keys matching the week-N.csv
    columns A-E: 'Team 1', 'Team 2', 'Result', 'Margin', 'Road?'. unmatched_names is
    every scraped name that didn't match the roster (and so was recorded as FCS) --
    print/review these each run to catch normalization gaps early.
    """
    names = [n.strip() for n in names]
    scores = [s.strip() for s in scores]

    if len(names) != len(scores):
        raise ValueError(
            f"Scrape mismatch: {len(names)} team names vs {len(scores)} scores -- "
            "ESPN's page structure may have changed."
        )
    if len(names) % 2 != 0:
        raise ValueError(f"Odd number of teams scraped ({len(names)}) -- expected pairs of two.")

    lookup = build_lookup(config)
    unmatched: list[str] = []
    rows: list[dict] = []
    seen_games: set[tuple] = set()

    for i in range(0, len(names), 2):
        away_raw, home_raw = names[i], names[i + 1]
        away_score_raw, home_score_raw = scores[i], scores[i + 1]
        try:
            away_score, home_score = int(away_score_raw), int(home_score_raw)
        except ValueError:
            continue  # not yet final (postponed/future game) -- skip

        # ESPN repeats some games in a "featured games" rail in addition to the main
        # grid, which would otherwise double-count them -- keep only the first
        # occurrence of each unique matchup+score.
        game_key = (away_raw, home_raw, away_score, home_score)
        if game_key in seen_games:
            continue
        seen_games.add(game_key)

        away_canon = canonicalize(away_raw, lookup)
        home_canon = canonicalize(home_raw, lookup)
        if away_canon is None:
            unmatched.append(away_raw)
        if home_canon is None:
            unmatched.append(home_raw)

        away_team = away_canon or FCS_LABEL
        home_team = home_canon or FCS_LABEL
        if away_team == FCS_LABEL and home_team == FCS_LABEL:
            continue  # two unrecognized teams -- not a game involving our roster

        margin = abs(away_score - home_score)
        away_won = away_score > home_score

        rows.append({
            "Team 1": away_team, "Team 2": home_team,
            "Result": "WIN" if away_won else "LOSS",
            "Margin": margin, "Road?": "Road",
        })
        rows.append({
            "Team 1": home_team, "Team 2": away_team,
            "Result": "LOSS" if away_won else "WIN",
            "Margin": margin, "Road?": "Home",
        })

    # Mirror the original notebook: only keep rows where Team 1 is an actual FBS
    # roster team (an FCS-team-as-Team-1 row is redundant with its FBS opponent's row).
    rows = [r for r in rows if r["Team 1"] != FCS_LABEL]
    return rows, sorted(set(unmatched))


def scrape_week(week: int, year: int, config: Config) -> tuple[list[dict], list[str]]:
    names, scores = fetch_week_names_and_scores(week, year)
    return build_rows(names, scores, config)


if __name__ == "__main__":
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(description="Scrape one week's ESPN CFB scoreboard (debug tool).")
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args()

    cfg = Config()
    game_rows, unmatched_names = scrape_week(args.week, args.year, cfg)
    print(f"Parsed {len(game_rows)} rows ({len(game_rows) // 2} games).")
    if unmatched_names:
        print("Unmatched -> treated as FCS (review for normalization gaps):")
        for n in unmatched_names:
            print(f"  - {n}")
    print(_json.dumps(game_rows[:6], indent=2, ensure_ascii=False))
