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

Usage:
    python update.py                           # Normal update (FREE - uses cached odds)
    python update.py --fresh-odds              # Force fresh odds (costs 1 API call)
    python update.py --bankroll 120            # Update your current bankroll
    python update.py --fresh-odds --bankroll 120  # Both options together

Options:
    --fresh-odds        Fetch live odds right now (costs 1 API call, default: use cache if < 4hrs old)
    --bankroll X        Your current bankroll in dollars (default: uses last session's value)
    --market-blend X    Blend model with market: (1-X)*model + X*market (default: 0.0 = no blend, backtest validated)
    --results-only      Just fill results + regenerate Excel (no odds/picks, FREE)
    --min-edge X        Minimum edge threshold (default: 0.08 = 8%, backtest validated)
    --kelly-frac X      Kelly fraction for bet sizing (default: 0.40 = 40%)

Examples:
    python update.py                           # Quick free update
    python update.py --fresh-odds              # Get latest odds (1 API call)
    python update.py --bankroll 150            # Update bankroll to $150
    python update.py --fresh-odds --bankroll 200  # Both

That's it! One command does everything.
"""
import sys

if __name__ == "__main__":
    # Check if user is asking for help
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)

    # Parse arguments to show what will be used
    bankroll = None
    market_blend = None
    for i, arg in enumerate(sys.argv):
        if arg == "--bankroll" and i + 1 < len(sys.argv):
            try:
                bankroll = float(sys.argv[i + 1])
            except ValueError:
                pass
        if arg == "--market-blend" and i + 1 < len(sys.argv):
            try:
                market_blend = float(sys.argv[i + 1])
            except ValueError:
                pass

    # Show settings
    print("="*70)
    print("  UPDATE SETTINGS")
    print("="*70)

    # Show odds setting
    if "--fresh-odds" in sys.argv:
        print("  Odds: FRESH (will use 1 API call from monthly quota)")
    else:
        print("  Odds: CACHED (FREE - no API call, <4hrs old)")

    # Show bankroll
    if bankroll:
        print(f"  Bankroll: ${bankroll:.2f} (will be logged to bankroll_log.csv)")
    else:
        from pathlib import Path
        import pandas as pd
        _bl = Path("data/bankroll_log.csv")
        last = float(pd.read_csv(_bl).iloc[-1]["bankroll"]) if _bl.exists() else 100.0
        print(f"  Bankroll: ${last:.2f} (last logged value — pass --bankroll X to update)")

    # Show market blend
    if market_blend is not None:
        model_pct = (1 - market_blend) * 100
        market_pct = market_blend * 100
        print(f"  Market blend: {model_pct:.0f}% model + {market_pct:.0f}% sportsbook (from --market-blend {market_blend})")
    else:
        print(f"  Market blend: None (no blend, default) -- pure model signal with 8% edge threshold")

    # Show edge threshold
    print(f"  Min edge: 8% (default, backtest validated -- no blend + 8% threshold = optimal sizing)")

    print("="*70)
    print()

    # Call daily_update - it already handles all the arguments!
    from daily_update import main
    main()
