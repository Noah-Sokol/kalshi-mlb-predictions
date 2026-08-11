"""
Baseball Savant (Statcast) team-level features — adapts the caching pattern
from WAR_predition/war_pipeline.py but targets team aggregates instead of individual batters.

Batting:  xwOBA vs actual wOBA gap (regression signal), barrel rate, hard-hit rate.
Pitching: same metrics for what pitchers ALLOW (aggregated from individual pitcher data,
          weighted by batters faced / PA).

Data is used for the PREVIOUS season (no lookahead), same as FanGraphs stats.
Caches to data/cache/statcast/ per year-endpoint.
"""
import io
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

CACHE_DIR = Path("data/cache/statcast")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126 Safari/537.36"}

_ENDPOINTS = {
    # Team-level batting expected stats
    "team_bat": (
        "https://baseballsavant.mlb.com/leaderboard/expected_statistics"
        "?type=team&year={yr}&position=&team=&min=1&csv=true"
    ),
    # Team-level exit velocity / barrel data
    "team_bat_ev": (
        "https://baseballsavant.mlb.com/leaderboard/statcast"
        "?type=team&year={yr}&position=&team=&min=1&csv=true"
    ),
    # Individual pitcher expected stats — aggregated to team level in Python
    "pit_expected": (
        "https://baseballsavant.mlb.com/leaderboard/expected_statistics"
        "?type=pitcher&year={yr}&position=&team=&filterType=pa&min=1&csv=true"
    ),
}

# MLB Stats API numeric team ID → FanGraphs abbreviation
# These IDs are stable across seasons; the mapping handles the 5 FG abbreviation divergences.
MLB_TEAM_ID_TO_FG = {
    108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS",
    112: "CHC", 113: "CIN", 114: "CLE", 115: "COL",
    116: "DET", 117: "HOU", 118: "KCR", 119: "LAD",
    120: "WSN", 121: "NYM", 133: "OAK", 134: "PIT",
    135: "SDP", 136: "SEA", 137: "SFG", 138: "STL",
    139: "TBR", 140: "TEX", 141: "TOR", 142: "MIN",
    143: "PHI", 144: "ATL", 145: "CHW", 146: "MIA",
    147: "NYY", 158: "MIL",
}

# Baseball Savant sometimes stores team abbreviations that differ from our FG standard
_SC_ABBREV_TO_FG = {
    "KC": "KCR", "SD": "SDP", "SF": "SFG", "TB": "TBR", "WSH": "WSN",
}


def _fetch_csv(endpoint_key: str, yr: int) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{endpoint_key}_{yr}.csv"
    if path.exists():
        return pd.read_csv(path)
    url = _ENDPOINTS[endpoint_key].format(yr=yr)
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    if df.empty:
        raise ValueError(f"Empty response from {endpoint_key} {yr}")
    df.to_csv(path, index=False)
    time.sleep(0.5)
    return df


