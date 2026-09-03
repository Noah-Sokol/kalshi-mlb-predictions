"""
ONE-COMMAND UPDATE - Does everything you need.

This script:
  1. Fills in yesterday's game results and calculates P&L
  2. Fetches today's schedule and live odds (cached by default, no API charge)
  3. Runs the model on today's games
  4. Logs predictions to bets_log.csv
  5. Exports Excel tracker (both Filtered and All Bets tabs)
  6. Shows performance summary
  7. Displays today's filtered picks
  8. Regenerates README with latest stats

Usage:
    python update.py                              # Normal update (FREE - uses cached odds)
    python update.py --fresh-odds                 # Force fresh odds (costs 1 API call)
    python update.py --bankroll 120                # Update your current bankroll
    python update.py --fresh-odds --bankroll 120   # Both options together
    python update.py --results-only                # Just fill results + regenerate Excel (no API calls)

Options:
    --fresh-odds        Fetch live odds right now (costs 1 API call, default: use cache if < 4hrs old)
    --bankroll X        Your current bankroll in dollars (default: uses last session's value)
    --market-blend X    Blend model with market: (1-X)*model + X*market (default: 0.0 = no blend)
    --results-only      Just fill results + regenerate Excel (no odds/picks, FREE)
    --min-edge X        Minimum edge threshold (default: 0.08 = 8%)
    --kelly-frac X      Kelly fraction for bet sizing (default: 0.40 = 40%)
    --min-bet-mkt-prob X  Skip bets where bet-team market prob < X (default: 0.40)
    --min-team-win-pct X  Skip bets on teams with season win%% < X (default: 0.33)

The defaults above aren't guesses — they're backtested. See reports/ for the analysis:
    reports/market_blend_analysis.md            -- why market_blend=0.0 + 8% edge beats blending
                                                    (blending halves Kelly bet sizing and roughly
                                                    halves total backtest profit for the same bet
                                                    count and win rate)
    reports/edge_return_correlation_analysis.md -- statistical validation that edge predicts real
                                                    returns, plus the market-prob quality-gate zone
                                                    analysis (the 38-40% zone showed -28.6% ROI)

Run manually:
    python update.py

Scheduled (set up once via setup_scheduler.py):
    Runs automatically at 9:00 AM every day via Windows Task Scheduler
"""
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

os.chdir(Path(__file__).parent)

from dotenv import load_dotenv
load_dotenv()

# ── Logging setup ────────────────────────────────────────────────────────────
_LOG_DIR = Path("data/logs")
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_log_file = _LOG_DIR / f"update_{date.today()}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# Suppress noisy library loggers
for _lib in ("urllib3", "requests", "charset_normalizer"):
    logging.getLogger(_lib).setLevel(logging.WARNING)

from src.data.mlb_api import fetch_today_schedule, fetch_season_games
from src.data.fangraphs import fetch_team_batting, fetch_team_pitching, fetch_pitcher_stats, fetch_batter_stats
from src.data.mlb_boxscores import fetch_season_pitching_lines
from src.data.statcast import fetch_team_batting_sc, fetch_team_pitching_sc
from src.data.odds_api import OddsAPIClient
from src.features.bullpen import aggregate_team_bp_quality, compute_rolling_bp_load, compute_rolling_starter_stats
from src.features.game_features import build_game_features, FEATURE_COLS
from src.models.win_probability import load, predict
from src.edge.kelly import bet_recommendation
from src.tracking.bet_tracker import log_predictions, fill_results, print_summary


def fill_yesterdays_results() -> None:
    """Pull yesterday's completed games from MLB API and update the bets log."""
    yesterday = date.today() - timedelta(days=1)
    print(f"Checking results for {yesterday}...")
    try:
        current_year = yesterday.year
        season_games = fetch_season_games(current_year)
        if season_games.empty:
            print("  No game data available.")
            return
        yesterday_games = season_games[
            (pd.to_datetime(season_games["date"]).dt.date == yesterday) &
            (season_games["home_win"].notna())
        ]
        if yesterday_games.empty:
            print("  No completed games found for yesterday (off day or results not yet posted).")
            return
        n = fill_results(yesterday_games)
        print(f"  Updated {n} rows with results.")
    except Exception as e:
        print(f"  Could not fetch yesterday's results: {e}")


