"""
Backtesting framework -- evaluates model edge against real and simulated markets.

Default mode uses real historical sportsbook closing lines (2021-2025) from
data/processed/historical_odds_clean.csv as the market price, falling back to
Pythagorean win expectancy for games with no odds data.

Recommended production backtest (2023-2024 test set):
    python backtest.py --min-edge 0.08
    => ~244 bets, 54.5%% win rate, +4.8x bankroll growth, ROI +10%%

Basic usage:
    python backtest.py                              # real historical odds + quality filters
    python backtest.py --min-edge 0.08          # recommended: blend model 50/50 with sportsbook
    python backtest.py --flat-bet 10               # flat bet (no compounding, honest signal check)
    python backtest.py --test-seasons 2            # specify holdout window

Quality-gate filters (ON by default):
    --min-bet-mkt-prob 0.40   Skip bets where market gives bet-team <40%% win prob.
                               Prevents betting against heavy market favorites.
    --min-team-win-pct 0.33   Skip bets on teams with season win%%<33%% OR
                               last-20-game win%%<28%%. Catches mid-season collapses.
    (disable with: --min-bet-mkt-prob 0 --min-team-win-pct 0)

Market options:
    --market-blend FLOAT   (1-blend)*model + blend*market. Corrects probability compression.
                           Recommendation: 0.5. Analysis optimal: ~0.8.
    --pythagorean-market   Pythagorean proxy + Gaussian noise (legacy baseline).
    --logistic-market      LR trained on all features -- hardest honest baseline.
    --use-saved-market     Daily-logged prices from data/market_prices.csv.
    --sim-spread 0.55      Flat implied home-win prob = 0.55 for every game.

Historical odds data:
    Source  : ArnavSaraogi/mlb-odds-scraper (SportsBookReview, free)
    Coverage: 2021-2025, ~11,400 games, avg 4.8 bookmakers per game
    Avg vig : ~4.3%%%% (removed before edge calculation)
    Rebuild : python src/data/historical_odds_parser.py
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

import os; os.chdir(Path(__file__).parent)

from src.models.win_probability import load, predict
from src.edge.kelly import fractional_kelly, bet_recommendation


def load_features(path: str = "data/processed/game_features.csv") -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        sys.exit(f"Feature matrix not found at {path}. Run python pipeline.py first.")
    feat = pd.read_csv(p, low_memory=False)
    if "suspicious" in feat.columns:
        feat["suspicious"] = feat["suspicious"].fillna(False).infer_objects(copy=False).astype(bool)
    if "home_win" not in feat.columns:
        sys.exit("Feature matrix missing 'home_win' column.")
    return feat


def temporal_split(feat: pd.DataFrame, test_seasons: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Same split logic as win_probability.py — train on older seasons, test on newer."""
    if "date" not in feat.columns:
        sys.exit("Feature matrix missing 'date' column.")
    feat = feat.copy()
    feat["_year"] = pd.to_datetime(feat["date"]).dt.year
    years = sorted(feat["_year"].unique())
    if len(years) <= test_seasons:
        sys.exit(f"Only {len(years)} seasons available; cannot hold out {test_seasons}.")
    cutoff = years[-test_seasons]
    train = feat[feat["_year"] < cutoff].drop(columns=["_year"])
    test  = feat[feat["_year"] >= cutoff].drop(columns=["_year"])
    return train, test


def calibration_bins(probs: np.ndarray, actuals: np.ndarray, n_bins: int = 10):
    """Returns a DataFrame of calibration bin statistics."""
    bins = np.linspace(0, 1, n_bins + 1)
    rows = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (probs >= lo) & (probs < hi)
        if mask.sum() == 0:
            continue
        rows.append({
            "bin_center": round((lo + hi) / 2, 2),
            "n_games": int(mask.sum()),
            "mean_pred": round(float(probs[mask].mean()), 3),
            "actual_rate": round(float(actuals[mask].mean()), 3),
            "error": round(float(probs[mask].mean() - actuals[mask].mean()), 3),
        })
    return pd.DataFrame(rows)


