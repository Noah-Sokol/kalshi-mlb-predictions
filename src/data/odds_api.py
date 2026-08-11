"""
The Odds API client — fetches live MLB moneyline odds from major US sportsbooks.

Free tier: 500 requests/month, covers current & upcoming games.
Get a free API key at https://the-odds-api.com

Usage:
    from src.data.odds_api import fetch_mlb_odds, get_home_win_probs
    probs = get_home_win_probs()  # dict: "NYY" -> 0.58
"""
import json
import os
import time
from pathlib import Path
from datetime import date, timezone
from typing import Optional

import requests
from dotenv import load_dotenv
load_dotenv()

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SPORT_KEY = "baseball_mlb"
CACHE_DIR = Path("data/cache/odds_api")

PREFERRED_BOOKS = ["draftkings", "fanduel", "betmgm", "caesars", "bovada"]

# The Odds API team names → our FanGraphs abbreviations
_NAME_TO_FG = {
    "New York Yankees": "NYY", "Boston Red Sox": "BOS", "Tampa Bay Rays": "TBR",
    "Baltimore Orioles": "BAL", "Toronto Blue Jays": "TOR",
    "Chicago White Sox": "CHW", "Cleveland Guardians": "CLE", "Detroit Tigers": "DET",
    "Kansas City Royals": "KCR", "Minnesota Twins": "MIN",
    "Houston Astros": "HOU", "Los Angeles Angels": "LAA", "Oakland Athletics": "OAK",
    "Seattle Mariners": "SEA", "Texas Rangers": "TEX", "Sacramento River Cats": "OAK",
    "Atlanta Braves": "ATL", "Miami Marlins": "MIA", "New York Mets": "NYM",
    "Philadelphia Phillies": "PHI", "Washington Nationals": "WSN",
    "Chicago Cubs": "CHC", "Cincinnati Reds": "CIN", "Milwaukee Brewers": "MIL",
    "Pittsburgh Pirates": "PIT", "St. Louis Cardinals": "STL",
    "Arizona Diamondbacks": "ARI", "Colorado Rockies": "COL",
    "Los Angeles Dodgers": "LAD", "San Diego Padres": "SDP", "San Francisco Giants": "SFG",
    "Cleveland Indians": "CLE", "Los Angeles Angels of Anaheim": "LAA",
    "Athletics": "OAK", "Las Vegas Athletics": "OAK",
}


def _team_to_fg(name: str) -> Optional[str]:
    if not name:
        return None
    fg = _NAME_TO_FG.get(name)
    if fg:
        return fg
    # partial match fallback
    name_lower = name.lower()
    for full, abbr in _NAME_TO_FG.items():
        if full.lower().split()[-1] in name_lower or name_lower.split()[-1] in full.lower():
            return abbr
    return None


def _american_to_prob(odds: int) -> float:
    if odds > 0:
        return 100.0 / (odds + 100.0)
    else:
        return abs(odds) / (abs(odds) + 100.0)


def _remove_vig(prob_home: float, prob_away: float) -> float:
    total = prob_home + prob_away
    return prob_home / total if total > 0 else 0.5


class OddsAPIClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ODDS_API_KEY", "")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def fetch_mlb_odds(self, bookmakers: list[str] = None,
                       cache_hours: float = 4.0) -> tuple[list[dict], int]:
        """
        Returns (games_list, requests_remaining).
        Caches the response for cache_hours so reruns don't burn quota.
        Set cache_hours=0 to always bypass cache.
        """
        import time as _time

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = CACHE_DIR / f"odds_{date.today().isoformat()}.json"

        if cache_path.exists() and cache_hours > 0:
            age_hours = (_time.time() - cache_path.stat().st_mtime) / 3600
            if age_hours < cache_hours:
                with open(cache_path) as f:
                    cached = json.load(f)
                return cached["data"], -1  # -1 signals served from cache

        if not self.configured:
            raise RuntimeError(
                "ODDS_API_KEY not set. Get a free key at https://the-odds-api.com"
            )
        params = {
            "apiKey": self.api_key,
            "regions": "us",
            "markets": "h2h",
            "oddsFormat": "american",
            "bookmakers": ",".join(bookmakers or PREFERRED_BOOKS),
        }
        r = requests.get(
            f"{ODDS_API_BASE}/sports/{SPORT_KEY}/odds",
            params=params, timeout=20
        )
        r.raise_for_status()
        remaining = int(r.headers.get("x-requests-remaining", -1))
        data = r.json()

        with open(cache_path, "w") as f:
            json.dump({"data": data, "remaining": remaining}, f)

        return data, remaining

    def get_home_win_probs(self, cache_hours: float = 4.0) -> dict[str, float]:
        """Returns home_team_fg -> vig-removed home win probability."""
        raw, _ = self.fetch_mlb_odds(cache_hours=cache_hours)
        return self._parse_probs(raw)

    def get_home_win_probs_with_quota(self, cache_hours: float = 4.0) -> tuple[dict[str, float], int]:
        """Returns (home_team_fg -> prob, requests_remaining)."""
        raw, remaining = self.fetch_mlb_odds(cache_hours=cache_hours)
        return self._parse_probs(raw), remaining

    def _parse_probs(self, raw: list) -> dict[str, float]:
        result = {}
        for game in raw:
            home_name = game.get("home_team", "")
            away_name = game.get("away_team", "")
            home_fg = _team_to_fg(home_name)
            if not home_fg:
                continue
            # Average implied probs across available bookmakers
            home_probs, away_probs = [], []
            for book in game.get("bookmakers", []):
                for market in book.get("markets", []):
                    if market.get("key") != "h2h":
                        continue
                    outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}
                    if home_name in outcomes and away_name in outcomes:
                        hp = _american_to_prob(outcomes[home_name])
                        ap = _american_to_prob(outcomes[away_name])
                        home_probs.append(hp)
                        away_probs.append(ap)
            if home_probs:
                avg_home = sum(home_probs) / len(home_probs)
                avg_away = sum(away_probs) / len(away_probs)
                result[home_fg] = round(_remove_vig(avg_home, avg_away), 4)
        return result

    def log_daily_prices(self, out_path: str = "data/market_prices.csv", cache_hours: float = 4.0) -> None:
        """
        Fetch today's odds and append to a running CSV log.
        Format: date, home_team_fg, away_team_fg, market_home_prob
        This accumulates over time to build a real historical dataset for backtest.
        """
        import pandas as pd

        raw, _ = self.fetch_mlb_odds(cache_hours=cache_hours)
        rows = []
        today_str = str(date.today())
        for game in raw:
            home_name = game.get("home_team", "")
            away_name = game.get("away_team", "")
            home_fg = _team_to_fg(home_name)
            away_fg = _team_to_fg(away_name)
            if not home_fg or not away_fg:
                continue
            home_probs, away_probs = [], []
            for book in game.get("bookmakers", []):
                for market in book.get("markets", []):
                    if market.get("key") != "h2h":
                        continue
                    outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}
                    if home_name in outcomes and away_name in outcomes:
                        hp = _american_to_prob(outcomes[home_name])
                        ap = _american_to_prob(outcomes[away_name])
                        home_probs.append(hp)
                        away_probs.append(ap)
            if home_probs:
                avg_home = sum(home_probs) / len(home_probs)
                avg_away = sum(away_probs) / len(away_probs)
                rows.append({
                    "date": today_str,
                    "home_team_fg": home_fg,
                    "away_team_fg": away_fg,
                    "market_home_prob": round(_remove_vig(avg_home, avg_away), 4),
                })

        if not rows:
            print("  No MLB odds found for today.")
            return

        new_df = pd.DataFrame(rows)
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            existing = pd.read_csv(p)
            # Drop any existing rows for today before appending (idempotent)
            existing = existing[existing["date"] != today_str]
            combined = pd.concat([existing, new_df], ignore_index=True)
        else:
            combined = new_df

        combined.to_csv(out_path, index=False)
        print(f"  Logged {len(rows)} game prices to {out_path}")