def build_features_for_today(today_games: pd.DataFrame, current_year: int) -> pd.DataFrame:
    """Build feature matrix including today's unplayed games."""
    prev_year = current_year - 1
    print(f"Loading {prev_year} season stats...")

    bat_prev = sp_prev = rp_prev = pit_prev = bat_indiv_prev = pd.DataFrame()
    for fn, kwargs, name in [
        (fetch_team_batting,   {"year": prev_year},               "batting"),
        (fetch_team_pitching,  {"year": prev_year, "role": "sp"}, "SP"),
        (fetch_team_pitching,  {"year": prev_year, "role": "rp"}, "RP"),
        (fetch_pitcher_stats,  {"year": prev_year},               "pitchers"),
        (fetch_batter_stats,   {"year": prev_year},               "batters"),
    ]:
        try:
            r = fn(**kwargs)
            if name == "batting":  bat_prev       = r
            elif name == "SP":     sp_prev        = r
            elif name == "RP":     rp_prev        = r
            elif name == "pitchers": pit_prev     = r
            else:                  bat_indiv_prev = r
        except Exception as e:
            print(f"  {prev_year} {name}: FAILED ({e})")

    bpq_prev = aggregate_team_bp_quality(pit_prev) if not pit_prev.empty else None

    print(f"Fetching {current_year} season-to-date games for rolling stats...")
    try:
        season_games = fetch_season_games(current_year)
        season_games = season_games[season_games["date"] < pd.Timestamp.now().normalize()]
    except Exception as e:
        print(f"  Season games: FAILED ({e})")
        season_games = pd.DataFrame(columns=["game_id", "date", "home_team_fg", "away_team_fg",
                                              "home_score", "away_score", "home_win", "home_sp", "away_sp"])

    bp_load = sp_rolling = None
    if not season_games.empty:
        try:
            # 45 days covers ~9 starts per pitcher — enough for sp_ip_avg_10g (10-start window)
            cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(days=45)
            recent = season_games[season_games["date"] >= cutoff]
            if not recent.empty:
                lines = fetch_season_pitching_lines(recent, max_workers=4)
                bp_load   = compute_rolling_bp_load(lines)
                sp_rolling = compute_rolling_starter_stats(lines)
        except Exception as e:
            print(f"  Bullpen/starter rolling: FAILED ({e})")

    sc_bat = sc_pit = None
    try:
        sc_bat = fetch_team_batting_sc(
            prev_year,
            batters_fg=bat_indiv_prev if not bat_indiv_prev.empty else None,
        )
    except Exception as e:
        print(f"  Statcast bat: FAILED ({e})")
    try:
        sc_pit = fetch_team_pitching_sc(
            prev_year,
            pitchers_fg=pit_prev if not pit_prev.empty else None,
        )
    except Exception as e:
        print(f"  Statcast pit: FAILED ({e})")

    placeholder = today_games.copy()
    placeholder["home_score"] = None
    placeholder["away_score"] = None
    placeholder["home_win"]   = None
    placeholder["game_id"]    = pd.to_numeric(placeholder["game_id"], errors="coerce")

    all_games = pd.concat([season_games, placeholder], ignore_index=True)
    # Normalize date to string so sort_values("date") doesn't mix Timestamp and str
    all_games["date"] = pd.to_datetime(all_games["date"]).dt.strftime("%Y-%m-%d")
    return build_game_features(
        all_games, bat_prev, sp_prev, rp_prev, pit_prev,
        bp_quality_prev=bpq_prev,
        bp_load=bp_load,
        sc_bat_prev=sc_bat,
        sc_pit_prev=sc_pit,
        sp_rolling_stats=sp_rolling,
    )


