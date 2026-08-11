"""
FanGraphs data fetcher — team batting, team pitching (SP/RP split), individual pitcher stats.
Follows the same caching pattern as WAR_predition/war_pipeline.py.
"""
import re
import time
from pathlib import Path

import pandas as pd
import requests

CACHE_DIR = Path("data/cache/fangraphs")
FG_URL = "https://www.fangraphs.com/api/leaders/major-league/data"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126 Safari/537.36"
    ),
    "Accept": "application/json",
}

# FanGraphs uses its own abbreviations; map to a standard key for joining
# FanGraphs uses shortened abbreviations for some teams; normalize to match TEAM_NAME_TO_FG in mlb_api.py
FG_ABBREV_NORMALIZE = {
    "KC":  "KCR",
    "SD":  "SDP",
    "SF":  "SFG",
    "TB":  "TBR",
    "WSH": "WSN",
}


def _fetch(params: dict, cache_key: str) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{cache_key}.csv"
    if path.exists():
        return pd.read_csv(path)
    r = requests.get(FG_URL, params=params, headers=HEADERS, timeout=60)
    r.raise_for_status()
    js = r.json()
    data = js["data"] if isinstance(js, dict) and "data" in js else js
    df = pd.DataFrame(data)
    df.to_csv(path, index=False)
    time.sleep(0.5)
    return df


def _strip_html(val) -> str:
    """Strip HTML anchor tags from a FanGraphs cell, e.g. '<a href="...">MIL</a>' → 'MIL'."""
    if not isinstance(val, str) or '<' not in val:
        return val
    cleaned = re.sub(r'<[^>]+>', '', val).strip()
    return cleaned if cleaned else None


def _normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    renames = {
        "PlayerName": "Team", "playerName": "Team",
        "wRC_plus": "wRC+", "wRCPlus": "wRC+",
        "BB_pct": "BB%", "K_pct": "K%",
        "xFIP_minus": "xFIP-",
    }
    safe = {k: v for k, v in renames.items() if k in df.columns and v not in df.columns}
    renamed = df.rename(columns=safe)

    # Dedup using iloc so it works even when columns already have duplicates
    # keep='last' → prefer the clean text column (FG sends HTML version first, clean version second)
    seen: dict[str, int] = {}
    for i, col in enumerate(renamed.columns):
        seen[col] = i  # overwrite — last index wins
    keep_idx = sorted(seen.values())
    result = renamed.iloc[:, keep_idx]

    # Strip HTML from string columns that FanGraphs wraps in anchor tags
    for col in ["Name", "Team", "TeamName", "TeamNameAbb"]:
        if col in result.columns:
            result = result.copy()
            result[col] = result[col].apply(_strip_html)
            # Traded-player rows show "- - -" for Team — treat as NaN
            result[col] = result[col].replace("- - -", None)

    return result


def _normalize_team_abbrev(df: pd.DataFrame) -> pd.DataFrame:
    """Reconcile FanGraphs short abbreviations (KC, SD, SF, TB, WSH) to standard set."""
    if "Team" in df.columns:
        df = df.copy()
        df["Team"] = df["Team"].map(
            lambda x: FG_ABBREV_NORMALIZE.get(str(x).strip(), str(x).strip()) if pd.notna(x) else x
        )
    return df


def fetch_team_batting(year: int) -> pd.DataFrame:
    """
    Team batting: wRC+, wOBA, BB%, K%, ISO, R/G.

    year: Season year

    Returns DataFrame with team batting stats
    """
    params = {
        "pos": "all", "stats": "bat", "lg": "all", "qual": 0,
        "season": year, "season1": year, "ind": 0,
        "team": "0,ts", "type": 8,
        "pageitems": 50, "pagenum": 1,
    }
    df = _normalize_team_abbrev(_normalize_cols(_fetch(params, f"team_bat_{year}")))
    df["Season"] = year
    return df


def fetch_team_pitching(year: int, role: str = "all") -> pd.DataFrame:
    """
    Team pitching: ERA, FIP, xFIP, SIERA.
    role = 'all' | 'sp' (starters) | 'rp' (relievers)
    """
    params = {
        "pos": "all", "stats": "pit", "lg": "all", "qual": 0,
        "season": year, "season1": year, "ind": 0,
        "team": "0,ts", "type": 8,
        "pageitems": 50, "pagenum": 1,
    }
    if role == "sp":
        params["starter"] = 1
    elif role == "rp":
        params["starter"] = 0

    df = _normalize_team_abbrev(_normalize_cols(_fetch(params, f"team_pit_{role}_{year}")))
    df["Season"] = year
    df["role"] = role
    return df


def fetch_pitcher_stats(year: int) -> pd.DataFrame:
    """
    Individual pitcher season stats for joining to game starters.
    Classifies each pitcher as SP or RP based on GS/G ratio.
    """
    params = {
        "pos": "all", "stats": "pit", "lg": "all", "qual": 0,
        "season": year, "season1": year, "ind": 1,
        "type": 8, "pageitems": 2000, "pagenum": 1,
    }
    df = _normalize_cols(_fetch(params, f"pitchers_{year}"))
    df["Season"] = year

    # FanGraphs individual endpoint wraps Name and Team in HTML anchor tags.
    # PlayerName and TeamNameAbb are the clean versions — use them instead.
    if "PlayerName" in df.columns:
        df = df.drop(columns=["Name"], errors="ignore")
        df = df.rename(columns={"PlayerName": "Name"})
    if "TeamNameAbb" in df.columns:
        df = df.drop(columns=["Team"], errors="ignore")
        df = df.rename(columns={"TeamNameAbb": "Team"})

    # Strip any residual HTML and normalize team abbreviations
    for col in ["Name", "Team"]:
        if col in df.columns:
            df[col] = df[col].apply(_strip_html)
    df = _normalize_team_abbrev(df)

    for col in ["G", "GS"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    if "G" in df.columns and "GS" in df.columns:
        df["is_sp"] = (df["GS"] / df["G"].replace(0, 1)) >= 0.5
    else:
        df["is_sp"] = False

    return df


def fetch_batter_stats(year: int) -> pd.DataFrame:
    """
    Individual batter season stats — used to resolve Baseball Savant player_id → team.
    Returns xMLBAMID (= Savant player_id), Team, PA, and basic slash stats.
    """
    params = {
        "pos": "all", "stats": "bat", "lg": "all", "qual": 0,
        "season": year, "season1": year, "ind": 1,
        "type": 8, "pageitems": 5000, "pagenum": 1,
    }
    df = _normalize_cols(_fetch(params, f"batters_{year}"))
    df["Season"] = year

    if "PlayerName" in df.columns:
        df = df.drop(columns=["Name"], errors="ignore")
        df = df.rename(columns={"PlayerName": "Name"})
    if "TeamNameAbb" in df.columns:
        df = df.drop(columns=["Team"], errors="ignore")
        df = df.rename(columns={"TeamNameAbb": "Team"})

    for col in ["Name", "Team"]:
        if col in df.columns:
            df[col] = df[col].apply(_strip_html)
    df = _normalize_team_abbrev(df)

    return df


def fetch_multi_year(fetch_fn, start_year: int, end_year: int, **kwargs) -> pd.DataFrame:
    frames = []
    for yr in range(start_year, end_year + 1):
        try:
            frames.append(fetch_fn(yr, **kwargs))
            print(f"  FG {fetch_fn.__name__} {yr}: OK", end="\r")
        except Exception as e:
            print(f"\n  FG {fetch_fn.__name__} {yr}: FAILED ({e})")
    if frames:
        print()
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