def simulate_pnl(
    test: pd.DataFrame,
    probs: np.ndarray,
    market_probs: np.ndarray,
    kelly_frac: float = 0.40,
    min_edge: float = 0.04,
    max_pct: float = 0.075,
    starting_bankroll: float = 1000.0,
    flat_bet: float = None,
    min_bet_mkt_prob: float = 0.40,
    min_team_win_pct: float = 0.33,
) -> pd.DataFrame:
    """
    Walk forward through test games, betting when model edge > min_edge.
    Tracks bankroll over time (using fractional Kelly sizing).

    Quality gate filters (applied after edge check):
      min_bet_mkt_prob : skip the bet when the market assigns the bet-team a win
                         probability below this threshold (default 0.40).
                         Prevents systematically fighting heavy market favorites.
      min_team_win_pct : skip the bet when the bet-team''s current-season rolling
                         win% is below this threshold (default 0.33).
                         Prevents over-betting clearly collapsing teams (CHW, OAK)
                         whose prior-season stats no longer reflect reality.
    Set either to 0.0 to disable the corresponding filter.

    Returns a row-per-game DataFrame with columns:
      date, home_team_fg, away_team_fg, model_prob, market_prob, edge,
      bet_side, kelly_pct, bet_dollars, outcome, pnl, bankroll
    """
    test = test.copy().reset_index(drop=True)
    test["model_prob"]  = probs
    test["market_prob"] = market_probs

    bankroll = starting_bankroll
    rows = []

    for _, row in test.sort_values("date").iterrows():
        mp  = float(row["model_prob"])
        mkt = float(row["market_prob"])
        actual = row.get("home_win")
        if pd.isna(actual):
            continue

        rec = bet_recommendation(mp, mkt, bankroll,
                                  min_edge=min_edge,
                                  kelly_frac=kelly_frac,
                                  max_pct_bankroll=max_pct)
        edge = mp - mkt
        pnl  = 0.0

        # ── Quality gate filters ──────────────────────────────────────────────────
        if rec["bet_side"] is not None:
            _home_bet  = rec["bet_side"] in ("YES", "home")
            _bet_mkt   = mkt if _home_bet else (1.0 - mkt)
            # Season-to-date win% AND last-20-game win% (catches mid-season collapses faster)
            _wpc_col   = "h_rolling_win_pct"       if _home_bet else "a_rolling_win_pct"
            _wpc20_col = "h_rolling_win_pct_last20" if _home_bet else "a_rolling_win_pct_last20"
            _wpc_val   = row.get(_wpc_col)
            _wpc20_val = row.get(_wpc20_col)
            # Market-side filter: bet-team market prob too low
            if min_bet_mkt_prob > 0 and _bet_mkt < min_bet_mkt_prob:
                rec = {"bet_side": None, "kelly_pct": 0, "bet_dollars": 0}
            # Season-to-date win% filter
            elif (min_team_win_pct > 0
                  and pd.notna(_wpc_val)
                  and float(_wpc_val) < min_team_win_pct):
                rec = {"bet_side": None, "kelly_pct": 0, "bet_dollars": 0}
            # Last-20-game win% filter (catches recent collapses; uses slightly lower threshold)
            elif (min_team_win_pct > 0
                  and pd.notna(_wpc20_val)
                  and float(_wpc20_val) < min_team_win_pct - 0.05):
                rec = {"bet_side": None, "kelly_pct": 0, "bet_dollars": 0}

        if rec["bet_side"] is not None:
            dollars = flat_bet if flat_bet is not None else rec["bet_dollars"]
            # kelly.py returns "YES"/"NO"; treat "YES"/home as betting home team wins
            if rec["bet_side"] in ("YES", "home"):
                won = float(actual) == 1.0
                win_odds = (1.0 - mkt) / mkt if mkt > 0 else 1.0
            else:  # "NO" / away
                won = float(actual) == 0.0
                edge = (1 - mp) - (1 - mkt)
                # Away contract costs (1-mkt), pays $1 → net odds = mkt/(1-mkt)
                win_odds = mkt / (1.0 - mkt) if mkt < 1 else 1.0

            if won:
                pnl = dollars * win_odds
            else:
                pnl = -dollars
            bankroll = max(bankroll + pnl, 0.01)

        rows.append({
            "date":          row.get("date"),
            "home_team_fg":  row.get("home_team_fg"),
            "away_team_fg":  row.get("away_team_fg"),
            "model_prob":    round(mp, 4),
            "market_prob":   round(mkt, 4),
            "edge":          round(edge, 4),
            "bet_side":      rec["bet_side"],
            "kelly_pct":     round(rec.get("kelly_pct", 0), 4),
            "bet_dollars":   round(rec.get("bet_dollars", 0), 2),
            "outcome_home_win": int(actual),
            "pnl":           round(pnl, 2),
            "bankroll":      round(bankroll, 2),
        })

    return pd.DataFrame(rows)