def score_and_log(today_games: pd.DataFrame, feat: pd.DataFrame,
                  market_probs: dict, artifacts: dict,
                  min_edge: float, kelly_frac: float,
                  bankroll: float = 121.0, market_blend: float = 0.0,
                  min_bet_mkt_prob: float = 0.40, min_team_win_pct: float = 0.33) -> None:
    """Score today's games, print picks, and log to bet tracker."""
    today_feat = feat[feat["home_win"].isna()].copy()
    if today_feat.empty:
        print("No unplayed games in feature matrix.")
        return

    probs = predict(today_feat, artifacts)
    today_feat = today_feat.copy()
    today_feat["model_home_prob_raw"] = probs.round(4)
    today_feat["model_home_prob"] = probs.round(4)

    # Apply market blend if we have market odds
    if market_blend > 0 and market_probs:
        today_feat["game_id_str"] = today_feat["game_id"].astype(str)
        today_games_temp = today_games.copy()
        today_games_temp["game_id"] = today_games_temp["game_id"].astype(str)

        # Map market probs by home team
        market_lookup = {}
        for _, row in today_games_temp.iterrows():
            home_fg = row.get("home_team_fg")
            if home_fg in market_probs:
                market_lookup[row["game_id"]] = market_probs[home_fg]

        # Blend model with market for games where we have market data
        blended_probs = []
        for idx, row in today_feat.iterrows():
            gid = str(row["game_id"])
            model_p = row["model_home_prob_raw"]
            market_p = market_lookup.get(gid)

            if market_p is not None:
                # Blend: (1-blend)*model + blend*market
                blended = (1.0 - market_blend) * model_p + market_blend * market_p
                blended_probs.append(blended)
            else:
                # No market data, use pure model
                blended_probs.append(model_p)

        today_feat["model_home_prob"] = pd.Series(blended_probs, index=today_feat.index).round(4)
        print(f"Market blend applied: {1-market_blend:.0%} model + {market_blend:.0%} sportsbook odds")

    # Align with schedule by game_id and include rolling win% for quality filters
    today_games = today_games.copy()
    today_games["game_id"] = today_games["game_id"].astype(str)
    today_feat["game_id"]  = today_feat["game_id"].astype(str)

    # Merge model probs + rolling win% columns for quality filters
    merge_cols = ["game_id", "model_home_prob"]
    if "h_rolling_win_pct" in today_feat.columns:
        merge_cols.append("h_rolling_win_pct")
    if "a_rolling_win_pct" in today_feat.columns:
        merge_cols.append("a_rolling_win_pct")
    if "h_rolling_win_pct_last20" in today_feat.columns:
        merge_cols.append("h_rolling_win_pct_last20")
    if "a_rolling_win_pct_last20" in today_feat.columns:
        merge_cols.append("a_rolling_win_pct_last20")

    today_merged = today_games.merge(
        today_feat[merge_cols],
        on="game_id", how="left",
    )
    today_merged["model_home_prob"] = today_merged["model_home_prob"].fillna(0.5)
    today_merged["model_away_prob"] = (1 - today_merged["model_home_prob"]).round(4)

    # Build prediction rows with edge computed
    predictions = []
    for _, row in today_merged.iterrows():
        home_fg = row.get("home_team_fg", "?")
        away_fg = row.get("away_team_fg", "?")
        mp      = float(row["model_home_prob"])
        mkt_val = market_probs.get(home_fg)
        mkt     = float(mkt_val) if mkt_val is not None else None

        rec  = {}
        edge = None
        if mkt is not None:
            rec  = bet_recommendation(mp, mkt, bankroll=bankroll,
                                      min_edge=min_edge, kelly_frac=kelly_frac,
                                      max_pct_bankroll=0.075)
            edge = mp - mkt

            # Apply quality gate filters (same thresholds used in backtest.py). These
            # aren't arbitrary cutoffs -- see reports/edge_return_correlation_analysis.md
            # for the market-prob zone analysis (38-40% zone showed -28.6% backtest ROI)
            # and reports/market_blend_analysis.md for the edge-threshold/Kelly tradeoff.
            if rec.get("bet_side") not in (None, "none"):
                _home_bet = rec["bet_side"] in ("YES", "home")
                _bet_mkt = mkt if _home_bet else (1.0 - mkt)

                # Get rolling win% for the team we're betting on
                _wpc_col = "h_rolling_win_pct" if _home_bet else "a_rolling_win_pct"
                _wpc20_col = "h_rolling_win_pct_last20" if _home_bet else "a_rolling_win_pct_last20"
                _wpc_val = row.get(_wpc_col)
                _wpc20_val = row.get(_wpc20_col)

                # Filter 1: Market-side filter (skip heavy underdogs)
                if min_bet_mkt_prob > 0 and _bet_mkt < min_bet_mkt_prob:
                    rec = {"bet_side": "none", "kelly_pct": 0, "bet_dollars": 0}
                # Filter 2: Season-to-date win% filter
                elif (min_team_win_pct > 0
                      and pd.notna(_wpc_val)
                      and float(_wpc_val) < min_team_win_pct):
                    rec = {"bet_side": "none", "kelly_pct": 0, "bet_dollars": 0}
                # Filter 3: Last-20-game win% filter (catches mid-season collapses)
                elif (min_team_win_pct > 0
                      and pd.notna(_wpc20_val)
                      and float(_wpc20_val) < min_team_win_pct - 0.05):
                    rec = {"bet_side": "none", "kelly_pct": 0, "bet_dollars": 0}

        predictions.append({
            "date":              str(date.today()),
            "home_team_fg":      home_fg,
            "away_team_fg":      away_fg,
            "home_sp":           row.get("home_sp", "?"),
            "away_sp":           row.get("away_sp", "?"),
            "model_home_prob":   mp,
            "market_home_prob":  mkt,
            "edge":              edge,
            "abs_edge":          abs(edge) if edge is not None else -1,
            "recommended_side":  rec.get("bet_side", "none") or "none",
            "kelly_pct":         rec.get("kelly_pct", 0.0),
            "bet_dollars":       rec.get("bet_dollars", 0.0),
        })

    # Sort: bets with market prices by absolute edge desc, no-price games last
    predictions.sort(key=lambda p: p["abs_edge"], reverse=True)

    bets      = [p for p in predictions if p["recommended_side"] not in ("none", None)]
    no_bet    = [p for p in predictions if p["recommended_side"] in ("none", None)]

    print(f"\n{'='*70}")
    print(f"  TODAY'S PICKS — {date.today()}")
    print(f"{'='*70}")

    if bets:
        print(f"\n  -- BETS ({len(bets)}) --")
        for p in bets:
            bet_team = p["home_team_fg"] if p["recommended_side"] == "YES" else p["away_team_fg"]
            print(
                f"\n  {p['away_team_fg']} @ {p['home_team_fg']}  "
                f"({p['away_sp']} vs {p['home_sp']})"
            )
            print(
                f"  Model: {p['model_home_prob']:.1%} home  "
                f"Market: {p['market_home_prob']:.1%}  "
                f"Edge: {p['edge']:+.1%}  "
                f">>> BET {bet_team} ${p['bet_dollars']:.2f}"
            )
    else:
        print("\n  No bets meet the edge threshold today.")

    print(f"\n  -- ALL GAMES --")
    for p in no_bet:
        mkt_str  = f"  Market: {p['market_home_prob']:.1%}" if p["market_home_prob"] is not None else "  Market: N/A"
        edge_str = f"  Edge: {p['edge']:+.1%}" if p["edge"] is not None else ""
        print(
            f"\n  {p['away_team_fg']} @ {p['home_team_fg']}  "
            f"({p['away_sp']} vs {p['home_sp']})"
        )
        print(f"  Model: {p['model_home_prob']:.1%} home{mkt_str}{edge_str}")

    print()
    log_predictions(
        [{k: v for k, v in p.items() if k not in ("abs_edge", "home_sp", "away_sp")}
         for p in predictions]
    )


