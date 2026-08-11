# Quick Start Guide - MLB Betting System

## One Command to Rule Them All

### `python update.py`

This does **everything**:
- ✅ Fills yesterday's results and calculates P&L
- ✅ Fetches today's games and odds (cached, FREE)
- ✅ Runs the ML model
- ✅ Shows you which bets to take (filtered picks only)
- ✅ Updates Excel tracker (2 tabs: Filtered + All Bets)
- ✅ Shows your performance stats

---

## Daily Workflow

### Morning (anytime):
```bash
python update.py
```
→ Shows today's filtered picks
→ FREE (uses cached odds)

### Record your bets:
```bash
python record_bet.py CHW 5.50
python record_bet.py NYY 5.00
```

### Next morning:
```bash
python update.py
```
→ Fills yesterday's results
→ Shows today's new picks
→ Updates your Excel tracker

**That's it!**

---

## Common Commands

### Normal update (FREE):
```bash
python update.py
```

### Get fresh live odds (costs 1 API call):
```bash
python update.py --fresh-odds
```

### Update your bankroll:
```bash
python update.py --bankroll 150
```

### Both:
```bash
python update.py --fresh-odds --bankroll 200
```

### Just fill results (midnight run, FREE):
```bash
python update.py --results-only
```

---

## See All Edges (including filtered out):
```bash
python show_all_edges.py
```

Shows:
- [TAKE] Bets that pass filters
- [SKIP] Bets filtered out (big underdogs)
- [INFO] Games with no edge

---

## Files You Care About

### Excel Tracker:
`data/picks_tracker.xlsx`
- **Tab 1 "Filtered"**: Only bets you should take
- **Tab 2 "All Bets"**: Every model recommendation (for analysis)

### Bet Log:
`data/bets_log.csv`
- Raw data with all predictions and actual bets

---

## What Gets Updated Automatically

When you run `python update.py`:

1. **Bets log** (`data/bets_log.csv`) - adds today's predictions
2. **Excel tracker** - both tabs regenerated with latest data
3. **Market prices** (`data/market_prices.csv`) - today's odds logged
4. **Results** - yesterday's games filled in with outcomes and P&L

---

## Default Settings

- **Bankroll**: $105.08 (change with `--bankroll X`)
- **Min Edge**: 7% (change with `--min-edge 0.08`)
- **Kelly Fraction**: 40% (change with `--kelly-frac 0.30`)
- **Quality Filters**: 
  - Market must give bet-team ≥ 40% win probability
  - Edge must be ≥ 7%

---

## API Quota

- **Total**: 500 calls/month
- **Cached odds**: FREE (updated every 4 hours automatically)
- **Fresh odds**: Costs 1 call (use `--fresh-odds`)
- **Check remaining**: Shows in update output

---

## Tips

✅ **Run `python update.py` daily** - It's free with cached odds
✅ **Use Filtered tab** - That's what you should bet
✅ **Check All Bets tab** - See what you're missing (usually correctly filtered)
✅ **Update bankroll** - After big wins/losses for accurate Kelly sizing
✅ **Don't overthink it** - The filters are based on backtesting, trust them

---

## Troubleshooting

**Excel won't update?**
→ Close `picks_tracker.xlsx` in Excel, then run `python update.py` again

**No picks today?**
→ Normal! Not every day has +7% edges that pass filters

**Want to see filtered bets anyway?**
→ Run `python show_all_edges.py`

**API quota running low?**
→ Just use cached odds (default). They update every 4 hours automatically.