def print_report(test: pd.DataFrame, probs: np.ndarray, market_probs: np.ndarray,
                 pnl_df: pd.DataFrame, kelly_frac: float, market_mode: str = "historical",
                 min_bet_mkt_prob: float = 0.0, min_team_win_pct: float = 0.0):
    actuals = test["home_win"].values.astype(float)
    mask = ~np.isnan(actuals)
    probs_clean = probs[mask]
    actuals_clean = actuals[mask]

    brier  = brier_score_loss(actuals_clean, probs_clean)
    logloss = float(-np.mean(
        actuals_clean * np.log(np.clip(probs_clean, 1e-9, 1 - 1e-9))
        + (1 - actuals_clean) * np.log(np.clip(1 - probs_clean, 1e-9, 1 - 1e-9))
    ))
    naive_brier = brier_score_loss(actuals_clean, np.full_like(actuals_clean, 0.54))

    print(f"\n{'='*60}")
    print(f"  BACKTEST RESULTS  [{market_mode.upper()} MARKET]")
    print(f"{'='*60}")
    print(f"  Test games       : {len(probs_clean):,}")
    print(f"  Brier score      : {brier:.4f}  (naive 54%: {naive_brier:.4f})")
    print(f"  Log-loss         : {logloss:.4f}")
    acc = float(((probs_clean > 0.5) == actuals_clean.astype(bool)).mean())
    print(f"  Model accuracy   : {acc:.3f}")

    print(f"\n{'-'*60}")
    print("  CALIBRATION  (mean predicted vs actual win rate per decile)")
    cal = calibration_bins(probs_clean, actuals_clean)
    print(cal.to_string(index=False))

    # Real odds quality report
    if "odds_source" in test.columns:
        n_real_t = int((test["odds_source"] == "real").sum())
        n_pyth_t = int((test["odds_source"] != "real").sum())
        n_susp   = int(test["suspicious"].sum()) if "suspicious" in test.columns else 0
        avg_vig  = float(test["avg_vig"].dropna().mean()) if "avg_vig" in test.columns else None
        print(chr(10) + chr(45)*60)
        print(  "  MARKET ODDS QUALITY")
        print(f"  Real odds         : {n_real_t:,} games ({n_real_t/len(test):.1%})")
        print(f"  Pythagorean fill  : {n_pyth_t:,} games ({n_pyth_t/len(test):.1%})")
        if n_susp:
            print(f"  Suspicious (>3%)  : {n_susp:,} games flagged")
        if avg_vig is not None:
            print(f"  Avg market vig    : {avg_vig:.2%}")

    if not pnl_df.empty:
        bets = pnl_df[pnl_df["bet_side"].notna() & (pnl_df["bet_dollars"] > 0)]
        filters_on = []
        if min_bet_mkt_prob > 0:
            filters_on.append(f"mkt>={min_bet_mkt_prob:.0%}")
        if min_team_win_pct > 0:
            filters_on.append(f"win%>={min_team_win_pct:.0%}")
        filter_str = ("  filters: " + ", ".join(filters_on)) if filters_on else "  no quality-gate filters"
        print(chr(10) + "-"*60)
        print(f"  P&L SIMULATION  (fractional Kelly = {kelly_frac:.0%})")
        print(f"  {filter_str}")
        print(f"  Total bets       : {len(bets):,} / {len(pnl_df):,} games")
        if not bets.empty:
            total_pnl = pnl_df["pnl"].sum()
            # Correct: YES/home bets win when home_win==1; NO/away bets win when home_win==0
            def _bet_won(r):
                if r["bet_side"] in ("YES", "home"): return int(r["outcome_home_win"]) == 1
                return int(r["outcome_home_win"]) == 0
            win_rate  = float(bets.apply(_bet_won, axis=1).mean())
            roi = total_pnl / bets["bet_dollars"].sum() if bets["bet_dollars"].sum() > 0 else 0
            final_br  = pnl_df["bankroll"].iloc[-1]
            print(f"  Bet win rate     : {win_rate:.3f}")
            print(f"  Total P&L        : ${total_pnl:+.2f}")
            print(f"  ROI on wagered   : {roi:+.3f}")
            print(f"  Final bankroll   : ${final_br:.2f}  (started: ${starting_bankroll:.0f})")

            # Monthly breakdown
            if "date" in pnl_df.columns:
                pnl_df["month"] = pd.to_datetime(pnl_df["date"]).dt.to_period("M")
                monthly = (
                    pnl_df.groupby("month")["pnl"]
                    .sum()
                    .reset_index()
                    .rename(columns={"pnl": "monthly_pnl"})
                )
                monthly["monthly_pnl"] = monthly["monthly_pnl"].round(2)
                print(f"\n  Monthly P&L:")
                for _, mrow in monthly.iterrows():
                    sign = "+" if mrow["monthly_pnl"] >= 0 else ""
                    print(f"    {mrow['month']}  {sign}${mrow['monthly_pnl']:.2f}")

    print()