def _export_excel(logger) -> None:
    """Build and save the Excel picks tracker. Falls back to a dated backup if the file is locked."""
    try:
        from export_excel import load_log as _load, write_picks_sheet, write_stats_panel, write_running_pnl, freeze_and_filter
        import openpyxl as _xl
        _df = _load()
        if _df.empty:
            return
        _wb = _xl.Workbook()

        # Sheet 1: Filtered picks
        _ws_filtered = _wb.active
        _bets_filtered = write_picks_sheet(_ws_filtered, _df, filtered=True)
        write_stats_panel(_ws_filtered, _df, start_col=17)
        write_running_pnl(_ws_filtered, _bets_filtered, start_col=20, actual_df=_df)
        freeze_and_filter(_ws_filtered)

        # Sheet 2: All bets
        _ws_all = _wb.create_sheet(title="All Bets")
        _bets_all = write_picks_sheet(_ws_all, _df, filtered=False)
        write_stats_panel(_ws_all, _df, start_col=17)
        write_running_pnl(_ws_all, _bets_all, start_col=20, actual_df=_df)
        freeze_and_filter(_ws_all)

        primary = Path("data/picks_tracker.xlsx")
        try:
            _wb.save(primary)
            logger.info(f"Excel tracker updated: {primary}")
        except PermissionError:
            logger.warning(
                "picks_tracker.xlsx is open in Excel — close it and run again to update."
            )
    except Exception as e:
        logger.warning(f"Excel export failed: {e}")


