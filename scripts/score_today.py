"""
Daily scoring script — builds predictions for today's games and compares to Kalshi prices.

Usage:
    python score_today.py                           # show predictions, no Kalshi comparison
    python score_today.py --bankroll 1000           # size bets with $1000 bankroll
    python score_today.py --bankroll 1000 \\
        --kalshi-ticker NYY KMLBWIN-NYY-20260805    # manually supply a ticker

Note on Kalshi tickers:
    Until the Kalshi client is configured, set market prices manually via --manual-prices.
    Format: "home_team_fg:price_cents" e.g. "NYY:62 LAD:48"

    Example: python score_today.py --bankroll 1000 --manual-prices "NYY:62 BOS:45"
"""
import argparse
import os
import sys
from pathlib import Path

import pandas as pd
import warnings
import sys

# Force UTF-8 output so accented names and cent signs display correctly on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Suppress pandas FutureWarnings from internal operations (not our code)
warnings.filterwarnings('ignore', category=FutureWarning)

os.chdir(Path(__file__).parent)

from src.data.mlb_api import fetch_today_schedule, fetch_season_games, TEAM_NAME_TO_FG
from src.data.fangraphs import fetch_team_batting, fetch_team_pitching, fetch_pitcher_stats, fetch_batter_stats
from src.data.kalshi_client import KalshiClient
from src.data.odds_api import OddsAPIClient
from src.data.mlb_boxscores import fetch_season_pitching_lines
from src.data.statcast import fetch_team_batting_sc, fetch_team_pitching_sc
from src.features.bullpen import aggregate_team_bp_quality, compute_rolling_bp_load, compute_rolling_starter_stats
from src.features.game_features import build_game_features
from src.models.win_probability import load, predict
from src.edge.edge_detector import attach_kalshi, find_edges, print_summary


def build_today_features(today: pd.DataFrame, current_year: int) -> pd.DataFrame:
    """
    Build features for today's games.

    Rolling + fatigue stats use this season's games-to-date.
    FanGraphs/Statcast stats use the previous full season (same as training).
    """
    print(f"Fetching season-to-date games for {current_year}...")
    try:
        season_games = fetch_season_games(current_year)
        season_games = season_games[season_games["date"] < pd.Timestamp.now().normalize()]
    except Exception as e:
        print(f"  Warning: could not load {current_year} games ({e}). Rolling stats will be empty.")
        season_games = pd.DataFrame(columns=["game_id", "date", "home_team_fg", "away_team_fg",
                                              "home_score", "away_score", "home_win", "home_sp", "away_sp"])

    prev_year = current_year - 1
    print(f"Fetching {prev_year} FanGraphs stats...")
    bat_prev, sp_prev, rp_prev, pit_prev, bat_indiv_prev = [pd.DataFrame()] * 5
    for fn, kwargs, name in [
        (fetch_team_batting,   {"year": prev_year},               "bat"),
        (fetch_team_pitching,  {"year": prev_year, "role": "sp"}, "sp"),
        (fetch_team_pitching,  {"year": prev_year, "role": "rp"}, "rp"),
        (fetch_pitcher_stats,  {"year": prev_year},               "pitchers"),
        (fetch_batter_stats,   {"year": prev_year},               "batters"),
    ]:
        try:
            result = fn(**kwargs)
            if name == "bat":      bat_prev       = result
            elif name == "sp":     sp_prev        = result
            elif name == "rp":     rp_prev        = result
            elif name == "pitchers": pit_prev     = result
            else:                  bat_indiv_prev = result
        except Exception as e:
            print(f"  {prev_year} {name}: FAILED ({e})")

    # IP-weighted bullpen quality from individual RP stats
    bpq_prev = aggregate_team_bp_quality(pit_prev) if not pit_prev.empty else None

    # Bullpen fatigue + starter recent form from boxscores (last 14 days — fast)
    bp_load = None
    sp_rolling = None
    if not season_games.empty:
        try:
            print("Fetching recent boxscores for bullpen fatigue and starter form...")
            # 45 days covers ~9 starts per pitcher — enough for sp_ip_avg_10g (10-start window)
            cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(days=45)
            recent_games = season_games[season_games["date"] >= cutoff]
            if not recent_games.empty:
                lines = fetch_season_pitching_lines(recent_games, max_workers=4)
                bp_load = compute_rolling_bp_load(lines)
                sp_rolling = compute_rolling_starter_stats(lines)
        except Exception as e:
            print(f"  Bullpen/starter form: FAILED ({e})")

    # Statcast team stats (previous season)
    sc_bat, sc_pit = None, None
    print(f"Fetching {prev_year} Statcast stats...")
    try:
        sc_bat = fetch_team_batting_sc(
            prev_year,
            batters_fg=bat_indiv_prev if not bat_indiv_prev.empty else None,
        )
    except Exception as e:
        print(f"  Statcast bat {prev_year}: FAILED ({e})")
    try:
        sc_pit = fetch_team_pitching_sc(prev_year, pitchers_fg=pit_prev if not pit_prev.empty else None)
    except Exception as e:
        print(f"  Statcast pit {prev_year}: FAILED ({e})")

    # Append today's unplayed games so build_game_features computes their pre-game rolling stats
    placeholder = today.copy()
    placeholder["home_score"] = None
    placeholder["away_score"] = None
    placeholder["home_win"] = None
    placeholder["game_id"] = pd.to_numeric(placeholder["game_id"], errors="coerce")

    all_games = pd.concat([season_games, placeholder], ignore_index=True)

    return build_game_features(
        all_games, bat_prev, sp_prev, rp_prev, pit_prev,
        bp_quality_prev=bpq_prev,
        bp_load=bp_load,
        sc_bat_prev=sc_bat,
        sc_pit_prev=sc_pit,
        sp_rolling_stats=sp_rolling,
    )


