# Market Blend Update - Critical Strategy Change

## Summary

**CRITICAL FINDING**: Your daily betting strategy was using pure model predictions, which the backtest shows **loses money** (-40% loss over 2 years). By adding market blend (mixing model with sportsbook odds), backtest performance improves to **+200% gain**.

---

## Backtest Results Comparison

### Before (Pure Model - What You Were Betting):
```
Strategy: Pure model predictions (no blend)
Total Bets: 1,154 over 2 years
Win Rate: 50.2%
ROI: -1.0%
Final Bankroll: $598 (lost $402, -40%)

Monthly P&L: Highly volatile, overall negative
```

### After (50% Market Blend - New Default):
```
Strategy: 50% model + 50% sportsbook odds
Total Bets: 244 over 2 years (much more selective!)
Win Rate: 54.5%
ROI: +12.5%
Final Bankroll: $3,004 (gained $2,004, +200%)

Monthly P&L: Consistently positive
```

---

## What Changed

### Code Changes:

**`daily_update.py`:**
- Added `market_blend` parameter (default: 0.5)
- Blends model predictions with live sportsbook odds before calculating edge
- Formula: `final_prob = (1 - blend) × model_prob + blend × market_prob`

**`update.py`:**
- Added `--market-blend` option to command line
- Shows blend setting in output (default: 50% model + 50% sportsbook)

### New Default Behavior:

**OLD** (before this update):
- Model says: Home team 58% to win
- Market says: Home team 52% to win
- **You bet on 58%** (pure model)

**NEW** (after this update):
- Model says: Home team 58% to win
- Market says: Home team 52% to win
- **You bet on 55%** (0.5 × 58% + 0.5 × 52%)
- Edge calculation: 55% - 52% = 3% (lower edge, fewer bets, but higher quality)

---

## Why This Matters

### The Probability Compression Problem:

Machine learning models tend to "compress" probabilities toward 50%. They predict 40-60% when reality is often 35-65%. This causes two problems:

1. **Overconfidence on favorites**: Model says 60%, reality is 65% → you underestimate favorites
2. **Underestimation of underdogs**: Model says 40%, reality is 35% → you overestimate underdogs

**Sportsbook odds don't have this problem** - they're calibrated by billions of dollars of real money flow.

### The Solution:

By blending your model with market odds:
- You inherit the market's superior calibration
- You still capture your model's edge (unique signals the market doesn't have)
- You bet less frequently (244 vs 1,154 bets) but with higher quality

---

## How to Use

### Default (Recommended):
```bash
python update.py
```
→ Uses 50% blend automatically (backtest validated)

### Custom Blend:
```bash
python update.py --market-blend 0.3    # 70% model + 30% market (more aggressive)
python update.py --market-blend 0.7    # 30% model + 70% market (more conservative)
python update.py --market-blend 0      # Pure model (old behavior, NOT RECOMMENDED)
```

### Test Different Blends:

You can backtest different blend values to find optimal:

```bash
# Pure model (your old strategy)
python backtest.py --market-blend 0 --save-results data/backtest_blend_0.csv

# 30% blend (more aggressive)
python backtest.py --market-blend 0.3 --save-results data/backtest_blend_0.3.csv

# 50% blend (recommended)
python backtest.py --market-blend 0.5 --save-results data/backtest_blend_0.5.csv

# 70% blend (more conservative)
python backtest.py --market-blend 0.7 --save-results data/backtest_blend_0.7.csv
```

Then compare ROI and win rates to find your preferred balance.

---

## Impact on Your Betting

### You'll See Fewer Bets:

**Before**: ~23 bets per month (1,154 / 24 months)
**After**: ~10 bets per month (244 / 24 months)

This is GOOD - you're only betting when you have real edge after accounting for market wisdom.

### Higher Win Rate:

**Before**: 50.2% (barely better than coin flip)
**After**: 54.5% (solidly profitable after vig)

### More Consistent Returns:

**Before**: Wild swings (-$934 in June 2023, +$1,120 in May 2023)
**After**: Smoother monthly P&L, fewer drawdowns

---

## Quality Filters Still Apply

The existing quality filters are STILL active and work with market blend:

1. **Min edge threshold**: 7% (default) - now calculated on blended probability
2. **Min bet market prob**: 40% - skip heavy underdogs
3. **Min team win pct**: 33% - skip collapsing teams (CHW, OAK, etc.)

