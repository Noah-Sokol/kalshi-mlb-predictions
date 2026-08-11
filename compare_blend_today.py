"""
Quick comparison: OLD (no blend) vs NEW (0.5 blend) predictions for today.
"""
import pandas as pd
from pathlib import Path

log_path = Path("data/bets_log.csv")
if not log_path.exists():
    print("No bets log found")
    exit(1)

df = pd.read_csv(log_path)
df["date"] = pd.to_datetime(df["date"])

# Get today's games
today = pd.Timestamp("2026-08-08").date()
today_games = df[df["date"].dt.date == today].copy()

if today_games.empty:
    print("No games logged for today")
    exit(1)

print(f"\n{'='*80}")
print(f"  COMPARISON: OLD (No Blend) vs NEW (0.5 Blend) for {today}")
print(f"{'='*80}\n")

for _, row in today_games.iterrows():
    home = row["home_team_fg"]
    away = row["away_team_fg"]
    model_prob = row["model_home_prob"]
    market_prob = row["market_home_prob"]
    old_edge = row["edge"]
    old_rec = row["recommended_side"]

    # Calculate what the NEW blended system would predict
    if pd.notna(market_prob):
        # Blend: 50% model + 50% market
        blended_prob = 0.5 * model_prob + 0.5 * market_prob
        new_edge = blended_prob - market_prob
    else:
        blended_prob = model_prob
        new_edge = None

    print(f"{away} @ {home}")
    print(f"  Market:          {market_prob:.1%}")
    print(f"  Model (OLD):     {model_prob:.1%}  =>  Edge: {old_edge:+.1%}  =>  Rec: {old_rec}")

    if pd.notna(market_prob):
        # Determine if NEW system would bet
        if abs(new_edge) >= 0.07:
            new_rec = "YES" if new_edge > 0 else "NO"
            print(f"  Blended (NEW):   {blended_prob:.1%}  =>  Edge: {new_edge:+.1%}  =>  Rec: {new_rec}")
        else:
            print(f"  Blended (NEW):   {blended_prob:.1%}  =>  Edge: {new_edge:+.1%}  =>  Rec: none (below 7% threshold)")
    else:
        print(f"  Blended (NEW):   {blended_prob:.1%}  =>  No market data")

    print()

print(f"{'='*80}")
print("SUMMARY:")
print(f"  OLD system bets: {(today_games['recommended_side'] != 'none').sum()}")

# Count NEW system bets
new_bets = 0
for _, row in today_games.iterrows():
    if pd.notna(row["market_home_prob"]):
        blended_prob = 0.5 * row["model_home_prob"] + 0.5 * row["market_home_prob"]
        new_edge = blended_prob - row["market_home_prob"]
        if abs(new_edge) >= 0.07:
            new_bets += 1

print(f"  NEW system bets (with 0.5 blend): {new_bets}")
print(f"\nNote: NEW system also includes quality filters (min market prob 40%, min team win% 33%)")
print(f"      which may further reduce the bet count from what's shown above.")
print(f"{'='*80}\n")