_BANKROLL_LOG = Path("data/bankroll_log.csv")
_BANKROLL_DEFAULT = 100.0


def _read_last_bankroll() -> float:
    if not _BANKROLL_LOG.exists():
        return _BANKROLL_DEFAULT
    df = pd.read_csv(_BANKROLL_LOG)
    if df.empty:
        return _BANKROLL_DEFAULT
    return float(df.iloc[-1]["bankroll"])


def _append_bankroll(amount: float) -> None:
    row = pd.DataFrame([{"date": date.today().isoformat(), "bankroll": amount}])
    if _BANKROLL_LOG.exists():
        row.to_csv(_BANKROLL_LOG, mode="a", header=False, index=False)
    else:
        row.to_csv(_BANKROLL_LOG, index=False)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-edge",    type=float, default=0.08,
                    help="Minimum edge to recommend a bet (default 0.08 = 8%%). Backtest-validated "
                         "-- see reports/market_blend_analysis.md and "
                         "reports/edge_return_correlation_analysis.md.")
    ap.add_argument("--kelly-frac",  type=float, default=0.40,
                    help="Fractional Kelly multiplier for bet sizing (default 0.40 = 40%%).")
    ap.add_argument("--bankroll",    type=float, default=None,
                    help="Your current bankroll in dollars (default: last logged value).")
    ap.add_argument("--market-blend", type=float, default=0.0,
                    help="Blend model with sportsbook: (1-b)*model + b*sportsbook. Default 0.0 "
                         "(no blend -- use 8%% edge threshold instead). Blending was tested and "
                         "rejected: it halves Kelly bet sizing and roughly halves total backtest "
                         "profit for the same bet count and win rate -- see "
                         "reports/market_blend_analysis.md.")
    ap.add_argument("--min-bet-mkt-prob", type=float, default=0.40,
                    help="Skip bets where bet-team market prob < this (0=off, default 0.40). The "
                         "38-40%% market-prob zone showed -28.6%% backtest ROI -- see "
                         "reports/edge_return_correlation_analysis.md.")
    ap.add_argument("--min-team-win-pct", type=float, default=0.33,
                    help="Skip bets on teams with current-season win%% < this (0=off, default 0.33)")
    ap.add_argument("--summary-days", type=int,  default=30)
    ap.add_argument("--fresh-odds",  action="store_true",
                    help="Bypass odds cache and fetch live prices now (costs 1 API call)")
    ap.add_argument("--results-only", action="store_true",
                    help="Skip odds/picks — just fill today's results and regenerate Excel (no API calls)")
    args = ap.parse_args()

    # Bankroll: log it when explicitly provided; otherwise use last logged value
    if args.bankroll is not None:
        _append_bankroll(args.bankroll)
        log.info(f"Bankroll updated to ${args.bankroll:.2f} — logged to {_BANKROLL_LOG}")
    else:
        args.bankroll = _read_last_bankroll()
        log.info(f"Bankroll: ${args.bankroll:.2f} (last logged value)")

    # ── Settings preview ─────────────────────────────────────────────────────
    print("="*70)
    print("  UPDATE SETTINGS")
    print("="*70)
    print(f"  Odds: {'FRESH (will use 1 API call from monthly quota)' if args.fresh_odds else 'CACHED (FREE — no API call, <4hrs old)'}")
    print(f"  Bankroll: ${args.bankroll:.2f}")
    if args.market_blend > 0:
        print(f"  Market blend: {100*(1-args.market_blend):.0f}% model + {100*args.market_blend:.0f}% sportsbook (from --market-blend {args.market_blend})")
    else:
        print("  Market blend: none — pure model signal (backtest-validated; see reports/market_blend_analysis.md)")
    print(f"  Min edge: {args.min_edge:.0%} (backtest-validated threshold; see reports/market_blend_analysis.md)")
    print("="*70)
    print()

    current_year = date.today().year
    log.info(f"=== Update started — {date.today()} ===")
    log.info(f"Log file: {_log_file}")

    # ── Results-only mode: fill results, show summary, regenerate Excel ──────
    if args.results_only:
        log.info("Results-only mode — skipping odds and picks.")
        fill_yesterdays_results()
        print_summary(args.summary_days)
        _export_excel(log)
        try:
            from generate_readme import generate as _gen_readme
            _gen_readme()
        except Exception as e:
            log.warning(f"  README generation failed: {e}")
        log.info("Done.")
        return

    # ── 1. Fill yesterday's results ─────────────────────────────────────────
    fill_yesterdays_results()

    # ── 2. Today's schedule ─────────────────────────────────────────────────
    log.info("Fetching today's schedule...")
    today_games = fetch_today_schedule()
    if today_games.empty:
        log.info("No games today.")
        print_summary(args.summary_days)
        return
    log.info(f"  {len(today_games)} games scheduled")

    # ── 3. Fetch live market odds ────────────────────────────────────────────
    market_probs = {}
    odds_client = OddsAPIClient()
    if odds_client.configured:
        cache_hours = 0.0 if args.fresh_odds else 4.0
        try:
            log.info("Fetching live odds from The Odds API...")
            market_probs, requests_remaining = odds_client.get_home_win_probs_with_quota(cache_hours=cache_hours)
            log.info(f"  Got prices for {len(market_probs)} teams")
            if requests_remaining >= 0:
                log.info(f"  *** Odds API quota: {requests_remaining} / 500 requests remaining this month ***")
            else:
                log.info(f"  *** Odds API quota: served from cache (no request used) ***")
        except Exception as e:
            log.warning(f"  Odds API failed: {e}")
        try:
            odds_client.log_daily_prices(cache_hours=cache_hours)
        except Exception as e:
            log.warning(f"  Could not save market prices: {e}")
    else:
        log.warning("  No ODDS_API_KEY — market prices unavailable. Add it to .env")

    # ── 4. Load model ────────────────────────────────────────────────────────
    try:
        artifacts = load()
    except FileNotFoundError:
        sys.exit("Model not found. Run python pipeline.py first.")

    # ── 5. Build features and score ──────────────────────────────────────────
    log.info("Building features and scoring today's games...")
    feat = build_features_for_today(today_games, current_year)

    score_and_log(today_games, feat, market_probs, artifacts,
                  min_edge=args.min_edge,
                  kelly_frac=args.kelly_frac,
                  bankroll=args.bankroll,
                  market_blend=args.market_blend,
                  min_bet_mkt_prob=args.min_bet_mkt_prob,
                  min_team_win_pct=args.min_team_win_pct)

    # ── 6. Performance summary ───────────────────────────────────────────────
    print_summary(args.summary_days)

    # ── 7. Regenerate Excel tracker ──────────────────────────────────────────
    _export_excel(log)

    # ── 8. Regenerate README with latest stats ────────────────────────────────
    try:
        from generate_readme import generate as _gen_readme
        _gen_readme()
    except Exception as e:
        log.warning(f"  README generation failed: {e}")

    log.info("Done.")
    log.info("  To record a bet: python record_bet.py TEAM DOLLARS")
    log.info(f"  Full log saved to: {_log_file}")


if __name__ == "__main__":
    main()
