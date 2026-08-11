# MLB Prediction Market Model

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![XGBoost](https://img.shields.io/badge/Model-XGBoost-orange)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-Live-brightgreen)

An end-to-end MLB prediction market system running in production since August 2026.
It ingests live FanGraphs, Statcast, and closing-line odds data; trains an XGBoost
classifier to estimate game win probabilities; detects positive-EV edges against the
market; sizes bets via fractional Kelly criterion; and logs everything for daily tracking.

> **Stats update automatically** on every daily run via `generate_readme.py` — no manual
> editing required.

---

## Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Daily Pipeline                               │
│                                                                     │
│  MLB API       FanGraphs     Statcast      The Odds API             │
│  (schedule) ──► (team stats) ──► (pitch/exit) ──► (closing lines)  │
│       │              │               │                  │           │
│       └──────────────┴───────────────┘                  │           │
│                      │                                  │           │
│              Feature Engineering                        │           │
│         (bullpen load, park factors,                    │           │
│          rolling form, matchup stats)                   │           │
│                      │                                  │           │
│              XGBoost Classifier                         │           │
│             (trained 2015–2022,                         │           │
│              tested 2023–2024)                          │           │
│                      │                                  │           │
│                      ▼                                  ▼           │
│                Edge Detection ◄────────────────────────┘           │
│           (model_prob − market_prob)                                │
│                      │                                              │
│              Quality Filters                                        │
│         (min 8% edge, min 40% mkt prob,                            │
│          min 33% team win pct)                                      │
│                      │                                              │
│           Fractional Kelly Sizing                                   │
│         (40% Kelly, 7.5% bankroll cap)                             │
│                      │                                              │
│              bets_log.csv  +  Excel tracker                         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Live Performance

> Tracking since **2026-08-06** &nbsp;|&nbsp; 15 games pending results &nbsp;|&nbsp; *Updated 2026-08-11 18:13*

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

## Backtest Results (2023–2024 holdout)

Holdout data: 4,859 MLB games not used in training.
Historical closing lines from SportsBookReview (vig ~4.5% removed before edge calculation).
Starting bankroll $1,000, 40% fractional Kelly.

| Filter | Bets | Win Rate | ROI (vig-free) | ROI (with ~4.5% vig) |
|---|---|---|---|---|
| ≥8% edge (production threshold) | 244 | 54.5% | +10.4% | +5.4% |
| ≥10% edge | 92 | 56.5% | +21.6% | +16.0% |

> The vig column shows what returns look like against real sportsbook prices —
> a useful reality check on theoretical edge.

---

## How It Works

### 1. Feature Engineering

Each game is represented by ~40 features built from multiple sources:

| Source | Features |
|---|---|
| FanGraphs | wRC+, xFIP, BABIP, K%, BB%, team batting/pitching ratings |
| Statcast | Exit velocity, barrel rate, spin rate, hard-hit % |
| MLB API | Lineup, starting pitcher, rest days, home/away |
| Custom | 45-day rolling bullpen load, park factor adjustments, last-20-game form |

### 2. XGBoost Classifier

- Trained on 2015–2022 regular seasons (~18,000 games)
- Held out 2023–2024 for backtesting (never seen during training)
- Predicts home-team win probability (calibrated with isotonic regression)
- Features selected via SHAP importance analysis

### 3. Edge Detection

```
edge = model_prob − market_implied_prob
```

The market probability is derived from The Odds API closing lines after vig removal.
A positive edge means the model believes the home team is underpriced on Kalshi.

### 4. Quality Filters

Before recommending a bet, three filters run:

- **Min edge ≥ 8%** — threshold validated via backtest grid search
- **Bet-team market prob ≥ 40%** — avoids fighting heavily favored teams
- **Team last-20 win pct ≥ 33%** — avoids collapsing rosters whose stats lag reality

Backtesting showed the 38–40% market prob zone has catastrophically bad returns (−28.6% ROI),
confirming these filters are load-bearing.

### 5. Kelly Criterion Sizing

```
Kelly fraction  =  (p × b − q) / b
  where  b = (1 − market_prob) / market_prob   (Kalshi net odds)
         p = model_prob
         q = 1 − p

Bet size = 40% × Kelly fraction × bankroll
         (capped at 7.5% of bankroll per bet)
```

Fractional Kelly (40%) provides ~95% of the theoretical growth rate of full Kelly
while dramatically reducing variance and drawdown.

---

## Tech Stack

| Layer | Tools |
|---|---|
| Model | XGBoost, scikit-learn, SHAP |
| Data | pandas, pybaseball, requests |
| APIs | FanGraphs, MLB Stats API, The Odds API, Kalshi API |
| Tracking | CSV log, openpyxl Excel export |
| Automation | Windows Task Scheduler / cron, auto-updating README |

---

## Project Structure

```
update.py           # One-command daily entry point
record_bet.py       # Record an actual bet: python record_bet.py TEAM DOLLARS
backtest.py         # Replay strategy over historical data
pipeline.py         # Rebuild full feature matrix and retrain model
export_excel.py     # Regenerate Excel picks tracker
generate_readme.py  # Auto-generate this README from live stats
setup_scheduler.py  # Configure daily scheduled runs
src/
  data/             # API clients: FanGraphs, Statcast, MLB API, Kalshi, odds
  features/         # Feature engineering: bullpen load, park factors, game context
  models/           # XGBoost training, inference, calibration
  edge/             # Kelly criterion sizing and edge detection
  tracking/         # Bet log read/write and P&L calculation
data/
  bets_log.csv      # Live log of every prediction and bet result
  backtest_nb_8pct.csv  # Holdout backtest results at 8% edge threshold
reports/            # Analysis: edge-vs-return correlation, market efficiency tests
```

---

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Add API keys
cp .env.example .env
# Edit .env with your ODDS_API_KEY and KALSHI_API_KEY

# 3. Build features and train model
python pipeline.py

# 4. Run the first daily update
python update.py --bankroll 100
```

### Daily Usage

```bash
# Every morning — fills results, fetches odds, scores games, exports tracker
python update.py --bankroll 125

# Force fresh odds (costs 1 API call; default reuses cache if < 4 hrs old)
python update.py --fresh-odds --bankroll 125

# Record an actual bet placed on Kalshi
python record_bet.py CIN 9.50
```

---

## Analysis

The [`reports/`](reports/) directory contains exploratory analysis done during development:

- **Edge vs. return correlation** — statistical test of whether model edge predicts
  actual wins (evidence against a perfectly efficient market)
- **Market blend analysis** — comparing pure model vs. market-blended probabilities
- **Threshold backtesting** — grid search over edge thresholds and filters

---

## Disclaimer

This project is for educational and research purposes. Prediction markets involve
real financial risk. Past backtest performance does not guarantee future results.