def parse_manual_prices(raw: str) -> dict:
    """Parse 'NYY:62 LAD:48' into {'NYY': 0.62, 'LAD': 0.48}."""
    prices = {}
    for part in raw.split():
        team, price = part.split(":")
        prices[team.strip().upper()] = float(price) / 100.0
    return prices


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bankroll", type=float, default=None,
                    help="Total bankroll in dollars for Kelly sizing")
    ap.add_argument("--min-edge", type=float, default=0.04,
                    help="Minimum edge to flag a bet (default 0.04 = 4%%)")
    ap.add_argument("--kelly-frac", type=float, default=0.40,
                    help="Fractional Kelly multiplier (default 0.40)")
    ap.add_argument("--min-bet-mkt-prob", type=float, default=0.40,
                    help="Skip bets where bet-team sportsbook prob < this (0=off, default 0.40)")
    ap.add_argument("--min-team-win-pct",  type=float, default=0.33,
                    help="Skip bets on teams with current-season win%% < this (0=off, default 0.33)")
    ap.add_argument("--market-blend",    type=float, default=0.5,
                    help="Blend model with sportsbook: (1-b)*model + b*sportsbook. Default 0.5 (validated best config).")
    ap.add_argument("--manual-prices", type=str, default=None,
                    help="Manual Kalshi prices: 'NYY:62 LAD:48' (home team FG abbrev : cents)")
    args = ap.parse_args()

    from datetime import date
    current_year = date.today().year

    # ── 1. Today's schedule ─────────────────────────────────────────────────
    print("Fetching today's schedule...")
    today = fetch_today_schedule()
    if today.empty:
        print("No games scheduled today.")
        return
    print(f"  {len(today)} games today\n")

    # ── 2. Build features ───────────────────────────────────────────────────
    feat = build_today_features(today, current_year)
    # Keep only today's rows
    today_feat = feat[feat["home_win"].isna()].copy() if "home_win" in feat.columns else feat.copy()

    # ── 3. Score with model ─────────────────────────────────────────────────
    try:
        artifacts = load()
    except FileNotFoundError:
        sys.exit("Model not found. Run python pipeline.py first to train the model.")

    probs = predict(today_feat, artifacts)

    # -- Quality-gate annotations: flag games that should be skipped
    # (used by edge_detector to apply same filters as backtest.py)
    today_feat = today_feat.copy()
    today_feat["model_home_prob_raw"] = probs.round(4)

    # -- Market blend: if sportsbook odds available in feature matrix, blend
    if args.market_blend > 0 and "market_home_prob" in today_feat.columns:
        sbook = today_feat["market_home_prob"].fillna(probs)  # fall back to model
        probs = (1.0 - args.market_blend) * probs + args.market_blend * sbook.values
        print(f"Market blend: {1-args.market_blend:.0%} model + {args.market_blend:.0%} sportsbook")

    # Join probabilities back by game_id — never assign by position, since today_feat
    # may have a different length or row order than the original today schedule.
    today_feat = today_feat.copy()
    today_feat["model_home_prob"] = probs.round(4)

    # Normalize game_id types before merge so str/int mismatches don't produce NaN
    today = today.copy()
    today["game_id"] = today["game_id"].astype(str)
    today_feat["game_id"] = today_feat["game_id"].astype(str)

    # Merge model prob + quality-gate columns into schedule so find_edges() can filter
    feat_cols_to_merge = ["game_id", "model_home_prob"]
    for _qc in ["h_rolling_win_pct", "a_rolling_win_pct", "market_home_prob"]:
        if _qc in today_feat.columns:
            feat_cols_to_merge.append(_qc)
    today = today.merge(
        today_feat[feat_cols_to_merge],
        on="game_id", how="left",
    )
    today["model_home_prob"] = today["model_home_prob"].fillna(0.5)
    today["model_away_prob"] = (1.0 - today["model_home_prob"]).round(4)

    # ── 4. Attach market prices (Kalshi manual > Odds API auto > none) ─────────
    if args.manual_prices:
        manual = parse_manual_prices(args.manual_prices)
        today["kalshi_home_prob"] = today["home_team_fg"].map(manual)
        today["kalshi_home_price"] = today["kalshi_home_prob"].apply(
            lambda p: round(p * 100, 1) if p is not None else None
        )
    else:
        # Try Kalshi API first, then fall back to The Odds API (sportsbook lines)
        prices_attached = False
        client = KalshiClient()
        if client._configured:
            today = attach_kalshi(today, client)
            prices_attached = today["kalshi_home_prob"].notna().any()

        if not prices_attached:
            odds_client = OddsAPIClient()
            if odds_client.configured:
                try:
                    print("Fetching live MLB odds from The Odds API...")
                    home_probs = odds_client.get_home_win_probs()
                    today["kalshi_home_prob"] = today["home_team_fg"].map(home_probs)
                    today["kalshi_home_price"] = today["kalshi_home_prob"].apply(
                        lambda p: round(p * 100, 1) if pd.notna(p) else None
                    )
                    # Log prices for future backtesting
                    try:
                        odds_client.log_daily_prices()
                    except Exception as e:
                        print(f"  Price logging failed: {e}")
                except Exception as e:
                    print(f"  Odds API failed: {e}")
                    today["kalshi_home_prob"] = None
                    today["kalshi_home_price"] = None
            else:
                today["kalshi_home_prob"] = None
                today["kalshi_home_price"] = None
                print("No market prices available.")
                print("  Set KALSHI_API_KEY or ODDS_API_KEY, or use --manual-prices 'TEAM:cents ...'")

    # ── 5. Print predictions ────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  TODAY'S GAME PREDICTIONS")
    print(f"{'='*70}")
    for _, row in today.iterrows():
        matchup  = f"{row['away_team']} @ {row['home_team']}"
        starters = f"  {row.get('away_sp','?')} vs {row.get('home_sp','?')}"
        model    = f"  Model: Home {row['model_home_prob']:.1%} | Away {row['model_away_prob']:.1%}"
        mkt_str  = ""
        if row.get("kalshi_home_price") is not None:
            mkt_str = f"  Kalshi: Home {row['kalshi_home_price']}¢"
        print(f"\n  {matchup}\n{starters}\n{model}{mkt_str}")

    # ── 6. Edge detection and Kelly sizing ──────────────────────────────────
    if args.bankroll is not None and today["kalshi_home_prob"].notna().any():
        print()
        edges = find_edges(
            today,
            bankroll=args.bankroll,
            min_edge=args.min_edge,
            kelly_frac=args.kelly_frac,
            min_bet_mkt_prob=args.min_bet_mkt_prob,
            min_team_win_pct=args.min_team_win_pct,
        )
        print_summary(edges)
    elif args.bankroll is not None:
        print("\nNo Kalshi prices available — skipping edge detection.")
        print("Use --manual-prices 'TEAM:cents ...' to add prices manually.")

    print()


if __name__ == "__main__":
    main()