def _pythagorean_market(test: pd.DataFrame, sim_vig: float) -> np.ndarray:
    """
    Simple market proxy: rolling Pythagorean win expectancy + Gaussian noise + vig.
    Does NOT use actual outcomes. Useful as a lower-bound on market sophistication.
    """
    np.random.seed(42)
    HOME_ADVANTAGE = 0.54
    pyth_home = pd.to_numeric(
        test.get("h_rolling_pythag", pd.Series(np.full(len(test), HOME_ADVANTAGE))),
        errors="coerce"
    ).fillna(HOME_ADVANTAGE).values
    pyth_away = pd.to_numeric(
        test.get("a_rolling_pythag", pd.Series(np.full(len(test), HOME_ADVANTAGE))),
        errors="coerce"
    ).fillna(HOME_ADVANTAGE).values
    matchup = pyth_home / np.where(pyth_home + pyth_away > 0, pyth_home + pyth_away, 1.0)
    blended = 0.6 * matchup + 0.4 * HOME_ADVANTAGE
    noise = np.random.normal(0, 0.04, len(blended))
    market_probs = np.clip(blended + noise + sim_vig / 2, 0.42, 0.70)
    print(f"Simulating Pythagorean market with vig={sim_vig:.0%} "
          f"(mean market prob: {market_probs.mean():.3f})")
    return market_probs


def _logistic_market(
    train: pd.DataFrame,
    test: pd.DataFrame,
    artifacts: dict,
    sim_vig: float,
) -> np.ndarray:
    """
    Logistic regression trained on the same features as the XGBoost model.
    Simulates an 'informed basic market' that uses all available pre-game data
    but with a weaker model — a harder and more honest bar than Pythagorean-only.
    """
    from src.features.game_features import FEATURE_COLS
    np.random.seed(42)

    available = [c for c in FEATURE_COLS if c in train.columns and c in test.columns]
    X_train = train[available].fillna(train[available].median())
    y_train = train["home_win"].astype(float)
    X_test  = test[available].fillna(train[available].median())

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s  = scaler.transform(X_test)

    lr = LogisticRegression(C=0.1, max_iter=1000, random_state=42)
    lr.fit(X_train_s, y_train)
    base_probs = lr.predict_proba(X_test_s)[:, 1]

    # Add vig overlay + small noise to simulate market spread
    noise = np.random.normal(0, 0.02, len(base_probs))
    market_probs = np.clip(base_probs + noise + sim_vig / 2, 0.42, 0.70)
    print(f"Logistic-regression market with vig={sim_vig:.0%} "
          f"(mean market prob: {market_probs.mean():.3f}, "
          f"using {len(available)}/{len(FEATURE_COLS)} features)")
    return market_probs



