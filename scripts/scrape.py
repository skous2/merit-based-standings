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


def espn_week_param(week: int, year: int) -> int:
    """Maps our week-N.csv numbering to ESPN's own week URL parameter. This is
    season-dependent, not a fixed offset:

    Through the 2025 season, ESPN's site had a genuine, separately-numbered
    "Week 0" exempt slate before the main season, which pushed our week-N.csv
    numbering one behind ESPN's own week parameter -- week-15.csv = ESPN's real
    Week 14 (rivalry week). Confirmed against archived 2025 data (see
    verify_scraper_2025.py) and the original notebook this pipeline replaced.

    Starting with the 2026 season, ESPN dropped that separate Week 0 grouping --
    requesting its week=0 page now returns a near-empty stub, not a real slate of
    exempt games -- so our week-N.csv maps directly to ESPN's own week=N. Verified
    2026-08-30: the live week=1 page's own <title> tag reads "Week 1", and its game
    grid includes North Dakota State and Sacramento State, this season's two
    newly-promoted FBS teams, playing their debut games.

    If this breaks again in a future season, re-verify against the live
    scoreboard's page title and roster names before adjusting the offset -- don't
    assume last season's mapping still holds.
    """
    return week if year >= 2026 else week - 1

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

GAME_CARD_SELECTOR = "section.Scoreboard"
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
    game card appears -- wait a fixed, generous settle period first (confirmed
    empirically: ~4s to fully populate, so 9s is a safe margin), then confirm the
    count really is done growing with a few widely-spaced checks.
    """
    page.wait_for_timeout(settle_ms)
    last_count = page.locator(GAME_CARD_SELECTOR).count()
    for _ in range(confirm_checks):
        page.wait_for_timeout(confirm_interval_ms)
        count = page.locator(GAME_CARD_SELECTOR).count()
        if count > last_count:
            last_count = count  # still growing -- keep confirming
    return last_count


def fetch_week_games(week: int, year: int, attempts: int = 2) -> list[dict]:
    """Loads the scoreboard page with a real headless browser (so ESPN's bot-
    challenge JS actually executes) and returns one dict per game card:
    {'away': str, 'home': str, 'away_score': str|None, 'home_score': str|None}.
    Scores are None for a game that hasn't kicked off yet -- ESPN's pregame cards
    show a kickoff time instead of a score element, so there's nothing to read.

    Reads names/scores from *within* each game card (via a per-card locator)
    rather than two flat page-wide lists: a finished game's card and a pregame
    card have different DOM shapes (the latter has no score element at all), so
    two flat lists collected separately can silently fall out of positional sync
    with each other once the page has a mix of both -- reading each card's own
    children keeps every score correctly paired with its own game no matter what
    mix of finished/in-progress/pregame games the week has. This also happens to
    sidestep ESPN's "featured games" rail, which uses a different wrapper (not
    matched by GAME_CARD_SELECTOR) and was previously a double-counting risk.
    """
    url = ESPN_URL_TEMPLATE.format(espn_week=espn_week_param(week, year), year=year)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1400, "height": 2200})
            page = context.new_page()
            last_error = None
            for attempt in range(1, attempts + 1):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_selector(GAME_CARD_SELECTOR, timeout=30000)
                    count = _wait_for_all_games_loaded(page)
                    if count <= 0:
                        raise PlaywrightTimeoutError("no game cards found after waiting")
                    cards = page.locator(GAME_CARD_SELECTOR)
                    games = []
                    for i in range(count):
                        card = cards.nth(i)
                        names = card.locator(NAME_SELECTOR).all_text_contents()
                        if len(names) != 2:
                            continue  # unexpected card shape -- skip defensively
                        scores = card.locator(SCORE_SELECTOR).all_text_contents()
                        away_score = scores[0] if len(scores) == 2 else None
                        home_score = scores[1] if len(scores) == 2 else None
                        games.append({
                            "away": names[0], "home": names[1],
                            "away_score": away_score, "home_score": home_score,
                        })
                    return games
                except PlaywrightTimeoutError as e:
                    last_error = e
            raise ScrapeBlockedError(
                f"Timed out waiting for scoreboard content at {url} after {attempts} "
                f"attempt(s). ESPN's bot-challenge page may not have resolved, or "
                "their markup changed (expected selector: "
                f"'{GAME_CARD_SELECTOR}'). Original error: {last_error}"
            )
        finally:
            browser.close()


def build_rows(games: list[dict], config: Config) -> tuple[list[dict], list[str]]:
    """Returns (raw_rows, unmatched_names). games is one dict per game card, as
    returned by fetch_week_games. raw_rows have keys matching the week-N.csv
    columns A-E: 'Team 1', 'Team 2', 'Result', 'Margin', 'Road?'. unmatched_names is
    every scraped name that didn't match the roster (and so was recorded as FCS) --
    print/review these each run to catch normalization gaps early.
    """
    lookup = build_lookup(config)
    unmatched: list[str] = []
    rows: list[dict] = []

    for g in games:
        away_score_raw, home_score_raw = g["away_score"], g["home_score"]
        if away_score_raw is None or home_score_raw is None:
            continue  # game hasn't kicked off (or is in progress) -- not final yet
        try:
            away_score, home_score = int(away_score_raw.strip()), int(home_score_raw.strip())
        except ValueError:
            continue  # e.g. postponed game showing non-numeric text -- skip

        away_raw, home_raw = g["away"].strip(), g["home"].strip()
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
    games = fetch_week_games(week, year)
    return build_rows(games, config)


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
