# -*- coding: utf-8 -*-
"""Pure scoring functions -- no file I/O, so these are directly unit-testable and
reused by both the live pipeline (update_week.py) and the 2025 regression test.

Design (confirmed with the site owner): the SOS bonus is retroactive by design,
and graded in two passes so it's always based on a number the site actually
displays for "last week," never a hidden intermediate value:

  Pass 1: grade every game played so far using last week's finalized standings
  (or the Week 0 preseason seed for the season's first run) -- this is the old
  single-pass behavior.

  Pass 2: recompute the cumulative standings through the week *before* the
  target week from Pass 1's own results, and re-grade every game a second time
  using THAT as the reference. This is the number that ends up in
  previous-standings.csv's "Week N-1" column, so the SOS bonus for grading week
  N always matches what the site shows for week N-1 -- not a snapshot from two
  weeks ago that never got published anywhere.

Two passes only -- not iterated further to a fixed point.
"""
from __future__ import annotations

from config import (
    Config,
    CONF_BONUS_POINTS,
    QUAL_LOSS_MAX_MARGIN,
    QUAL_LOSS_POINTS,
    QUAL_LOSS_RANK_THRESHOLD,
    QUAL_WIN_HOME_MIN_MARGIN,
    QUAL_WIN_POINTS,
    WIN_POINTS,
)


def build_rank_map(ordered_teams: list[str]) -> dict[str, int]:
    """ordered_teams[0] is rank 1, etc. Teams not in the list (e.g. FCS) are simply
    absent from the map, which is exactly what the SOS lookup wants (no rank -> 0 bonus).
    """
    return {team: i + 1 for i, team in enumerate(ordered_teams)}


def score_row(row: dict, rank_map: dict[str, int], config: Config) -> dict:
    """row has 'Team 1','Team 2','Result','Margin','Road?'. Returns a new dict with
    the scored columns ('Win','Qual Win','SOS','Conf','Qual Loss','Total') added.
    """
    result = row["Result"]
    margin = int(row["Margin"])
    road = row["Road?"]
    opponent = row["Team 2"]
    is_win = result == "WIN"
    opponent_rank = rank_map.get(opponent)

    win_pts = WIN_POINTS if is_win else 0

    qual_win_pts = 0
    if is_win and (road == "Road" or margin >= QUAL_WIN_HOME_MIN_MARGIN):
        qual_win_pts = QUAL_WIN_POINTS

    sos_pts = 0
    if is_win:
        sos_pts = config.sos_bonus(opponent_rank)

    conf_pts = CONF_BONUS_POINTS if is_win and opponent in config.power_bonus_teams else 0

    qual_loss_pts = 0
    if (
        not is_win and opponent_rank is not None
        and opponent_rank <= QUAL_LOSS_RANK_THRESHOLD
        and margin <= QUAL_LOSS_MAX_MARGIN
    ):
        qual_loss_pts = QUAL_LOSS_POINTS

    total = win_pts + qual_win_pts + sos_pts + conf_pts + qual_loss_pts

    out = dict(row)
    out["Win"] = win_pts
    out["Qual Win"] = qual_win_pts
    out["SOS"] = sos_pts
    out["Conf"] = conf_pts
    out["Qual Loss"] = qual_loss_pts
    out["Total"] = total
    return out


def score_all_weeks(
    raw_by_week: dict[int, list[dict]], rank_map: dict[str, int], config: Config
) -> dict[int, list[dict]]:
    """Recomputes F-J columns for every week's raw rows using the SAME rank_map --
    this is the 'grade the whole season with today's rankings' pass."""
    return {
        week: [score_row(row, rank_map, config) for row in rows]
        for week, rows in raw_by_week.items()
    }


def score_season_two_pass(
    raw_by_week: dict[int, list[dict]],
    reference_order: list[str],
    config: Config,
    seed_order: list[str],
    target_week: int,
) -> tuple[dict[int, list[dict]], list[str]]:
    """The two-pass grading described in this module's docstring. `reference_order`
    is last week's finalized standings (or the preseason seed), same as before --
    it's used only to bootstrap Pass 1. The reference actually used for grading
    is Pass 2's: the cumulative standings through `target_week - 1`, recomputed
    from Pass 1's own results.

    Returns (scored_by_week, refined_order). `refined_order` is also the exact
    ranking that must be published as previous-standings.csv's "Week
    (target_week - 1)" column -- if that column were instead re-derived from
    the Pass 2 output (which re-grades weeks < target_week a second time using
    refined_order), it would drift slightly from the number actually used to
    grade target_week's games, breaking the "SOS bonus matches what the site
    shows for last week" guarantee this whole scheme exists for.
    """
    rank_map_pass1 = build_rank_map(reference_order)
    scored_pass1 = score_all_weeks(raw_by_week, rank_map_pass1, config)

    prior_weeks = {w: rows for w, rows in scored_pass1.items() if w < target_week}
    refined_order = [r["Team"] for r in compute_standings(prior_weeks, config, seed_order)]

    rank_map_pass2 = build_rank_map(refined_order)
    return score_all_weeks(raw_by_week, rank_map_pass2, config), refined_order


def compute_standings(
    scored_by_week: dict[int, list[dict]],
    config: Config,
    seed_order: list[str],
) -> list[dict]:
    """Aggregates every team's cumulative Total Points across all scored weeks and
    returns standings rows sorted by Total Points descending, ties broken by AVG
    Points descending, further ties broken by Initial Standing ascending (a team
    with no Initial Standing -- e.g. newly promoted from FCS with no prior-year
    merit history -- sorts after every team that has one), and any remaining tie
    broken alphabetically by team name for determinism. Every roster team is
    included, even ones with zero games played so far (Total Points/AVG Points = 0),
    so weekly snapshots always have the same number of rows.
    """
    initial_standing = {team: i + 1 for i, team in enumerate(seed_order)}

    totals: dict[str, int] = {team: 0 for team in config.roster}
    games_played: dict[str, int] = {team: 0 for team in config.roster}

    for rows in scored_by_week.values():
        for row in rows:
            team = row["Team 1"]
            if team not in totals:
                continue  # shouldn't happen; roster is the source of truth
            totals[team] += row["Total"]
            games_played[team] += 1

    standings = []
    for team in config.roster:
        gp = games_played[team]
        total_pts = totals[team]
        avg_pts = round(total_pts / gp, 2) if gp else 0.0
        standings.append({
            "Team": team,
            "Total Points": total_pts,
            "AVG Points": avg_pts,
            "Initial Standing": initial_standing.get(team, ""),
            "Conference": config.conferences.get(team, ""),
            "_gp": gp,
        })

    no_initial_standing = len(seed_order) + 1
    standings.sort(key=lambda r: (
        -r["Total Points"],
        -r["AVG Points"],
        r["Initial Standing"] if isinstance(r["Initial Standing"], int) else no_initial_standing,
        r["Team"],
    ))
    for i, row in enumerate(standings):
        row["Standing"] = i + 1
        del row["_gp"]

    return standings
