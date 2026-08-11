"""
Quick verification that backtest calculations are correct.
Checks a sample of bets to ensure P&L math is accurate.
"""
import pandas as pd
import numpy as np
from pathlib import Path

# Load backtest results if they exist
results_path = Path("data/backtest_verification.csv")

# Run a quick backtest and save detailed results
import subprocess
import sys

print("Running backtest with detailed output...")
result = subprocess.run(
    [sys.executable, "backtest.py", "--test-seasons", "1", "--save-results", str(results_path)],
    capture_output=True,
    text=True
)

if result.returncode != 0:
    print("Backtest failed:")
    print(result.stderr)
    sys.exit(1)

# Load and analyze results
df = pd.read_csv(results_path)
bets = df[df["bet_side"].notna() & (df["bet_dollars"] > 0)].copy()

print(f"\n{'='*70}")
print("BACKTEST VERIFICATION REPORT")
print(f"{'='*70}\n")

print(f"Total games tested: {len(df):,}")
print(f"Total bets placed: {len(bets):,} ({len(bets)/len(df):.1%})")
print(f"\nBets breakdown:")
print(f"  Home bets (YES): {(bets['bet_side'].isin(['YES', 'home'])).sum():,}")
print(f"  Away bets (NO):  {(bets['bet_side'].isin(['NO', 'away'])).sum():,}")

# Verify win rate calculation
def bet_won(row):
    if row["bet_side"] in ("YES", "home"):
        return int(row["outcome_home_win"]) == 1
    else:
        return int(row["outcome_home_win"]) == 0

bets["won"] = bets.apply(bet_won, axis=1)
actual_win_rate = bets["won"].mean()
print(f"\nActual bet win rate: {actual_win_rate:.3f}")

# Check P&L calculation for a few sample bets
print(f"\n{'='*70}")
print("SAMPLE BET VERIFICATION (first 10 bets)")
print(f"{'='*70}\n")

for idx, (_, row) in enumerate(bets.head(10).iterrows()):
    model_p = row["model_prob"]
    market_p = row["market_prob"]
    edge = row["edge"]
    bet_side = row["bet_side"]
    bet_amt = row["bet_dollars"]
    outcome = row["outcome_home_win"]
    pnl = row["pnl"]

    # Manually calculate expected P&L
    if bet_side in ("YES", "home"):
        win_odds = (1.0 - market_p) / market_p if market_p > 0 else 1.0
        won = outcome == 1
    else:
        win_odds = market_p / (1.0 - market_p) if market_p < 1 else 1.0
        won = outcome == 0

    expected_pnl = bet_amt * win_odds if won else -bet_amt

    match = "OK" if abs(expected_pnl - pnl) < 0.01 else "MISMATCH"

    print(f"Bet {idx+1}: {row['date']} {row['home_team_fg']} vs {row['away_team_fg']}")
    print(f"  Model: {model_p:.3f}, Market: {market_p:.3f}, Edge: {edge:+.3f}")
    print(f"  Bet {bet_side} ${bet_amt:.2f} @ odds {win_odds:.3f}x")
    print(f"  Outcome: {'WON' if won else 'LOST'}")
    print(f"  P&L: ${pnl:.2f} (expected: ${expected_pnl:.2f}) {match}")
    print()

# Check edge vs outcome correlation
print(f"{'='*70}")
print("EDGE QUALITY ANALYSIS")
print(f"{'='*70}\n")

# Bin bets by edge size
bets["edge_bin"] = pd.cut(bets["edge"].abs(), bins=[0, 0.05, 0.10, 0.15, 1.0],
                           labels=["0-5%", "5-10%", "10-15%", "15%+"])

edge_analysis = bets.groupby("edge_bin").agg({
    "won": ["count", "mean"],
    "pnl": "sum",
    "bet_dollars": "sum"
}).round(3)

print("Win rate and ROI by edge size:")
print(edge_analysis)

# Check calibration: does higher model edge = higher win rate?
print(f"\n{'='*70}")
print("MODEL CALIBRATION CHECK")
print(f"{'='*70}\n")

bets["model_bin"] = pd.cut(bets["model_prob"], bins=[0, 0.4, 0.5, 0.6, 1.0],
                            labels=["<40%", "40-50%", "50-60%", "60%+"])

# For home bets
home_bets = bets[bets["bet_side"].isin(["YES", "home"])]
if len(home_bets) > 0:
    print("HOME BETS:")
    home_cal = home_bets.groupby("model_bin").agg({
        "model_prob": "mean",
        "won": ["count", "mean"]
    }).round(3)
    print(home_cal)

# For away bets
away_bets = bets[bets["bet_side"].isin(["NO", "away"])]
if len(away_bets) > 0:
    print("\nAWAY BETS:")
    away_cal = away_bets.groupby("model_bin").agg({
        "model_prob": "mean",
        "won": ["count", "mean"]
    }).round(3)
    print(away_cal)

# Final P&L check
print(f"\n{'='*70}")
print("P&L SUMMARY")
print(f"{'='*70}\n")

total_wagered = bets["bet_dollars"].sum()
total_pnl = bets["pnl"].sum()
roi = total_pnl / total_wagered if total_wagered > 0 else 0

print(f"Total wagered: ${total_wagered:,.2f}")
print(f"Total P&L: ${total_pnl:+,.2f}")
print(f"ROI: {roi:+.2%}")
print(f"Win rate: {actual_win_rate:.1%}")
print(f"Expected win rate at 0% vig: ~{bets['model_prob'].mean():.1%}")

# Check for suspicious patterns
print(f"\n{'='*70}")
print("QUALITY FLAGS")
print(f"{'='*70}\n")

# Check if we're losing more on high-edge bets (would indicate a bug)
high_edge = bets[bets["edge"].abs() > 0.10]
if len(high_edge) > 0:
    high_edge_roi = high_edge["pnl"].sum() / high_edge["bet_dollars"].sum()
    print(f"High-edge bets (>10%): {len(high_edge)} bets, ROI = {high_edge_roi:+.2%}")
    if high_edge_roi < -0.20:
        print("  WARNING: Losing heavily on high-edge bets - check P&L calculation")

# Check market prob distribution
print(f"\nMarket prob range: {bets['market_prob'].min():.3f} to {bets['market_prob'].max():.3f}")
print(f"Market prob mean: {bets['market_prob'].mean():.3f}")
if bets["market_prob"].mean() > 0.60 or bets["market_prob"].mean() < 0.45:
    print("  WARNING: Market prob distribution looks unusual")

print("\nVerification complete")
