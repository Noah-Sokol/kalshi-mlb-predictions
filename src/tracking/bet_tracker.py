"""
Bet tracker — logs daily model predictions and tracks actual vs theoretical P&L.

Two P&L streams are maintained side by side:
  theoretical_pnl  — what you would have made if you took every model suggestion
  actual_pnl       — what you actually made (you fill this in via record_actual_bet)

Log file: data/bets_log.csv
One row per game per day the model had an opinion.
"""
from pathlib import Path
from datetime import date, datetime
from typing import Optional

import pandas as pd
import numpy as np

LOG_PATH = Path("data/bets_log.csv")

COLUMNS = [
    "date",
    "home_team_fg",
    "away_team_fg",
    "model_home_prob",
    "market_home_prob",
    "edge",
    "recommended_side",   # "home", "away", or "none"
    "kelly_pct",
    "suggested_dollars",  # flat $10 suggestion
    # Filled in later by fill_results()
    "home_win",
    "theoretical_pnl",    # P&L if you had taken the suggestion
    # Filled in manually via record_actual_bet()
    "actual_side",        # "home", "away", "none" (none = skipped)
    "actual_dollars",
    "actual_pnl",
    "notes",
]


def load_log() -> pd.DataFrame:
    if LOG_PATH.exists():
        df = pd.read_csv(LOG_PATH)
        # Ensure all columns exist (handles older log files)
        for col in COLUMNS:
            if col not in df.columns:
                df[col] = None
        return df[COLUMNS]
    return pd.DataFrame(columns=COLUMNS)


def _save_log(df: pd.DataFrame) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(LOG_PATH, index=False)


def log_predictions(
    predictions: list[dict],
    flat_bet: float = 5.0,
    overwrite_today: bool = True,
) -> None:
    """
    Append today's model predictions to the log.

    predictions: list of dicts with keys:
        date, home_team_fg, away_team_fg, model_home_prob, market_home_prob,
        edge, recommended_side, kelly_pct, bet_dollars
    """
    today_str = str(date.today())
    df = load_log()

    if overwrite_today:
        # Preserve any actual bets already recorded for today before wiping
        actual_cols = ["actual_side", "actual_dollars", "actual_pnl", "notes"]
        today_actuals = df[df["date"] == today_str][
            ["home_team_fg", "away_team_fg"] + actual_cols
        ].copy()
        df = df[df["date"] != today_str]
    else:
        today_actuals = None

    rows = []
    for p in predictions:
        rows.append({
            "date":             p.get("date", today_str),
            "home_team_fg":     p["home_team_fg"],
            "away_team_fg":     p["away_team_fg"],
            "model_home_prob":  round(float(p["model_home_prob"]), 4),
            "market_home_prob": round(float(p["market_home_prob"]), 4) if p.get("market_home_prob") is not None else None,
            "edge":             round(float(p.get("edge") or 0), 4),
            "recommended_side": p.get("recommended_side", "none"),
            "kelly_pct":        round(float(p.get("kelly_pct", 0)), 4),
            "suggested_dollars": round(float(p.get("bet_dollars") or flat_bet), 2) if p.get("recommended_side") not in (None, "none") else 0,
            "home_win":         None,
            "theoretical_pnl":  None,
            "actual_side":      None,
            "actual_dollars":   None,
            "actual_pnl":       None,
            "notes":            None,
        })

    new_rows = pd.DataFrame(rows, columns=COLUMNS)

    # Re-apply any actual bets that were recorded before this run
    if overwrite_today and today_actuals is not None and not today_actuals.empty:
        for _, act in today_actuals.iterrows():
            mask = (
                (new_rows["home_team_fg"] == act["home_team_fg"]) &
                (new_rows["away_team_fg"] == act["away_team_fg"])
            )
            if mask.any():
                idx = new_rows[mask].index[0]
                for col in actual_cols:
                    if pd.notna(act[col]):
                        new_rows.at[idx, col] = act[col]

    # Align dtypes before concat to avoid pandas NA-column dtype change warning
    for col in df.columns:
        if col in new_rows.columns and df[col].dtype != object:
            new_rows[col] = new_rows[col].astype(df[col].dtype, errors='ignore')
    combined = pd.concat([df, new_rows], ignore_index=True)
    _save_log(combined)
    print(f"  Logged {len(rows)} predictions ({sum(1 for r in rows if r['recommended_side'] != 'none')} bets suggested)")


