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
BACKTEST_PATH   = Path("data/backtest_no_blend.csv")
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


def _backtest_stats() -> list[dict]:
    if not BACKTEST_PATH.exists():
        return []
    bt = pd.read_csv(BACKTEST_PATH)
    bets = bt[bt["bet_side"].notna() & (bt["bet_dollars"] > 0)].copy()
    bets["bet_mkt_prob"] = np.where(
        bets["bet_side"].isin(["YES", "home"]),
        bets["market_prob"],
        1 - bets["market_prob"]
    )
    bets["won"] = np.where(
        bets["bet_side"].isin(["YES", "home"]),
        bets["outcome_home_win"] == 1,
        bets["outcome_home_win"] == 0
    )
    rows = []
    for label, mask in [
        ("All bets (>4% edge)",  bets["bet_mkt_prob"] >= 0.0),
        (">7% edge",             bets["bet_mkt_prob"] >= 0.0),   # placeholder — filtered by edge below
        (">10% edge",            bets["bet_mkt_prob"] >= 0.0),
    ]:
        pass

    # Use the original backtest CSV which was run with 8% edge + 40% mkt filter
    # Bucket by absolute edge size post-hoc
    all_bets = bets
    hi7  = bets[abs(bt.loc[bets.index, "edge"]) >= 0.07]
    hi10 = bets[abs(bt.loc[bets.index, "edge"]) >= 0.10]

    for label, subset in [
        ("All bets (≥8% edge, ≥40% mkt)", all_bets),
        ("≥7% edge",  hi7),
        ("≥10% edge", hi10),
    ]:
        n = len(subset)
        if n == 0:
            continue
        wagered = subset["bet_dollars"].sum()
        pnl     = subset["pnl"].sum()
        rows.append({
            "label":    label,
            "n":        n,
            "win_rate": subset["won"].mean(),
            "pnl":      pnl,
            "roi":      pnl / wagered if wagered > 0 else 0,
            "final_bk": subset["bankroll"].iloc[-1],
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
            f"{_dollar(r['pnl'])} | {_pct(r['roi'])} |\n"
        )

    readme = f"""\
# MLB Kalshi Prediction Model

XGBoost model trained on FanGraphs, Statcast, and park factor data to predict MLB game outcomes.
Bets are placed on [Kalshi](https://kalshi.com) prediction markets when the model's edge over
the sportsbook closing line exceeds 8% and the bet-team's market probability exceeds 40%.

---

## Live Performance

> Tracking since **{s['start_date']}** &nbsp;|&nbsp; {s['pending']} games pending results &nbsp;|&nbsp; *Updated {now}*

|  | Theoretical *(took every suggestion)* | Actual *(bets placed)* |
|---|---|---|
| **Bets** | {s['theo_n']} | {s['act_n']} |
| **Win Rate** | {s['theo_win_rate']:.1%} | {s['act_win_rate']:.1%} |
| **P&L** | {_dollar(s['theo_pnl'])} | {_dollar(s['act_pnl'])} |
| **ROI** | {_pct(s['theo_roi'])} | {_pct(s['act_roi'])} |
| **Wagered** | ${s['theo_wagered']:.2f} | ${s['act_wagered']:.2f} |

### Recent Daily P&L (actual bets)

| Date | Bets | P&L |
|---|---|---|
{daily_rows}\
---

## Backtest Performance (2023–2024 holdout)

Historical odds sourced from SportsBookReview closing lines (~4.5% avg vig removed).
Starting bankroll $1,000, 40% fractional Kelly sizing.

| Filter | Bets | Win Rate | P&L | ROI |
|---|---|---|---|---|
{bt_rows}\
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
"""

    README_PATH.write_text(readme, encoding="utf-8")
    print(f"  README.md updated ({len(readme):,} chars)")


if __name__ == "__main__":
    generate()
