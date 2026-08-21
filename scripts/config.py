"""Scoring constants and roster/conference config, loaded from teams.json.

Point values reflect the 2026-season rule revision (confirmed with the site
owner 2026-08-21): Win=3, Conf=2, quality win requires a home margin of 14+
(road wins always qualify), and a new quality-loss bonus rewards close losses
to currently top-30 teams.
"""
import json
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent

WIN_POINTS = 3
QUAL_WIN_POINTS = 2
QUAL_WIN_HOME_MIN_MARGIN = 14  # home win qualifies if margin >= this; any road win always qualifies
CONF_BONUS_POINTS = 2
SOS_TIERS = (
    (30, 3),
    (60, 2),
    (100, 1),
)  # (rank <= threshold, bonus points), first match wins, else 0

QUAL_LOSS_POINTS = 3
QUAL_LOSS_RANK_THRESHOLD = 30  # opponent must be currently ranked at or above this
QUAL_LOSS_MAX_MARGIN = 7  # loss must be by this many points or fewer

FCS_LABEL = "FCS"


def load_teams_config(path: Path = None) -> dict:
    path = path or (SCRIPTS_DIR / "teams.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class Config:
    def __init__(self, teams_path: Path = None):
        raw = load_teams_config(teams_path)
        self.conferences: dict[str, str] = raw["conferences"]
        self.aliases: dict[str, str] = raw["aliases"]
        self.roster: list[str] = sorted(self.conferences.keys())
        power_confs = set(raw["power_conferences"])
        self.power_bonus_teams: set[str] = {
            team for team, conf in self.conferences.items() if conf in power_confs
        } | set(raw["power_bonus_extra"])

    def sos_bonus(self, opponent_rank: int | None) -> int:
        if opponent_rank is None:
            return 0
        for threshold, bonus in SOS_TIERS:
            if opponent_rank <= threshold:
                return bonus
        return 0
