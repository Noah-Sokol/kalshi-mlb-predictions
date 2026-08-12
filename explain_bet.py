"""
Show SHAP-based explanation for today's recommended bets.

Usage:
    python explain_bet.py               # explain all today's recommended bets
    python explain_bet.py --top 8       # show top 8 features (default 6)

Reads from data/bets_log.csv (today's rows with a recommended side).
The model must already be trained (run pipeline.py if not).
"""
import os
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

os.chdir(Path(__file__).parent)

from dotenv import load_dotenv
load_dotenv()

from src.models.win_probability import load, _fill_features
from src.edge.explain import explain_bet, format_explanation

LOG_PATH = Path("data/bets_log.csv")


def main():
    top_n = 6
    if "--top" in sys.argv:
        idx = sys.argv.index("--top")
        if idx + 1 < len(sys.argv):
            top_n = int(sys.argv[idx + 1])

    if not LOG_PATH.exists():
        sys.exit("data/bets_log.csv not found. Run update.py first.")

    df = pd.read_csv(LOG_PATH)
    df["date"] = pd.to_datetime(df["date"])
    today = pd.Timestamp(date.today())
    today_bets = df[
        (df["date"].dt.date == today.date()) &
        (df["recommended_side"].isin(["home", "away", "YES", "NO"]))
    ]

    if today_bets.empty:
        print("No recommended bets found for today. Run update.py first.")
        return

    print("Loading model...")
    artifacts = load()

    from src.features.game_features import FEATURE_COLS, NULLABLE_FEATURE_COLS
    from src.data.mlb_api import fetch_today_schedule, fetch_season_games
    from src.features.game_features import build_game_features
    from src.features.bullpen import aggregate_team_bp_quality, compute_rolling_bp_load, compute_rolling_starter_stats
    from src.data.fangraphs import fetch_team_batting, fetch_team_pitching, fetch_pitcher_stats, fetch_batter_stats
    from src.data.mlb_boxscores import fetch_season_pitching_lines
    from src.data.statcast import fetch_team_batting_sc, fetch_team_pitching_sc

    print("Fetching features for today's games...")
    current_year = date.today().year

    try:
        season_games = fetch_season_games(current_year)
        fg_bat  = fetch_team_batting(current_year - 1)
        fg_pit  = fetch_team_pitching(current_year - 1)
        sp_stats = fetch_pitcher_stats(current_year - 1)
        bat_stats = fetch_batter_stats(current_year - 1)
        sc_bat  = fetch_team_batting_sc(current_year - 1)
        sc_pit  = fetch_team_pitching_sc(current_year - 1)
        bp_lines = fetch_season_pitching_lines(current_year)

        bp_qual  = aggregate_team_bp_quality(fg_pit, sp_stats)
        bp_load  = compute_rolling_bp_load(bp_lines)
        sp_roll  = compute_rolling_starter_stats(bp_lines)

        today_games = fetch_today_schedule()
        feat = build_game_features(
            today_games, season_games, fg_bat, fg_pit,
            sc_bat, sc_pit, bp_qual, bp_load, sp_roll, sp_stats
        )
    except Exception as e:
        sys.exit(f"Could not build features: {e}")

    print()
    print("=" * 65)
    print("  BET EXPLANATIONS")
    print("=" * 65)

    for _, row in today_bets.iterrows():
        home = row["home_team"]
        away = row["away_team"]
        model_prob  = float(row["model_home_prob"])
        market_prob = float(row["market_home_prob"])
        bet_side    = row["recommended_side"]
        bet_team    = home if bet_side in ("YES", "home") else away

        # Find this game's feature row
        game_feat = feat[
            (feat["home_team_fg"] == home) | (feat["away_team_fg"] == away)
        ]
        if game_feat.empty:
            print(f"\n{away} @ {home}  —  features not found, skipping")
            continue

        X = _fill_features(game_feat.iloc[[0]], artifacts["medians"])
        model_features = list(artifacts["model"].feature_names_in_)
        for col in model_features:
            if col not in X.columns:
                X[col] = np.nan
        X = X[model_features]

        contribs = explain_bet(X, artifacts, model_prob, top_n=top_n)

        edge = model_prob - market_prob
        edge_str = f"+{edge*100:.1f}%" if edge >= 0 else f"{edge*100:.1f}%"

        print()
        print(f"  {away} @ {home}")
        print(f"  Bet: {bet_team}  |  Model: {model_prob:.1%}  |  Market: {market_prob:.1%}  |  Edge: {edge_str}")
        print()
        print(format_explanation(home, away, model_prob, market_prob, bet_side, contribs))

    print()
    print("=" * 65)


if __name__ == "__main__":
    main()
