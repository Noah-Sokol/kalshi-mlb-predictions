"""
Training pipeline — fetches data, engineers features, trains model, saves artifacts.

Usage:
    python pipeline.py                         # train on 2015-2024
    python pipeline.py --start 2018            # shorter history
    python pipeline.py --test-seasons 3        # hold out 3 seasons for evaluation
    python pipeline.py --skip-boxscores        # skip boxscore fetch (faster, no fatigue features)
    python pipeline.py --skip-statcast         # skip Statcast fetch (faster, no xwOBA features)
    python pipeline.py --skip-historical-odds  # skip merging real sportsbook odds into features
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

import os; os.chdir(Path(__file__).parent)

from src.data.mlb_api import fetch_season_games
from src.data.fangraphs import fetch_team_batting, fetch_team_pitching, fetch_pitcher_stats, fetch_batter_stats
from src.data.mlb_boxscores import fetch_season_pitching_lines
from src.data.statcast import fetch_team_batting_sc, fetch_team_pitching_sc
from src.features.bullpen import aggregate_team_bp_quality, compute_rolling_bp_load, compute_rolling_starter_stats
from src.features.game_features import build_game_features
from src.models.win_probability import train


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=2015)
    ap.add_argument("--end",   type=int, default=2024)
    ap.add_argument("--test-seasons", type=int, default=2)
    ap.add_argument("--skip-boxscores", action="store_true",
                    help="Skip boxscore fetch; omits bullpen fatigue features")
    ap.add_argument("--skip-statcast", action="store_true",
                    help="Skip Statcast fetch; omits xwOBA/barrel features")
    ap.add_argument("--skip-historical-odds", action="store_true",
                    help="Skip merging historical sportsbook closing lines into feature matrix")
    args = ap.parse_args()

    START, END = args.start, args.end
    print(f"\n{'='*60}")
    print(f"  MLB Win Probability Model — training on {START}–{END}")
    print(f"{'='*60}\n")

    # ── 1. Fetch game results ────────────────────────────────────────────────
    print("Fetching game results from MLB Stats API...")
    game_frames = {}
    all_game_frames = []
    for yr in range(START, END + 1):
        try:
            df = fetch_season_games(yr)
            game_frames[yr] = df
            all_game_frames.append(df)
        except Exception as e:
            print(f"  {yr}: FAILED ({e})")
    if not all_game_frames:
        sys.exit("No game data fetched.")
    games_all = pd.concat(all_game_frames, ignore_index=True)
    print(f"  Total: {len(games_all):,} games\n")

    # ── 2. Fetch boxscores → bullpen load ───────────────────────────────────
    bp_load_by_year: dict[int, pd.DataFrame] = {}
    sp_rolling_by_year: dict[int, pd.DataFrame] = {}
    if not args.skip_boxscores:
        print("Fetching game boxscores for bullpen fatigue and starter form metrics...")
        for yr in range(START, END + 1):
            if yr not in game_frames:
                continue
            try:
                lines = fetch_season_pitching_lines(game_frames[yr])
                bp_load_by_year[yr] = compute_rolling_bp_load(lines)
                sp_rolling_by_year[yr] = compute_rolling_starter_stats(lines)
                print(f"  {yr}: bullpen load + starter form computed")
            except Exception as e:
                print(f"  {yr}: boxscore/bullpen FAILED ({e})")
        print()
    else:
        print("Skipping boxscores (--skip-boxscores).\n")

    # ── 3. Fetch FanGraphs stats (previous season for each training year) ────
    print("Fetching FanGraphs team batting...")
    bat_frames, sp_frames, rp_frames, pit_frames, batter_frames = [], [], [], [], []
    for yr in range(START - 1, END):
        try:
            df = fetch_team_batting(yr); df["stat_season"] = yr; bat_frames.append(df)
        except Exception as e:
            print(f"  {yr} bat: FAILED ({e})")
        try:
            df = fetch_team_pitching(yr, role="sp"); df["stat_season"] = yr; sp_frames.append(df)
        except Exception as e:
            print(f"  {yr} sp: FAILED ({e})")
        try:
            df = fetch_team_pitching(yr, role="rp"); df["stat_season"] = yr; rp_frames.append(df)
        except Exception as e:
            print(f"  {yr} rp: FAILED ({e})")
        try:
            pit_frames.append(fetch_pitcher_stats(yr))
        except Exception as e:
            print(f"  {yr} pitchers: FAILED ({e})")
        try:
            batter_frames.append(fetch_batter_stats(yr))
        except Exception as e:
            print(f"  {yr} batters: FAILED ({e})")
    team_bat    = pd.concat(bat_frames,    ignore_index=True) if bat_frames    else pd.DataFrame()
    team_pit_sp = pd.concat(sp_frames,     ignore_index=True) if sp_frames     else pd.DataFrame()
    team_pit_rp = pd.concat(rp_frames,     ignore_index=True) if rp_frames     else pd.DataFrame()
    pitchers    = pd.concat(pit_frames,    ignore_index=True) if pit_frames    else pd.DataFrame()
    batters     = pd.concat(batter_frames, ignore_index=True) if batter_frames else pd.DataFrame()
    print()

    # ── 4. Fetch Statcast team stats (previous season for each training year) ─
    sc_bat_by_year: dict[int, pd.DataFrame] = {}
    sc_pit_by_year: dict[int, pd.DataFrame] = {}
    if not args.skip_statcast:
        print("Fetching Statcast team batting stats...")
        for yr in range(START - 1, END):
            try:
                bat_yr = batters[batters["Season"] == yr] if not batters.empty else None
                sc_bat_by_year[yr] = fetch_team_batting_sc(yr, batters_fg=bat_yr)
                print(f"  {yr} SC bat: {len(sc_bat_by_year[yr])} teams", end="\r")
            except Exception as e:
                print(f"\n  {yr} SC bat: FAILED ({e})")
        print("\nFetching Statcast team pitching stats...")
        for yr in range(START - 1, END):
            try:
                pit_yr = pitchers[pitchers["Season"] == yr] if not pitchers.empty else None
                sc_pit_by_year[yr] = fetch_team_pitching_sc(yr, pitchers_fg=pit_yr)
                print(f"  {yr} SC pit: {len(sc_pit_by_year[yr])} teams", end="\r")
            except Exception as e:
                print(f"\n  {yr} SC pit: FAILED ({e})")
        print()
    else:
        print("Skipping Statcast (--skip-statcast).\n")

    # ── 5. Build features year-by-year ───────────────────────────────────────
    print("Building game feature matrix...")
    feat_frames = []
    for yr in range(START, END + 1):
        yr_games = game_frames.get(yr, pd.DataFrame())
        if yr_games.empty:
            continue

        prev_yr = yr - 1

        def _prev(df, col="stat_season"):
            if df.empty: return pd.DataFrame()
            return df[df[col] == prev_yr]

        bat_prev = _prev(team_bat)
        sp_prev  = _prev(team_pit_sp)
        rp_prev  = _prev(team_pit_rp)
        pit_prev = pitchers[pitchers["Season"] == prev_yr] if not pitchers.empty else pd.DataFrame()

        # IP-weighted bullpen quality from individual RP stats (prev season)
        bpq_prev = aggregate_team_bp_quality(pit_prev) if not pit_prev.empty else None

        # Bullpen fatigue + starter recent form from boxscores (current season)
        bp_load_yr = bp_load_by_year.get(yr)
        sp_rolling_yr = sp_rolling_by_year.get(yr)

        sc_bat = sc_bat_by_year.get(prev_yr)
        sc_pit = sc_pit_by_year.get(prev_yr)

        try:
            feat_yr = build_game_features(
                yr_games, bat_prev, sp_prev, rp_prev, pit_prev,
                bp_quality_prev=bpq_prev,
                bp_load=bp_load_yr,
                sc_bat_prev=sc_bat,
                sc_pit_prev=sc_pit,
                sp_rolling_stats=sp_rolling_yr,
            )
            feat_frames.append(feat_yr)
            print(f"  {yr}: {len(feat_yr):,} games x {feat_yr.shape[1]} columns")
        except Exception as e:
            print(f"  {yr}: feature build FAILED ({e})")

    if not feat_frames:
        sys.exit("No features built.")

    feat = pd.concat(feat_frames, ignore_index=True)
    print(f"Feature matrix: {feat.shape[0]:,} games x {feat.shape[1]} columns")

    # ── 6. Merge historical sportsbook odds (closing lines, 2021-2025) ────────────────
    if not args.skip_historical_odds:
        print("Merging historical sportsbook odds (SBR closing lines)...")
        try:
            from src.data.historical_odds_parser import load_historical_odds
            odds_df = load_historical_odds()
            odds_df["_ds"] = pd.to_datetime(odds_df["date"]).dt.strftime("%Y-%m-%d")
            feat["_ds"]    = pd.to_datetime(feat["date"]).dt.strftime("%Y-%m-%d")
            ODDS_COLS = [
                "_ds", "home_team_fg", "away_team_fg",
                "market_home_prob", "market_away_prob",
                "n_bookmakers", "bookmaker_spread", "avg_vig",
                "suspicious", "odds_source",
            ]
            # Drop any stale odds columns so re-runs stay clean
            drop_existing = [c for c in ODDS_COLS[3:] if c in feat.columns]
            if drop_existing:
                feat = feat.drop(columns=drop_existing)
            feat = feat.merge(
                odds_df[ODDS_COLS],
                on=["_ds", "home_team_fg", "away_team_fg"],
                how="left",
            ).drop(columns=["_ds"])
            feat["odds_source"] = feat["odds_source"].fillna("pythagorean_fallback")
            n_real  = (feat["odds_source"] == "real").sum()
            n_total = len(feat)
            pct     = n_real / n_total if n_total else 0
            print(f"  Real odds matched : {n_real:,} / {n_total:,} games ({pct:.1%})")
            yr_cov = (
                feat.assign(_yr=pd.to_datetime(feat["date"]).dt.year)
                .groupby("_yr")["odds_source"]
                .apply(lambda s: (s == "real").mean())
            )
            for yr2, cov in yr_cov.items():
                print(f"    {yr2}: {cov:.0%} coverage")
        except Exception as e:
            print(f"  Historical odds merge FAILED: {e}")
    else:
        print("Skipping historical odds merge (--skip-historical-odds).")

    # ── Save feature matrix ──────────────────────────────────────────────────────────
    out_path = "data/processed/game_features.csv"
    print(f"Saving ({feat.shape[0]:,} x {feat.shape[1]}) to {out_path}...")
    try:
        feat.to_csv(out_path, index=False, encoding="utf-8")
        print(f"Saved to {out_path}")
    except PermissionError:
        import tempfile, os as _os
        tmp = _os.path.join(tempfile.gettempdir(), "game_features.csv")
        feat.to_csv(tmp, index=False, encoding="utf-8")
        print(f"  (Permission denied — saved to {tmp})")

    # ── 7. Train model ───────────────────────────────────────────────────────
    print("Training model...")
    artifacts = train(feat, test_seasons=args.test_seasons)
    print("\nDone.")
    return artifacts


if __name__ == "__main__":
    main()
