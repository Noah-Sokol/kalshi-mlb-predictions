"""
Show all edges for today using NEW blended system (0.5 blend).
"""
import pandas as pd
from pathlib import Path

log_path = Path("data/bets_log.csv")
df = pd.read_csv(log_path)
df["date"] = pd.to_datetime(df["date"])

# Get today's games
today = pd.Timestamp("2026-08-08").date()
today_games = df[df["date"].dt.date == today].copy()

if today_games.empty:
    print("No games logged for today")
    exit(1)

print(f"\n{'='*100}")
print(f"  ALL EDGES FOR {today} - NEW BLENDED SYSTEM (50% model + 50% sportsbook)")
print(f"{'='*100}\n")

# Prepare data
results = []
for _, row in today_games.iterrows():
    home = row["home_team_fg"]
    away = row["away_team_fg"]
    model_prob = row["model_home_prob"]
    market_prob = row["market_home_prob"]

    if pd.notna(market_prob):
        # Blend: 50% model + 50% market
        blended_prob = 0.5 * model_prob + 0.5 * market_prob
        edge = blended_prob - market_prob

        # Determine bet side
        if abs(edge) >= 0.07:
            if edge > 0:
                bet_side = "HOME"
                bet_team = home
            else:
                bet_side = "AWAY"
                bet_team = away
        else:
            bet_side = "none"
            bet_team = ""
    else:
        blended_prob = model_prob
        edge = None
        bet_side = "N/A"
        bet_team = ""

    results.append({
        "matchup": f"{away} @ {home}",
        "market": market_prob,
        "model": model_prob,
        "blended": blended_prob,
        "edge": edge,
        "bet": bet_side,
        "bet_team": bet_team,
    })

# Sort by absolute edge (largest edges first)
results_df = pd.DataFrame(results)
results_df["abs_edge"] = results_df["edge"].abs()
results_df = results_df.sort_values("abs_edge", ascending=False, na_position="last")

# Print formatted table
print(f"{'Matchup':<20} {'Market':<8} {'Model':<8} {'Blended':<8} {'Edge':<8} {'Bet?':<8} {'Team':<5}")
print(f"{'-'*20} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*5}")

for _, r in results_df.iterrows():
    matchup = r["matchup"]
    market = f"{r['market']:.1%}" if pd.notna(r["market"]) else "N/A"
    model = f"{r['model']:.1%}"
    blended = f"{r['blended']:.1%}"
    edge = f"{r['edge']:+.1%}" if pd.notna(r["edge"]) else "N/A"
    bet = r["bet"]
    team = r["bet_team"]

    print(f"{matchup:<20} {market:<8} {model:<8} {blended:<8} {edge:<8} {bet:<8} {team:<5}")

print(f"\n{'='*100}")
print(f"SUMMARY:")
print(f"  Total games:        {len(results_df)}")
print(f"  Games with odds:    {results_df['market'].notna().sum()}")
print(f"  Bets (edge >= 7%):  {(results_df['bet'] != 'none').sum()}")
print(f"\nNote: Quality filters (min market prob 40%, min team win% 33%) not shown above")
print(f"      but would be applied before actual bet placement.")
print(f"{'='*100}\n")
