"""
Show all edges for today's games with filter analysis.
Displays which bets pass quality filters and which don't.
"""
import pandas as pd
from pathlib import Path

# Load today's predictions from bets_log.csv
log = pd.read_csv("data/bets_log.csv")
today = pd.Timestamp.now().date().isoformat()
today_preds = log[log["date"] == today].copy()

if today_preds.empty:
    print("No predictions for today yet. Run: python daily_update.py")
    exit()

# Add filter analysis
MIN_BET_MKT_PROB = 0.40
MIN_TEAM_WIN_PCT = 0.33

def check_filters(row):
    """Check if bet passes quality filters."""
    if row["recommended_side"] in ("none", None) or pd.isna(row["recommended_side"]):
        return "NO BET", "Edge < 4% threshold"

    # Determine bet-team market probability
    if row["recommended_side"] in ("YES", "home"):
        bet_team_prob = row["market_home_prob"]
        bet_team = row["home_team_fg"]
    else:
        bet_team_prob = 1 - row["market_home_prob"]
        bet_team = row["away_team_fg"]

    # Check market prob filter
    if bet_team_prob < MIN_BET_MKT_PROB:
        return "FILTERED", f"Market gives {bet_team} only {bet_team_prob:.1%} (< 40%)"

    # If we get here, it passes
    return "PASSES", f"Market: {bet_team_prob:.1%} >= 40%"

# Apply filter check
today_preds["filter_status"], today_preds["filter_reason"] = zip(*today_preds.apply(check_filters, axis=1))

# Sort by absolute edge
today_preds["abs_edge"] = today_preds["edge"].abs()
today_preds = today_preds.sort_values("abs_edge", ascending=False)

print(f"\n{'='*80}")
print(f"  ALL EDGES FOR TODAY ({today})")
print(f"{'='*80}\n")

print(f"Quality Filters:")
print(f"  • Min Market Prob for Bet-Team: {MIN_BET_MKT_PROB:.0%}")
print(f"  • Min Edge Threshold: 4%\n")

# Group by filter status
passes = today_preds[today_preds["filter_status"] == "PASSES"]
filtered = today_preds[today_preds["filter_status"] == "FILTERED"]
no_bet = today_preds[today_preds["filter_status"] == "NO BET"]

print(f"{'-'*80}")
print(f"  [TAKE] BETS TO TAKE ({len(passes)} games) - Pass all filters")
print(f"{'-'*80}\n")

if not passes.empty:
    for idx, row in passes.iterrows():
        home = row["home_team_fg"]
        away = row["away_team_fg"]
        side = row["recommended_side"]
        bet_team = home if side in ("YES", "home") else away
        edge = row["edge"] if side in ("YES", "home") else -row["edge"]

        print(f"  {away} @ {home}")
        print(f"  >>> BET {bet_team} ${row['suggested_dollars']:.2f}")
        print(f"      Model: {row['model_home_prob']:.1%} home  |  Market: {row['market_home_prob']:.1%}")
        print(f"      Edge: {edge:+.1%}  |  {row['filter_reason']}")
        print()
else:
    print("  No bets pass filters today.\n")

print(f"{'-'*80}")
print(f"  [SKIP] FILTERED OUT ({len(filtered)} games) - Failed quality check")
print(f"{'-'*80}\n")

if not filtered.empty:
    for idx, row in filtered.iterrows():
        home = row["home_team_fg"]
        away = row["away_team_fg"]
        side = row["recommended_side"]
        bet_team = home if side in ("YES", "home") else away
        edge = row["edge"] if side in ("YES", "home") else -row["edge"]

        print(f"  {away} @ {home}")
        print(f"  Would bet {bet_team} ${row['suggested_dollars']:.2f}")
        print(f"      Model: {row['model_home_prob']:.1%} home  |  Market: {row['market_home_prob']:.1%}")
        print(f"      Edge: {edge:+.1%}")
        print(f"      [X] {row['filter_reason']}")
        print()
else:
    print("  No games were filtered.\n")

print(f"{'-'*80}")
print(f"  [INFO] NO BET RECOMMENDED ({len(no_bet)} games) - Edge too small")
print(f"{'-'*80}\n")

if not no_bet.empty:
    for idx, row in no_bet.iterrows():
        home = row["home_team_fg"]
        away = row["away_team_fg"]

        print(f"  {away} @ {home}")
        print(f"      Model: {row['model_home_prob']:.1%} home  |  Market: {row['market_home_prob']:.1%}")
        print(f"      Edge: {row['edge']:+.1%} (below 4% threshold)")
        print()

print(f"{'='*80}")
print(f"  SUMMARY")
print(f"{'='*80}")
print(f"  Total games: {len(today_preds)}")
print(f"  [TAKE] Should bet: {len(passes)}")
print(f"  [SKIP] Filtered: {len(filtered)}")
print(f"  [INFO] No edge: {len(no_bet)}")
print()
