"""
MLB Stats API client — free, no auth required.
Pulls regular-season game results and today's schedule with probable pitchers.
"""
import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

CACHE_DIR = Path("data/cache/mlb_api")
BASE_URL = "https://statsapi.mlb.com/api/v1"
HEADERS = {"User-Agent": "Mozilla/5.0 (sports-research-model)"}

# Maps MLB Stats API full team name -> FanGraphs abbreviation
TEAM_NAME_TO_FG = {
    "Arizona Diamondbacks": "ARI",
    "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL",
    "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC",
    "Chicago White Sox": "CHW",
    "Cincinnati Reds": "CIN",
    "Cleveland Guardians": "CLE",
    "Cleveland Indians": "CLE",
    "Colorado Rockies": "COL",
    "Detroit Tigers": "DET",
    "Houston Astros": "HOU",
    "Kansas City Royals": "KCR",
    "Los Angeles Angels": "LAA",
    "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA",
    "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN",
    "New York Mets": "NYM",
    "New York Yankees": "NYY",
    "Oakland Athletics": "OAK",
    "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SDP",
    "San Francisco Giants": "SFG",
    "Seattle Mariners": "SEA",
    "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TBR",
    "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSN",
    "Athletics": "OAK",
}


def _get(endpoint: str, params: dict = None, cache_key: str = None,
         max_age_hours: float = None) -> dict:
    """
    max_age_hours: if set and the cache file is older than this, re-fetch.
                   Pass 0 to always bypass cache.
    """
    import time as _time
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if cache_key:
        path = CACHE_DIR / f"{cache_key}.json"
        if path.exists():
            age_hours = (_time.time() - path.stat().st_mtime) / 3600
            if max_age_hours is None or age_hours < max_age_hours:
                with open(path) as f:
                    return json.load(f)
    r = requests.get(f"{BASE_URL}/{endpoint}", params=params or {}, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    if cache_key:
        with open(CACHE_DIR / f"{cache_key}.json", "w") as f:
            json.dump(data, f)
    time.sleep(0.25)
    return data


def fetch_season_games(year: int) -> pd.DataFrame:
    """
    All regular-season games for a year with final scores and probable pitchers.
    Current year: refreshed every 6 hours so new results appear.
    Past years: cached permanently (scores don't change).
    """
    current_year = date.today().year
    max_age = 6.0 if year == current_year else None
    data = _get(
        "schedule",
        params={
            "sportId": 1, "season": year, "gameType": "R",
            "hydrate": "probablePitcher,linescore",
            "startDate": f"{year}-03-01", "endDate": f"{year}-11-01",
        },
        cache_key=f"schedule_{year}",
        max_age_hours=max_age,
    )

    rows = []
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            if game["status"]["abstractGameState"] != "Final":
                continue
            home = game["teams"]["home"]
            away = game["teams"]["away"]
            h_score = home.get("score")
            a_score = away.get("score")
            if h_score is None or a_score is None:
                continue

            rows.append({
                "game_id": game["gamePk"],
                "date": date_entry["date"],
                "home_team": home["team"]["name"],
                "away_team": away["team"]["name"],
                "home_team_fg": TEAM_NAME_TO_FG.get(home["team"]["name"], home["team"]["name"]),
                "away_team_fg": TEAM_NAME_TO_FG.get(away["team"]["name"], away["team"]["name"]),
                "home_score": int(h_score),
                "away_score": int(a_score),
                "home_win": int(int(h_score) > int(a_score)),
                "home_sp": home.get("probablePitcher", {}).get("fullName", ""),
                "away_sp": away.get("probablePitcher", {}).get("fullName", ""),
            })

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    print(f"  {year}: {len(df)} games loaded")
    return df.sort_values("date").reset_index(drop=True)


def fetch_today_schedule() -> pd.DataFrame:
    """Today's games with probable pitchers. Never cached."""
    data = _get(
        "schedule",
        params={
            "sportId": 1, "date": date.today().isoformat(), "gameType": "R",
            "hydrate": "probablePitcher,venue",
        },
        cache_key=None,
    )

    rows = []
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            home = game["teams"]["home"]
            away = game["teams"]["away"]
            rows.append({
                "game_id": game["gamePk"],
                "date": date_entry["date"],
                "home_team": home["team"]["name"],
                "away_team": away["team"]["name"],
                "home_team_fg": TEAM_NAME_TO_FG.get(home["team"]["name"], home["team"]["name"]),
                "away_team_fg": TEAM_NAME_TO_FG.get(away["team"]["name"], away["team"]["name"]),
                "home_sp": home.get("probablePitcher", {}).get("fullName", "TBD"),
                "away_sp": away.get("probablePitcher", {}).get("fullName", "TBD"),
                "venue": game.get("venue", {}).get("name", ""),
                "game_time": game.get("gameDate", ""),
            })

    return pd.DataFrame(rows)