def _historical_odds_market(test: pd.DataFrame, sim_vig: float, line_type: str = "closing") -> np.ndarray:
    """
    Use real SBR sportsbook lines as the market price.
    line_type="closing" = closing line (default, most efficient market price).
    line_type="opening" = opening line (softer, approximates ~10am Kalshi price).
    Priority: (1) closing already merged in feature matrix, else (2) on-the-fly merge.
    """
    hist_path = Path("data/processed/historical_odds_clean.csv")

    # Path 1: closing line already in feature matrix (skip for opening line mode)
    if line_type == "closing" and "market_home_prob" in test.columns:
        mprobs  = test["market_home_prob"].values.astype(float)
        src_col = test["odds_source"] if "odds_source" in test.columns else pd.Series(["unknown"] * len(test))
        n_real  = int((src_col == "real").sum())
        n_total = len(test)
        n_miss  = int(pd.isna(mprobs).sum())
        print(f"Historical odds (feature matrix): {n_real:,}/{n_total:,} games "
              f"({n_real/n_total:.1%}) real; {n_miss:,} missing -> Pythagorean")
        if n_miss > 0:
            fill   = _pythagorean_market(test, sim_vig)
            mprobs = np.where(pd.isna(mprobs), fill, mprobs)
        return mprobs.astype(float)

    # Path 2: on-the-fly merge from CSV
    if not hist_path.exists():
        print("  historical_odds_clean.csv not found; run pipeline.py first. "
              "Falling back to Pythagorean.")
        return _pythagorean_market(test, sim_vig)

    from src.data.historical_odds_parser import load_historical_odds
    odds_df = load_historical_odds()
    t2 = test.copy().reset_index(drop=True)
    t2["_ds"]      = pd.to_datetime(t2["date"]).dt.strftime("%Y-%m-%d")
    odds_df["_ds"] = pd.to_datetime(odds_df["date"]).dt.strftime("%Y-%m-%d")
    prob_col = "market_home_prob" if line_type == "closing" else "open_home_prob"
    merge_cols = ["_ds", "home_team_fg", "away_team_fg", prob_col]  # no odds_source to avoid suffix conflict
    if "open_avg_vig" in odds_df.columns and line_type == "opening":
        merge_cols.append("open_avg_vig")
    merged = t2.merge(
        odds_df[[c for c in merge_cols if c in odds_df.columns]],
        on=["_ds", "home_team_fg", "away_team_fg"],
        how="left",
    )
    mprobs  = merged[prob_col].values.astype(float) if prob_col in merged.columns else merged["market_home_prob"].values.astype(float)
    n_real  = int((~pd.isna(mprobs)).sum())  # matched rows = real odds
    n_total = len(merged)
    n_miss  = int(pd.isna(mprobs).sum())
    print(f"Historical odds (on-the-fly, {line_type} line): {n_real:,}/{n_total:,} games "
          f"({n_real/n_total:.1%}) real; {n_miss:,} missing -> Pythagorean")
    if n_miss > 0:
        fill   = _pythagorean_market(test, sim_vig)
        mprobs = np.where(pd.isna(mprobs), fill, mprobs)
    return mprobs.astype(float)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feature-file",  default="data/processed/game_features.csv")
    ap.add_argument("--test-seasons",  type=int,   default=2)
    ap.add_argument("--kelly-frac",    type=float, default=0.40)
    ap.add_argument("--min-edge",      type=float, default=0.08)
    ap.add_argument("--sim-spread",    type=float, default=None,
                    help="Flat implied home-win prob for all games (e.g. 0.55)")
    ap.add_argument("--sim-vig",       type=float, default=0.05,
                    help="Market vig to add to true probability (default 0.05)")
    ap.add_argument("--use-saved-market", action="store_true",
                    help="Use market_home_prob column from feature CSV if present")
    ap.add_argument("--logistic-market", action="store_true",
                    help="Simulate market as logistic regression on same features (harder baseline)")
    ap.add_argument("--pythagorean-market", action="store_true",
                    help="Use Pythagorean win expectancy + noise (old default backtest mode)")
    ap.add_argument("--opening-line", action="store_true",
                    help="Use sportsbook OPENING lines instead of closing lines (~10am proxy)")
    ap.add_argument("--flat-bet",       type=float, default=None,
                    help="Fixed dollar amount per bet instead of Kelly sizing (e.g. 10)")
    ap.add_argument("--market-blend",    type=float, default=0.0,
                    help="Blend model with market: final_prob = (1-blend)*model + blend*market. "
                         "0=model only, 1=market only. Analysis suggests 0.5-0.8 is optimal.")
    ap.add_argument("--min-bet-mkt-prob", type=float, default=0.40,
                    help="Skip bets where bet-team market prob < this (0=off, default 0.40)")
    ap.add_argument("--min-team-win-pct",  type=float, default=0.33,
                    help="Skip bets on teams with current-season win%% < this (0=off, default 0.33)")
    ap.add_argument("--starting-bankroll", type=float, default=1000.0,
                    help="Starting bankroll in dollars (default: 1000)")
    ap.add_argument("--save-results",  default=None,
                    help="Optional path to save P&L DataFrame as CSV")
    args = ap.parse_args()

    feat = load_features(args.feature_file)

    # ── Merge real logged market prices if available ─────────────────────────
    market_log = Path("data/market_prices.csv")
    if market_log.exists() and "market_home_prob" not in feat.columns:
        try:
            prices = pd.read_csv(market_log)
            # Deduplicate: keep the pre-game price (closest to 0.5) per game.
            # In-game prices inflate toward 1.0 for the leading team and corrupt the backtest.
            prices["_prob_dist"] = (prices["market_home_prob"] - 0.5).abs()
            prices = (
                prices.sort_values("_prob_dist")
                .drop_duplicates(subset=["date", "home_team_fg", "away_team_fg"], keep="first")
                .drop(columns=["_prob_dist"])
            )
            prices["_date"] = pd.to_datetime(prices["date"]).dt.date.astype(str)
            feat["_date"] = pd.to_datetime(feat["date"]).dt.date.astype(str)
            feat = feat.merge(
                prices[["_date", "home_team_fg", "away_team_fg", "market_home_prob"]],
                on=["_date", "home_team_fg", "away_team_fg"],
                how="left",
            ).drop(columns=["_date"])
            n_matched = feat["market_home_prob"].notna().sum()
            if n_matched > 0:
                print(f"Merged {n_matched:,} real market prices from {market_log}")
        except Exception as e:
            print(f"  market_prices.csv merge failed: {e}")

    train, test = temporal_split(feat, args.test_seasons)

    print(f"Loading model artifacts...")
    try:
        artifacts = load()
    except FileNotFoundError:
        sys.exit("Model not found. Run python pipeline.py first.")

    print(f"Scoring {len(test):,} holdout games...")
    probs = predict(test, artifacts)

    # ── Construct market probabilities for simulation ───────────────────────────────────────────────────────────────
    if args.logistic_market:
        market_probs = _logistic_market(train, test, artifacts, args.sim_vig)
    elif args.sim_spread is not None:
        market_probs = np.full(len(test), args.sim_spread)
        print(f"Simulating flat market: home win prob = {args.sim_spread:.3f}")
    elif args.use_saved_market:
        if "market_home_prob" in test.columns:
            market_probs = test["market_home_prob"].values.astype(float)
            n_ok = int((~pd.isna(market_probs)).sum())
            print(f"Saved market prices: {n_ok:,} games with prices.")
            if pd.isna(market_probs).any():
                fill = _pythagorean_market(test, args.sim_vig)
                market_probs = np.where(pd.isna(market_probs), fill, market_probs)
        else:
            print("  --use-saved-market: market_home_prob not found; using historical odds.")
            market_probs = _historical_odds_market(test, args.sim_vig)
    elif args.pythagorean_market:
        market_probs = _pythagorean_market(test, args.sim_vig)
    else:
        # Default: real historical lines (closing or opening based on --opening-line)
        _line = "opening" if args.opening_line else "closing"
        market_probs = _historical_odds_market(test, args.sim_vig, line_type=_line)
    # -- Market blend: mix model prob toward market to reduce compression.
    # Use --market-blend 0.0 (default) for pure model; 0.8 closely tracks market.
    if args.market_blend > 0:
        b = float(args.market_blend)
        probs = (1.0 - b) * probs + b * market_probs
        print(f"Market blend applied: {1-b:.0%} model + {b:.0%} market")

    pnl_df = simulate_pnl(
        test, probs, market_probs,
        kelly_frac=args.kelly_frac,
        min_edge=args.min_edge,
        flat_bet=args.flat_bet,
        starting_bankroll=args.starting_bankroll,
        min_bet_mkt_prob=args.min_bet_mkt_prob,
        min_team_win_pct=args.min_team_win_pct,
    )

    _mmode = ("logistic" if args.logistic_market
              else "pythagorean" if args.pythagorean_market
              else "saved" if args.use_saved_market
              else "opening line" if args.opening_line
              else "historical (closing)")
    print_report(test, probs, market_probs, pnl_df, args.kelly_frac, market_mode=_mmode,
                 min_bet_mkt_prob=args.min_bet_mkt_prob,
                 min_team_win_pct=args.min_team_win_pct)

    if args.save_results and not pnl_df.empty:
        pnl_df.to_csv(args.save_results, index=False)
        print(f"P&L results saved to {args.save_results}")


if __name__ == "__main__":
    main()