def fill_results(games_with_outcomes: pd.DataFrame) -> int:
    """
    Fill home_win and theoretical_pnl for rows that have results now.
    games_with_outcomes: DataFrame with date, home_team_fg, away_team_fg, home_win columns
    Returns number of rows updated.
    """
    df = load_log()
    if df.empty:
        return 0

    outcomes = (
        games_with_outcomes[["date", "home_team_fg", "away_team_fg", "home_win"]]
        .dropna(subset=["home_win"])
        .copy()
    )
    outcomes["date"] = pd.to_datetime(outcomes["date"]).dt.date.astype(str)
    df["date"] = pd.to_datetime(df["date"]).dt.date.astype(str)

    updated = 0
    for idx, row in df.iterrows():
        if pd.notna(df.at[idx, "home_win"]):
            continue  # already filled
        match = outcomes[
            (outcomes["date"] == row["date"]) &
            (outcomes["home_team_fg"] == row["home_team_fg"]) &
            (outcomes["away_team_fg"] == row["away_team_fg"])
        ]
        if match.empty:
            continue

        hw = int(match.iloc[0]["home_win"])
        df.at[idx, "home_win"] = hw

        # Compute theoretical P&L
        side = row["recommended_side"]
        dollars = row["suggested_dollars"]
        mkt = row["market_home_prob"]
        if side in ("home", "YES", "away", "NO") and pd.notna(dollars) and pd.notna(mkt) and float(dollars) > 0:
            mkt = float(mkt)
            dollars = float(dollars)
            if side in ("home", "YES"):
                won = hw == 1
                win_odds = (1 - mkt) / mkt if mkt > 0 else 1.0
            else:
                won = hw == 0
                win_odds = mkt / (1 - mkt) if mkt < 1 else 1.0
            df.at[idx, "theoretical_pnl"] = round(dollars * win_odds if won else -dollars, 2)
        else:
            df.at[idx, "theoretical_pnl"] = 0.0

        # Compute actual P&L if bet was recorded
        actual_side = df.at[idx, "actual_side"]
        actual_dollars = df.at[idx, "actual_dollars"]
        if pd.notna(actual_side) and actual_side not in (None, "none", "") and pd.notna(actual_dollars) and pd.notna(mkt):
            actual_dollars = float(actual_dollars)
            mkt = float(mkt)
            if actual_side in ("home", "YES"):
                won = hw == 1
                win_odds = (1 - mkt) / mkt if mkt > 0 else 1.0
            else:
                won = hw == 0
                win_odds = mkt / (1 - mkt) if mkt < 1 else 1.0
            df.at[idx, "actual_pnl"] = round(actual_dollars * win_odds if won else -actual_dollars, 2)

        updated += 1

    _save_log(df)
    return updated


def record_actual_bet(
    home_team_fg: str,
    away_team_fg: str,
    side: str,
    dollars: float,
    bet_date: str = None,
    notes: str = None,
) -> bool:
    """
    Record that you actually placed a bet.

    side: "home" or "away"
    dollars: dollar amount wagered
    """
    bet_date = bet_date or str(date.today())
    df = load_log()
    mask = (
        (df["date"] == bet_date) &
        (df["home_team_fg"] == home_team_fg.upper()) &
        (df["away_team_fg"] == away_team_fg.upper())
    )
    if not mask.any():
        print(f"No prediction found for {away_team_fg} @ {home_team_fg} on {bet_date}")
        return False
    idx = df[mask].index[0]
    df.at[idx, "actual_side"]    = side
    df.at[idx, "actual_dollars"] = dollars
    if notes:
        df.at[idx, "notes"] = notes
    _save_log(df)
    print(f"  Recorded: ${dollars} on {side} for {away_team_fg} @ {home_team_fg}")
    return True


def print_summary(n_days: int = 30) -> None:
    """Print a summary of recent P&L — theoretical vs actual."""
    df = load_log()
    if df.empty:
        print("No bets logged yet.")
        return

    df["date"] = pd.to_datetime(df["date"])
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=n_days)
    tracking_start = pd.Timestamp("2026-08-06")
    recent = df[(df["date"] >= cutoff) & (df["date"] >= tracking_start)].copy()

    print(f"\n{'='*65}")
    print(f"  BET TRACKER — last {n_days} days")
    print(f"{'='*65}")

    settled = recent.dropna(subset=["home_win"])
    suggested = settled[settled["recommended_side"].isin(["home", "away", "YES", "NO"])]
    print(f"  Games with results : {len(settled):,}")
    print(f"  Suggestions made   : {len(suggested):,}")

    if not suggested.empty:
        theo_pnl = suggested["theoretical_pnl"].fillna(0).sum()
        theo_wagered = suggested["suggested_dollars"].fillna(0).sum()
        theo_roi = theo_pnl / theo_wagered if theo_wagered > 0 else 0
        theo_wins = (suggested["theoretical_pnl"].fillna(0) > 0).sum()
        print(f"\n  -- THEORETICAL (took every suggestion) --")
        print(f"  Bets     : {len(suggested):,}  |  Win rate: {theo_wins/len(suggested):.1%}")
        print(f"  P&L      : ${theo_pnl:+.2f}  |  ROI: {theo_roi:+.1%}")
        print(f"  Wagered  : ${theo_wagered:.2f}")

    actual = settled[settled["actual_side"].isin(["home", "away", "YES", "NO"])]
    if not actual.empty:
        act_pnl = actual["actual_pnl"].fillna(0).sum()
        act_wagered = actual["actual_dollars"].fillna(0).sum()
        act_roi = act_pnl / act_wagered if act_wagered > 0 else 0
        act_wins = (actual["actual_pnl"].fillna(0) > 0).sum()
        print(f"\n  -- ACTUAL (bets you placed) --")
        print(f"  Bets     : {len(actual):,}  |  Win rate: {act_wins/len(actual):.1%}")
        print(f"  P&L      : ${act_pnl:+.2f}  |  ROI: {act_roi:+.1%}")
        print(f"  Wagered  : ${act_wagered:.2f}")
    else:
        print(f"\n  -- ACTUAL -- (no bets recorded yet)")
        print("  Use: python record_bet.py NYY 25")

    # Recent daily breakdown — actual bets placed, sorted newest first
    if not actual.empty:
        daily = (
            actual.groupby(actual["date"].dt.date)
            .agg(pnl=("actual_pnl", "sum"), n=("actual_pnl", "count"))
            .sort_index(ascending=False)
            .head(14)
        )
        print()
        print("  Daily actual P&L (last 14 days with results):")
        for d, row in daily.iterrows():
            sign = "+" if row["pnl"] >= 0 else ""
            print(f"    {d}  {sign}${row['pnl']:.2f}  ({int(row['n'])} bets)")

    print()
