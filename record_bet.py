"""
Record an actual bet you placed.

Usage:
    python record_bet.py TEAM DOLLARS [NOTES]
    python record_bet.py TEAM none            # mark a suggestion as skipped

Examples:
    python record_bet.py OAK 1
    python record_bet.py CHC 3.75
    python record_bet.py MIL 7
    python record_bet.py BAL none

TEAM is the FanGraphs abbreviation of the team you bet on
(same as shown in daily_update output).
"""
import sys
from datetime import date
from pathlib import Path
import os; os.chdir(Path(__file__).parent)

from src.tracking.bet_tracker import record_actual_bet, print_summary, load_log


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)

    team   = args[0].upper()
    amount = args[1]
    notes  = args[2] if len(args) > 2 else None

    skip    = amount.lower() == "none"
    dollars = 0.0 if skip else float(amount)

    # Find today's game for this team in the bets log
    today_str = str(date.today())
    df = load_log()
    df["date"] = df["date"].astype(str).str[:10]
    today = df[df["date"] == today_str]

    home_match = today[today["home_team_fg"] == team]
    away_match = today[today["away_team_fg"] == team]

    if not home_match.empty:
        row       = home_match.iloc[0]
        home_team = row["home_team_fg"]
        away_team = row["away_team_fg"]
        side      = "none" if skip else "home"
    elif not away_match.empty:
        row       = away_match.iloc[0]
        home_team = row["home_team_fg"]
        away_team = row["away_team_fg"]
        side      = "none" if skip else "away"
    else:
        sys.exit(
            f"No game found for {team} on {today_str} in the bets log. "
            "Run daily_update.py first to log today's predictions."
        )

    ok = record_actual_bet(home_team, away_team, side, dollars, notes=notes)
    if ok:
        print_summary(n_days=7)


if __name__ == "__main__":
    main()
