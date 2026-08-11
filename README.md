# MLB Kalshi Prediction Model

XGBoost model trained on FanGraphs, Statcast, and park factor data to predict MLB game outcomes.
Bets are placed on [Kalshi](https://kalshi.com) prediction markets when the model's edge over
the sportsbook closing line exceeds 8% and the bet-team's market probability exceeds 40%.

---

## Live Performance

> Tracking since **2026-08-06** &nbsp;|&nbsp; 15 games pending results &nbsp;|&nbsp; *Updated 2026-08-11 13:00*

| | Model Results |
|---|---|
| **Bets** | 19 |
| **Win Rate** | 52.6% |
| **P&L** | +$46.73 |
| **ROI** | +36.9% |
| **Wagered** | $126.54 |

### Recent Daily P&L

| Date | Bets | P&L |
|---|---|---|
| 2026-08-10 | 2 | +$4.06 |
| 2026-08-09 | 4 | +$3.35 |
| 2026-08-08 | 3 | +$9.43 |
| 2026-08-07 | 4 | +$13.91 |
| 2026-08-06 | 4 | +$6.09 |
---

## Backtest Performance (2023–2024 holdout)

Historical odds sourced from SportsBookReview closing lines (~4.5% avg vig removed).
Starting bankroll $1,000, 40% fractional Kelly sizing.

| Filter | Bets | Win Rate | P&L | ROI |
|---|---|---|---|---|
| All bets (≥8% edge, ≥40% mkt) | 1,154 | 50.2% | -$401.52 | -1.0% |
| ≥7% edge | 403 | 52.4% | +$440.87 | +2.3% |
| ≥10% edge | 92 | 56.5% | +$624.88 | +12.4% |
---

## How It Works

1. **Features** — FanGraphs team batting/pitching (xFIP, wRC+, etc.), Statcast exit velocity / spin rate,
   45-day rolling bullpen load, park factors, starting pitcher recent form
2. **Model** — XGBoost classifier, trained on 2015–2022, tested on 2023–2024
3. **Edge** — `model_prob − market_implied_prob` (after vig removal)
4. **Bet sizing** — fractional Kelly (40%) capped at 7.5% of bankroll per bet
5. **Quality filters** — skip bets where the bet-team's market probability < 40%
   (prevents fighting heavy market favorites)

## Project Structure

```
update.py          # Daily entry point — run this every morning
record_bet.py      # Record a bet: python record_bet.py TEAM DOLLARS
backtest.py        # Run historical simulations with custom parameters
export_excel.py    # Regenerate Excel picks tracker
pipeline.py        # Rebuild feature matrix and retrain model
src/
  data/            # MLB API, FanGraphs, Statcast, odds fetchers
  features/        # Feature engineering (bullpen, park factors, game features)
  models/          # XGBoost training and inference
  edge/            # Kelly sizing
  tracking/        # Bet log management
data/
  bets_log.csv     # Live prediction + bet log
  backtest_*.csv   # Saved backtest results
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # add your ODDS_API_KEY
python pipeline.py     # build features + train model
python update.py --bankroll 100
```

---

*Created with Claude Code*
