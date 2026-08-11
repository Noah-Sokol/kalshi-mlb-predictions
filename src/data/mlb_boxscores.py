"""
MLB Stats API boxscore fetcher — per-pitcher IP and ER for each game.
Used by the bullpen module to compute fatigue (recent reliever IP) and
starter depth (average IP per start).

Each boxscore is cached individually; the first full-history fetch takes ~5–10 min
per season (8 parallel threads). Subsequent runs load from disk instantly.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

CACHE_DIR = Path("data/cache/mlb_api/boxscores")
BASE_URL = "https://statsapi.mlb.com/api/v1"
HEADERS = {"User-Agent": "Mozilla/5.0 (sports-research-model)"}


def _ip_to_decimal(ip_str) -> float:
    """
    Convert baseball IP notation to decimal innings.
    '5.2' → 5.667  (5 full innings + 2 outs = 2/3 inning)
    """
    try:
        s = str(ip_str).strip()
        if "." in s:
            whole, thirds = s.split(".", 1)
            return int(whole) + int(thirds) / 3.0
        return float(s)
    except (ValueError, AttributeError):
        return 0.0


def fetch_game_boxscore(game_id: int) -> dict:
    """Fetch and cache a single game boxscore."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"boxscore_{game_id}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    r = requests.get(f"{BASE_URL}/game/{game_id}/boxscore",
                     headers=HEADERS, timeout=20)
    r.raise_for_status()
    data = r.json()
    with open(path, "w") as f:
        json.dump(data, f)
    time.sleep(0.1)
    return data


def extract_pitching_lines(
    boxscore: dict, side: str, game_id, date: str, team_fg: str
) -> list[dict]:
    """
    Parse one team's pitching lines from a boxscore.

    pitcher_order=0 is the starter (SP); all others are relievers (RP).
    Opener rule: if SP throws < 2.0 IP and a second pitcher exists, it was an opener
    game — we still label order=0 as SP for simplicity; the low IP value naturally
    signals this to the model.
    """
    team_data = boxscore.get("teams", {}).get(side, {})
    pitcher_ids = team_data.get("pitchers", [])
    players = team_data.get("players", {})

    lines = []
    for order, pid in enumerate(pitcher_ids):
        player = players.get(f"ID{pid}", {})
        name = player.get("person", {}).get("fullName", "")
        stats = player.get("stats", {}).get("pitching", {})
        ip_dec = _ip_to_decimal(stats.get("inningsPitched", "0.0"))
        er = int(stats.get("earnedRuns", 0) or 0)
        lines.append({
            "game_id": game_id,
            "date": date,
            "team_fg": team_fg,
            "pitcher_name": name,
            "pitcher_order": order,
            "is_starter": order == 0,
            "ip": ip_dec,
            "er": er,
        })
    return lines


def fetch_season_pitching_lines(
    games: pd.DataFrame,
    max_workers: int = 8,
) -> pd.DataFrame:
    """
    Batch-fetch boxscores for every game in `games` and return long-form pitching lines.

    Parameters:
      games: DataFrame with columns game_id, date, home_team_fg, away_team_fg
      max_workers: parallel threads for uncached requests

    Returns DataFrame with one row per pitcher per game:
      game_id, date, team_fg, pitcher_name, pitcher_order, is_starter, ip, er
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    records = games[["game_id", "date", "home_team_fg", "away_team_fg"]].to_dict("records")

    cached_count = sum(1 for r in records
                       if (CACHE_DIR / f"boxscore_{r['game_id']}.json").exists())
    fresh_count = len(records) - cached_count
    eta = f"~{max(1, int(fresh_count * 0.1 / max_workers))}s" if fresh_count > 0 else "instant"
    print(f"  Boxscores: {cached_count} cached + {fresh_count} to fetch ({eta})...")

    def _fetch_one(row):
        try:
            bs = fetch_game_boxscore(int(row["game_id"]))
            lines = []
            for side, tc in [("home", "home_team_fg"), ("away", "away_team_fg")]:
                lines.extend(extract_pitching_lines(
                    bs, side, row["game_id"],
                    str(row["date"])[:10], row[tc],
                ))
            return lines
        except Exception:
            return []

    all_rows = []
    with ThreadPoolExecutor(max_workers=max_workers) as exe:
        futures = {exe.submit(_fetch_one, r): r for r in records}
        for fut in as_completed(futures):
            result = fut.result()
            if result:
                all_rows.extend(result)

    df = pd.DataFrame(all_rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df