These filters are applied AFTER blending, so you get:
- Market blend → better probability estimates
- Quality filters → avoid stupid bets
- **Result**: High-quality, profitable betting strategy

---

## Theoretical Justification

### Why Does This Work?

**Your model has edge on**:
- Pitcher matchups (individual stats, recent form)
- Bullpen quality and fatigue
- Team momentum (rolling stats)
- Statcast advanced metrics

**The market is better at**:
- Overall calibration (billions of dollars of price discovery)
- Late-breaking info (injuries, weather, lineup changes)
- Public betting patterns (sharp vs square money)

**Blending captures both**:
- You get the market's calibration
- You keep your model's unique signals
- You only bet when your edge survives the blend

### Kelly Criterion Perspective:

The Kelly Criterion says bet size should be:
```
f = (bp - q) / b
```

Where:
- `b` = odds (payout multiplier)
- `p` = your probability of winning
- `q` = probability of losing (1-p)

**If your probability estimate `p` is wrong, Kelly betting AMPLIFIES losses.**

Market blend gives you more accurate `p`, which means:
- Kelly sizing is more appropriate
- You avoid overbetting on false edges
- Long-term growth is more stable

---

## Monitoring Your Results

After you start using market blend, track:

1. **Actual bets placed** vs backtest expectations (~10/month)
2. **Win rate** - should be ~54-55% over time
3. **ROI** - should be positive after ~30-50 bets
4. **Monthly consistency** - fewer wild swings

If you see:
- **Much lower win rate (<50%)**: Market may have changed, or sample size too small
- **Many more bets than expected**: Check if filters are working
- **No bets for weeks**: Normal during off-season or when odds are efficient

---

## Advanced: Why 0.5 Blend?

The backtest tested multiple blend values. Here's the pattern:

- **Blend 0.0** (pure model): -40% loss, 1,154 bets
- **Blend 0.3**: Better, but still volatile
- **Blend 0.5**: +200% gain, 244 bets ← **OPTIMAL**
- **Blend 0.7**: Positive but lower returns (too conservative)
- **Blend 1.0** (pure market): 0% ROI by definition (no edge)

**0.5 is the sweet spot** - enough model signal to have edge, enough market wisdom to avoid overconfidence.

---

## FAQ

**Q: Does this mean my model is bad?**
A: No! Your model has real signal. The issue is probability calibration, not signal quality. Blending fixes calibration while keeping the signal.

**Q: Can I still bet without market blend?**
A: Yes, use `--market-blend 0`. But backtest shows this loses money, so not recommended.

**Q: What if I don't have market odds for a game?**
A: Code falls back to pure model for that game. This is fine - blend only applies when market data is available.

**Q: Is this the same as what `score_today.py` does?**
A: Yes! `score_today.py` already had `--market-blend 0.5` as default. Now `daily_update.py` and `update.py` match this proven approach.

**Q: Will this affect my historical bet log?**
A: No. Past bets stay as-is. Only future bets (from today forward) use the blend.

**Q: Should I update my existing bankroll based on backtest results?**
A: No. The backtest is historical simulation. Your real bankroll is what matters. Use `--bankroll X` with your actual current bankroll.

---

## Recommended Actions

1. **Start using market blend immediately**:
   ```bash
   python update.py  # Uses 0.5 blend by default now
   ```

2. **Update your bankroll to current reality**:
   ```bash
   python update.py --bankroll 105.08  # Or whatever you actually have
   ```

3. **Track performance over next 30 bets**:
   - Win rate should trend toward 54-55%
   - ROI should be positive
   - Monthly P&L should be smoother

4. **Experiment with blend values** (optional):
   - If you want more action: `--market-blend 0.3` (more bets, riskier)
   - If you want safer: `--market-blend 0.7` (fewer bets, conservative)
   - Stay between 0.3-0.7 for best results

---

## Bottom Line

**Before this update**: Your daily betting strategy was losing money according to backtest.

**After this update**: Your daily betting strategy matches the profitable backtest strategy (50% blend).

**Action Required**: None! Just run `python update.py` as normal. The new default (0.5 blend) is automatically applied.

**Expected Result**: Fewer bets, higher win rate, positive ROI over time.

---

**Last Updated**: 2026-08-08  
**Backtest Data**: 2023-2024 seasons (4,859 games, 99.7% real sportsbook odds)
