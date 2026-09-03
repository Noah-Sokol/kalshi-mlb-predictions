"""
Generates README.md with live model performance stats pulled from bets_log.csv.
Called automatically by update.py after each daily run.
"""
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import os; os.chdir(Path(__file__).parent)

TRACKING_START  = pd.Timestamp("2026-08-06")
LOG_PATH        = Path("data/bets_log.csv")
BACKTEST_PATH   = Path("data/backtest_nb_8pct.csv")
README_PATH     = Path("README.md")


def _load_live() -> pd.DataFrame:
    df = pd.read_csv(LOG_PATH)
    df["date"] = pd.to_datetime(df["date"])
    for col in ["theoretical_pnl", "actual_pnl", "suggested_dollars", "actual_dollars", "market_home_prob", "edge"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[df["date"] >= TRACKING_START].copy()


def _live_stats(df: pd.DataFrame) -> dict:
    settled = df.dropna(subset=["home_win"])
    rec = settled[settled["recommended_side"].isin(["home", "away", "YES", "NO"])]
    placed = settled[settled["actual_side"].isin(["home", "away", "YES", "NO"])]

    theo_pnl     = rec["theoretical_pnl"].fillna(0).sum()
    theo_wagered = rec["suggested_dollars"].fillna(0).sum()
    theo_wins    = int((rec["theoretical_pnl"].fillna(0) > 0).sum())
    theo_n       = len(rec)

    act_pnl     = placed["actual_pnl"].fillna(0).sum()
    act_wagered = placed["actual_dollars"].fillna(0).sum()
    act_wins    = int((placed["actual_pnl"].fillna(0) > 0).sum())
    act_n       = len(placed)

    # Daily breakdown (actual, last 10 days with results, newest first)
    daily = (
        placed.groupby(placed["date"].dt.date)
        .agg(pnl=("actual_pnl", "sum"), n=("actual_pnl", "count"))
        .sort_index(ascending=False)
        .head(10)
    )

    return {
        "theo_n":        theo_n,
        "theo_wins":     theo_wins,
        "theo_win_rate": theo_wins / theo_n if theo_n > 0 else 0,
        "theo_pnl":      theo_pnl,
        "theo_roi":      theo_pnl / theo_wagered if theo_wagered > 0 else 0,
        "theo_wagered":  theo_wagered,
        "act_n":         act_n,
        "act_wins":      act_wins,
        "act_win_rate":  act_wins / act_n if act_n > 0 else 0,
        "act_pnl":       act_pnl,
        "act_roi":       act_pnl / act_wagered if act_wagered > 0 else 0,
        "act_wagered":   act_wagered,
        "daily":         daily,
        "start_date":    TRACKING_START.strftime("%Y-%m-%d"),
        "pending":       int(df["home_win"].isna().sum()),
    }


AVG_VIG = 0.045  # average sportsbook vig (~4.5%, sourced from SBR closing lines)


def _pnl_after_vig(subset: pd.DataFrame, vig: float = AVG_VIG) -> float:
    """Recalculate P&L using vig-inclusive odds (win payouts reduced by vig spread)."""
    total = 0.0
    for _, row in subset.iterrows():
        mkt     = float(row["market_prob"])
        dollars = float(row["bet_dollars"])
        home_bet = row["bet_side"] in ("YES", "home")
        won      = bool(row["won"])
        # Vig inflates the quoted probability toward 0.5 by vig/2 on each side
        q = min(mkt + vig / 2, 0.99) if home_bet else min((1 - mkt) + vig / 2, 0.99)
        win_odds = (1 - q) / q
        total += dollars * win_odds if won else -dollars
    return total


def _backtest_stats() -> list[dict]:
    if not BACKTEST_PATH.exists():
        return []
    bt   = pd.read_csv(BACKTEST_PATH)
    bets = bt[bt["bet_side"].notna() & (bt["bet_dollars"] > 0)].copy()
    bets["won"] = np.where(
        bets["bet_side"].isin(["YES", "home"]),
        bets["outcome_home_win"] == 1,
        bets["outcome_home_win"] == 0,
    )

    all_bets = bets
    hi10     = bets[bt.loc[bets.index, "edge"].abs() >= 0.10]

    rows = []
    for label, subset in [
        ("≥8% edge (production threshold)", all_bets),
        ("≥10% edge",                       hi10),
    ]:
        n = len(subset)
        if n == 0:
            continue
        wagered     = subset["bet_dollars"].sum()
        pnl_free    = subset["pnl"].sum()
        pnl_vig     = _pnl_after_vig(subset)
        rows.append({
            "label":    label,
            "n":        n,
            "win_rate": subset["won"].mean(),
            "roi_free": pnl_free / wagered if wagered > 0 else 0,
            "roi_vig":  pnl_vig  / wagered if wagered > 0 else 0,
        })
    return rows


def _pct(val: float, decimals: int = 1) -> str:
    sign = "+" if val >= 0 else ""
    return f"{sign}{val*100:.{decimals}f}%"


def _dollar(val: float) -> str:
    sign = "+" if val >= 0 else "-"
    return f"{sign}${abs(val):.2f}"


def generate() -> None:
    df  = _load_live()
    s   = _live_stats(df)
    bt  = _backtest_stats()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Daily P&L rows
    daily_rows = ""
    for d, row in s["daily"].iterrows():
        pnl_str = _dollar(row["pnl"])
        daily_rows += f"| {d} | {int(row['n'])} | {pnl_str} |\n"

    # Backtest rows
    bt_rows = ""
    for r in bt:
        bt_rows += (
            f"| {r['label']} | {r['n']:,} | {r['win_rate']:.1%} | "
            f"{_pct(r['roi_free'])} | {_pct(r['roi_vig'])} |\n"
        )

    readme = f"""\
# MLB DraftKings Betting Model

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![XGBoost](https://img.shields.io/badge/Model-XGBoost-orange)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-Live-brightgreen)

An end-to-end MLB sports betting system running in production since August 2026.
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

> Tracking since **{s['start_date']}** &nbsp;|&nbsp; {s['pending']} games pending results &nbsp;|&nbsp; *Updated {now}*

| | Model Results |
|---|---|
| **Bets** | {s['theo_n']} |
| **Win Rate** | {s['theo_win_rate']:.1%} |
| **P&L** | {_dollar(s['theo_pnl'])} |
| **ROI** | {_pct(s['theo_roi'])} |
| **Wagered** | ${s['theo_wagered']:.2f} |

### Recent Daily P&L

| Date | Bets | P&L |
|---|---|---|
{daily_rows}
---

## Backtest Results (2023–2024 holdout)

Holdout data: 4,859 MLB games not used in training.
Historical closing lines from SportsBookReview (vig ~4.5% removed before edge calculation).
Starting bankroll $1,000, 40% fractional Kelly.

| Filter | Bets | Win Rate | ROI (vig-free) | ROI (with ~4.5% vig) |
|---|---|---|---|---|
{bt_rows}
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
A positive edge means the model believes the home team is underpriced against the
DraftKings line.

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
  where  b = (1 − market_prob) / market_prob   (DraftKings net odds)
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
| APIs | FanGraphs, MLB Stats API, The Odds API (DraftKings, FanDuel, BetMGM, Caesars, Bovada) |
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
  data/             # API clients: FanGraphs, Statcast, MLB API, odds
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
# Edit .env with your ODDS_API_KEY

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

# Record an actual bet placed on DraftKings
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
"""

    README_PATH.write_text(readme, encoding="utf-8")
    print(f"  README.md updated ({len(readme):,} chars)")


if __name__ == "__main__":
    generate()