def _resolve_team_fg(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add 'team_fg' column from whichever identifier column Baseball Savant provides.
    For team-level endpoints, 'player_id' is the numeric MLB team ID.
    For individual endpoints, 'team_id' holds the numeric team ID.
    """
    df = df.copy()

    # Try numeric team ID first (most reliable)
    for id_col in ["player_id", "team_id"]:
        if id_col in df.columns:
            mapped = pd.to_numeric(df[id_col], errors="coerce").map(MLB_TEAM_ID_TO_FG)
            if mapped.notna().any():
                df["team_fg"] = mapped
                return df

    # Fall back to abbreviation column
    for abbr_col in ["player_name", "team_name", "team_abbrev", "team"]:
        if abbr_col in df.columns:
            df["team_fg"] = df[abbr_col].map(
                lambda x: _SC_ABBREV_TO_FG.get(str(x).strip(), str(x).strip())
                if pd.notna(x) else np.nan
            )
            if df["team_fg"].notna().any():
                return df

    df["team_fg"] = np.nan
    return df


def fetch_team_batting_sc(year: int, batters_fg: pd.DataFrame = None) -> pd.DataFrame:
    """
    Team batting Statcast for one season — PA-weighted aggregate from individual batter data.
    Returns one row per team: team_fg, xwoba_gap, brl_pct, hard_hit_pct, avg_ev.

    Both Savant endpoints (team_bat, team_bat_ev) actually return individual batter rows
    despite the 'type=team' URL param. We aggregate to team level using FanGraphs
    xMLBAMID to resolve each batter's team (same approach as pitching).

    batters_fg: output of fangraphs.fetch_batter_stats(); must have xMLBAMID and Team.
    """
    def _build_id_map(batters_fg):
        if batters_fg is None or batters_fg.empty:
            return {}
        if "xMLBAMID" not in batters_fg.columns or "Team" not in batters_fg.columns:
            return {}
        return (
            batters_fg.dropna(subset=["xMLBAMID", "Team"])
            .assign(xMLBAMID=lambda d: pd.to_numeric(d["xMLBAMID"], errors="coerce"))
            .dropna(subset=["xMLBAMID"])
            .drop_duplicates(subset=["xMLBAMID"])
            .set_index("xMLBAMID")["Team"]
            .to_dict()
        )

    col_map = {
        "est_woba_minus_woba_diff": "xwoba_gap",
        "pa": "pa",
        "barrel_batted_rate":  "brl_pct",
        "hard_hit_percent":    "hard_hit_pct",
        "avg_hit_speed":       "avg_ev",
        "avg_exit_velocity":   "avg_ev",
        "brl_percent":         "brl_pct",
        "hard_hit_pct":        "hard_hit_pct",
    }

    id_map = _build_id_map(batters_fg)

    def _load_and_resolve(endpoint):
        try:
            df = _fetch_csv(endpoint, year)
        except Exception as e:
            print(f"  Statcast {endpoint} {year}: FAILED ({e})")
            return pd.DataFrame()
        df = df.rename(columns={k: v for k, v in col_map.items()
                                 if k in df.columns and v not in df.columns})
        if id_map:
            df["team_fg"] = pd.to_numeric(df.get("player_id", pd.Series(dtype=float)),
                                           errors="coerce").map(id_map)
        else:
            df = _resolve_team_fg(df)
        for c in ["xwoba_gap", "brl_pct", "hard_hit_pct", "avg_ev", "pa"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df

    bat = _load_and_resolve("team_bat")
    ev  = _load_and_resolve("team_bat_ev")

    def _agg(df, stats):
        if df.empty or "team_fg" not in df.columns or "pa" not in df.columns:
            return pd.DataFrame()
        df = df.dropna(subset=["team_fg", "pa"])
        df = df[df["pa"] > 0]
        rows = []
        for team, grp in df.groupby("team_fg"):
            row = {"team_fg": team}
            for stat in stats:
                if stat in grp.columns and grp[stat].notna().any():
                    valid = grp.dropna(subset=[stat])
                    row[stat] = round((valid[stat] * valid["pa"]).sum() / valid["pa"].sum(), 4)
            rows.append(row)
        return pd.DataFrame(rows)

    result = _agg(bat, ["xwoba_gap"])
    ev_agg = _agg(ev, ["brl_pct", "hard_hit_pct", "avg_ev"])

    if result.empty and ev_agg.empty:
        return pd.DataFrame()
    if result.empty:
        return ev_agg
    if not ev_agg.empty:
        result = result.merge(ev_agg, on="team_fg", how="outer")

    return result.dropna(subset=["team_fg"]).reset_index(drop=True)


def fetch_team_pitching_sc(year: int, pitchers_fg: pd.DataFrame = None) -> pd.DataFrame:
    """
    Team pitching Statcast — what each team's pitchers ALLOW, weighted by PA.

    Aggregates individual pitcher expected stats to team level.
    Returns one row per team: team_fg, pit_xwoba_gap, pit_brl_pct_allowed, pit_hard_hit_allowed.

    pit_xwoba_gap > 0 means pitchers are allowing harder contact than ERA reflects
    -> expect ERA regression upward (pitching quality overstated by results).

    pitchers_fg: FanGraphs individual pitcher stats (fetch_pitcher_stats output).
                 The pit_expected endpoint has no team column; we resolve team via
                 FanGraphs xMLBAMID (== Baseball Savant player_id).
    """
    try:
        pit = _fetch_csv("pit_expected", year)
    except Exception as e:
        print(f"  Statcast pit_expected {year}: FAILED ({e})")
        return pd.DataFrame()

    # pit_expected has no team column — resolve via FanGraphs xMLBAMID cross-reference
    if (pitchers_fg is not None and not pitchers_fg.empty
            and "xMLBAMID" in pitchers_fg.columns and "Team" in pitchers_fg.columns):
        id_map = (
            pitchers_fg.dropna(subset=["xMLBAMID", "Team"])
            .assign(xMLBAMID=lambda d: pd.to_numeric(d["xMLBAMID"], errors="coerce"))
            .dropna(subset=["xMLBAMID"])
            .drop_duplicates(subset=["xMLBAMID"])
            .set_index("xMLBAMID")["Team"]
            .to_dict()
        )
        pit["team_fg"] = pd.to_numeric(pit["player_id"], errors="coerce").map(id_map)
    else:
        pit = _resolve_team_fg(pit)  # fallback (no team column in this endpoint)

    col_map = {
        "est_woba_minus_woba_diff": "xwoba_gap",
        "xwoba_diff":               "xwoba_gap",
        "barrel_batted_rate":       "brl_pct",
        "hard_hit_percent":         "hard_hit_pct",
        "hard_hit_pct":             "hard_hit_pct",
        "pa":                       "pa",
    }
    pit = pit.rename(columns={k: v for k, v in col_map.items()
                               if k in pit.columns and v not in pit.columns})

    for c in ["xwoba_gap", "brl_pct", "hard_hit_pct", "pa"]:
        if c in pit.columns:
            pit[c] = pd.to_numeric(pit[c], errors="coerce")

    if "team_fg" not in pit.columns or "pa" not in pit.columns:
        return pd.DataFrame()

    pit = pit.dropna(subset=["team_fg", "pa"])
    pit = pit[pd.to_numeric(pit["pa"], errors="coerce") > 0]

    rows = []
    for team, grp in pit.groupby("team_fg"):
        row = {"team_fg": team}
        for stat, out_name in [("xwoba_gap", "pit_xwoba_gap"),
                                ("brl_pct",   "pit_brl_pct_allowed"),
                                ("hard_hit_pct", "pit_hard_hit_allowed")]:
            if stat in grp.columns and grp[stat].notna().any():
                valid = grp.dropna(subset=[stat])
                row[out_name] = round((valid[stat] * valid["pa"]).sum() / valid["pa"].sum(), 4)
        rows.append(row)

    return pd.DataFrame(rows).dropna(subset=["team_fg"]).reset_index(drop=True)
