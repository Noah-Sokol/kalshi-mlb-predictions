"""
Park run factors — FanGraphs 5-year basic run factor (2024 edition).
Values > 1.0 = hitter-friendly, < 1.0 = pitcher-friendly.
Update annually: https://www.fangraphs.com/guts.aspx?type=pf
"""
import pandas as pd

# Raw FanGraphs 5-year basic run park factor (index = 100 is neutral)
_PARK_FACTORS_RAW = {
    "COL": 115, "TEX": 108, "MIN": 107, "CIN": 104,
    "BOS": 103, "MIL": 102, "PHI": 101,
    "NYY": 100, "CHC": 100, "TOR": 100,
    "ATL": 99,  "KCR": 99,
    "BAL": 98,  "PIT": 98,  "ARI": 98,
    "LAA": 97,  "STL": 97,  "CLE": 97,  "CHW": 97,  "DET": 96,
    "LAD": 96,  "HOU": 96,
    "NYM": 95,  "WSN": 95,
    "TBR": 94,  "SDP": 94,  "SEA": 94,
    "OAK": 93,  "SFG": 92,  "MIA": 91,
}

PARK_FACTORS = {team: raw / 100.0 for team, raw in _PARK_FACTORS_RAW.items()}
DEFAULT_FACTOR = 1.0


def get_park_factor(fg_team_abbrev: str) -> float:
    return PARK_FACTORS.get(fg_team_abbrev, DEFAULT_FACTOR)


def as_dataframe() -> pd.DataFrame:
    return pd.DataFrame([
        {"team_fg": team, "park_run_factor": factor}
        for team, factor in PARK_FACTORS.items()
    ])
