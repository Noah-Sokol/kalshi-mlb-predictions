# Production Settings - 3% Edge Threshold (Max Volume Strategy)

## Official Settings (Active Now)

### Core Parameters:
- **Market Blend**: 0.5 (50% model + 50% sportsbook) ✅
- **Min Edge Threshold**: **0.03 (3%)** ← **CHANGED FOR MAX VOLUME**
- **Kelly Fraction**: 0.40 (40% of full Kelly) ✅
- **Min Bet Market Prob**: 0.40 (40% - skip heavy underdogs) ✅
- **Min Team Win %**: 0.33 (33% - skip collapsing teams) ✅

---

## 2024 Backtest Performance (Starting $90)

**With 3% Threshold:**
```
Total Bets:        257 / 2,429 games (10.6%)
Bets per Month:    ~36.7 bets/month (~1-2 per day)
Win Rate:          52.1%
Total P&L:         +$74.08
ROI on Wagered:    +6.3%
Final Bankroll:    $164.08 (started: $90)
Bankroll Growth:   +82.3%
Positive Months:   5/7 (71%)
```

**Monthly Breakdown (2024):**
- Mar: +$0.76
- Apr: +$40.92 ✅ Best month
- May: +$24.49
- Jun: -$7.63 ❌
- Jul: +$12.90
- Aug: +$18.68
- Sep: -$16.04 ❌

---

## Why 3% Instead of 4%?

| Metric | 4% Threshold | 3% Threshold | Winner |
|--------|--------------|--------------|--------|
| **Final Bankroll** | $159.10 | **$164.08** | 3% 🥇 |
| **Total Profit** | $69.10 | **$74.08** | 3% 🥇 |
| Bets per Month | 14.4 | **36.7** | 3% (more action) |
| Win Rate | 55.4% | 52.1% | 4% (higher quality) |
| ROI | 13.9% | 6.3% | 4% (more efficient) |

**Your Priority**: Maximum total profit → **3% wins** ($164 vs $159)

**Trade-off**: 
- Get $5 more profit
- But need 2.5x more bets (257 vs 101)
- Win rate drops 3 points (still profitable at 52.1%)
- More daily activity (~1-2 bets/day vs 2-3 bets/week)

---

## Today's Example (2026-08-08)

**With 3% Threshold:**

### Recommended Bets: **8 bets**

1. **ARI** vs LAD - Edge: +6.4%
2. **OAK** @ BOS - Edge: -5.8% (bet underdog)
3. **CHW** vs CLE - Edge: +4.4%
4. **KCR** vs CHC - Edge: +4.2%
5. **NYY** vs ATL - Edge: +3.9%
6. **LAA** @ MIA - Edge: -3.6% (bet underdog)
7. **TOR** @ PHI - Edge: -3.3% (bet underdog)
8. **SEA** vs TBR - Edge: +3.2%

**vs 4% Threshold: Would have only 4 bets**

---

## What to Expect

### Daily Activity:
- **0-3 bets per day** during regular season
- Some days: 0 bets (when market is efficient)
- Active days: 2-4 bets
- Average: ~1-2 bets per day

### Monthly Volume:
- **~37 bets per month** (vs 14 with 4%)
- Expect 5-10 bets per week
- Much more active than 4% threshold

### Performance Targets:
- **Win Rate**: ~52% (need >52.4% to beat vig, so this is tight!)
- **ROI**: ~6-7% (lower per bet, but more bets)
- **Monthly Growth**: ~11-12% per month on average
- **Annual Growth**: ~80-90% per year (if sustained)

---

## Risk Considerations

### Compared to 4% Threshold:

**Pros:**
- ✅ More total profit ($74 vs $69 in 2024)
- ✅ Higher final bankroll ($164 vs $159)
- ✅ More action (less idle time)
- ✅ Better compounding (more bets = more opportunities)

**Cons:**
- ❌ Lower win rate (52.1% vs 55.4%)
- ❌ Lower ROI (6.3% vs 13.9%)
- ❌ More time commitment (~1-2 bets/day)
- ❌ Thinner edges (closer to vig)
- ❌ More losing months (2/7 vs 1/7)

**The Edge Quality:**
- 3% blended edge = 6% raw model edge
- These are still real edges, but thinner
- Win rate of 52.1% proves edges exist
- But closer to break-even than 4% (55.4%)

---

## How to Use

### Default (uses 3% now):
```bash
python update.py
```

### To go back to 4% (higher quality, less volume):
```bash
python update.py --min-edge 0.04
```

### To go even more aggressive (2%):
```bash
python update.py --min-edge 0.02
```
⚠️ Warning: 2% only had 50.7% win rate and worse final bankroll

---

## Quality Control

**Watch for these red flags:**

1. **Win rate <50%** over 30+ bets → Something's wrong
2. **Losing 2-3 months in a row** → Re-evaluate strategy
3. **ROI <3%** sustained → Edges may be disappearing
4. **Win rate <52%** over 100+ bets → Too close to vig, consider raising threshold

**Good signs:**
- ✅ Win rate staying around 52-53%
- ✅ ROI around 6-7%
- ✅ Most months positive
- ✅ Bankroll growing steadily

---

## When to Reconsider

**Consider switching back to 4% if:**
- You're not comfortable with the daily activity level
- Win rate drops below 51% for extended period
- You prefer quality over quantity
- You want higher confidence per bet

**Consider going to 2% if:**
- You want even more action (79 bets/month!)
- ⚠️ But note: 2% had worse results than 3% in backtest

---

## Files Changed

1. **`daily_update.py`**: Changed default `--min-edge` from 0.04 to 0.03
2. **`update.py`**: Updated help text and display to show 3% as default

No other changes needed - all the quality filters, market blend, and Kelly sizing remain the same.

---

## Next Steps

1. **Run update.py** - Will now use 3% threshold automatically
2. **Expect 8 bets today** (vs 4 with old 4% threshold)
3. **Track win rate** - Should stay around 52-53%
4. **Monitor monthly P&L** - Should be positive most months
5. **After 30-50 bets** - Check if performance matches backtest

---

## Remember

**You're trading quality for quantity:**
- More bets (2.5x more)
- More total profit ($5 more in 2024)
- But thinner edges (52% win rate vs 55%)

**This is the right choice if:**
- You want maximum bankroll growth
- You prefer more action
- You're okay with daily betting activity
- You can handle slightly more volatility

**It's working if:**
- Win rate stays >51%
- Most months are positive
- Bankroll grows steadily

---

**Last Updated**: 2026-08-08  
**Active Setting**: 3% edge threshold  
**Backtest Validated**: 2024 season, $90 → $164 (+82%)  
**Status**: Production Ready for Max Volume Strategy ✅
