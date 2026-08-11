"""
Show today's bets with NEW system: 0.5 blend + 4% edge threshold
"""
import pandas as pd
from pathlib import Path

log_path = Path("data/bets_log.csv")
df = pd.read_csv(log_path)
df["date"] = pd.to_datetime(df["date"])

# Get today's games
today = pd.Timestamp("2026-08-08").date()
today_games = df[df["date"].dt.date == today].copy()

MIN_EDGE = 0.04  # 4% threshold

print(f"\n{'='*100}")
print(f"  TODAY'S BETS - NEW SYSTEM (50% blend + 4% edge threshold)")
print(f"{'='*100}\n")

bets = []
for _, row in today_games.iterrows():
    home = row["home_team_fg"]
    away = row["away_team_fg"]
    model_prob = row["model_home_prob"]
    market_prob = row["market_home_prob"]

    if pd.notna(market_prob):
        # Blend: 50% model + 50% market
        blended_prob = 0.5 * model_prob + 0.5 * market_prob
        edge = blended_prob - market_prob

        # Check if passes 4% threshold
        if abs(edge) >= MIN_EDGE:
            if edge > 0:
                bet_side = "HOME"
                bet_team = home
            else:
                bet_side = "AWAY"
                bet_team = away

            bets.append({
                "matchup": f"{away} @ {home}",
                "bet_team": bet_team,
                "market": market_prob,
                "blended": blended_prob,
                "edge": edge,
                "bet_side": bet_side,
            })

# Sort by absolute edge
bets_df = pd.DataFrame(bets)
if not bets_df.empty:
    bets_df["abs_edge"] = bets_df["edge"].abs()
    bets_df = bets_df.sort_values("abs_edge", ascending=False)

    print(f"RECOMMENDED BETS: {len(bets_df)}\n")
    for i, r in enumerate(bets_df.itertuples(), 1):
        print(f"BET #{i}: {r.matchup}")
        print(f"  Bet: {r.bet_team} ({r.bet_side})")
        print(f"  Market prob: {r.market:.1%}")
        print(f"  Blended prob: {r.blended:.1%}")
        print(f"  Edge: {r.edge:+.1%}")
        print()
else:
    print("No bets meet the 4% edge threshold today.\n")

print(f"{'='*100}")
print(f"Note: Quality filters (min market prob 40%, min team win% 33%) may further")
print(f"      filter these bets before actual placement.")
print(f"{'='*100}\n")
