# CODING PLAN: Backtesting System Overhaul with Historical Odds Integration

**Date Created:** 2026-08-07  
**Objective:** Integrate historical MLB betting odds (2021-2025) from ArnavSaraogi's GitHub dataset into the backtesting framework to replace simulated market prices with real sportsbook closing lines, enabling accurate ROI calculations based on actual market pricing.

**Bet Placement Time Assumption:** 10:00 AM EST (as specified)

---

## PHASE 1: DATA ACQUISITION & PREPARATION

### Task 1.1: Download Historical Odds Dataset
- **Action:** Clone or download ArnavSaraogi/mlb-odds-scraper repository
- **Location:** https://github.com/ArnavSaraogi/mlb-odds-scraper
- **Dataset:** 76 MB JSON file covering March 2021 to August 2025
- **Target Directory:** `data/raw/historical_odds/`
- **Files to obtain:**
  - Pre-scraped dataset (JSON format)
  - Scraper code (for potential future updates)

### Task 1.2: Explore Dataset Schema
- **Action:** Load and inspect the JSON structure
- **Key fields to identify:**
  - Date/timestamp of odds capture
  - Home team / Away team identifiers
  - Bookmakers included (DraftKings, FanDuel, Bet365, etc.)
  - Moneyline odds (American format: +150, -180, etc.)
  - Point spread / Totals (secondary, but may be useful)
  - Timestamp resolution (are these opening lines, closing lines, or multiple snapshots?)

### Task 1.3: Create Odds Data Parser
- **New File:** `src/data/historical_odds_parser.py`
- **Functions to implement:**
  - `load_historical_odds(json_path: str) -> pd.DataFrame`
  - `parse_sportsbook_review_format(raw_json: dict) -> pd.DataFrame`
  - `american_to_implied_prob(odds: int) -> float` (convert -180 → 0.643)
  - `remove_vig(home_prob: float, away_prob: float) -> tuple[float, float]`
  - `map_team_names_to_fg(team_name: str) -> str` (standardize to FanGraphs abbreviations)

### Task 1.4: Clean & Normalize Historical Odds
- **Action:** Process raw JSON into standardized CSV
- **Output File:** `data/processed/historical_odds_clean.csv`
- **Schema:**
  ```
  date (YYYY-MM-DD)
  home_team_fg (e.g., "NYY")
  away_team_fg (e.g., "BOS")
  bookmaker (e.g., "draftkings", "fanduel", "bet365")
  home_ml_odds (American format, e.g., -150)
  away_ml_odds (American format, e.g., +130)
  home_implied_prob (raw, with vig)
  away_implied_prob (raw, with vig)
  home_devigged_prob (vig-removed, sums to 1.0)
  away_devigged_prob (vig-removed, sums to 1.0)
  timestamp (if available)
  odds_type (e.g., "closing", "opening", "10am_snapshot")
  ```

### Task 1.5: Determine Bet Placement Time Logic
- **Challenge:** Dataset may contain multiple odds snapshots per game
- **Strategy Options:**
  1. **10 AM EST snapshot** (if available in data)
  2. **Closest to 10 AM** (find nearest timestamp before first pitch)
  3. **Closing line** (last odds before game start - most accurate for market efficiency)
  
- **Recommended Approach:** 
  - Use **closing line** as the primary market price (most informative for edge detection)
  - Add optional **10 AM line** simulation if timestamps support it
  - Document in backtest which line was used

- **Implementation:**
  - `get_market_odds_at_time(game_date, home_team, away_team, target_time="closing") -> dict`

---

## PHASE 2: FEATURE MATRIX INTEGRATION

### Task 2.1: Merge Historical Odds into Feature Matrix
- **File to modify:** `pipeline.py` (or create new `pipeline_with_odds.py`)
- **Action:** After `build_game_features()` completes, merge historical odds
- **Join Logic:**
  ```python
  # Match on: date + home_team_fg + away_team_fg
  features = features.merge(
      historical_odds[['date', 'home_team_fg', 'away_team_fg', 
                       'market_home_prob', 'bookmaker_consensus']],
      on=['date', 'home_team_fg', 'away_team_fg'],
      how='left'
  )
  ```

### Task 2.2: Handle Multiple Bookmaker Prices
- **Strategy:** Average across top bookmakers (DraftKings, FanDuel, BetMGM, Caesars)
- **Function:** `compute_consensus_odds(game_odds: pd.DataFrame) -> float`
- **Logic:**
  - Filter to preferred bookmakers (avoid smaller books with stale lines)
  - Average implied probabilities (after vig removal)
  - Flag games with wide spreads between books (>3% difference = suspicious)

### Task 2.3: Fallback for Missing Odds
- **Reality:** Not all games will have historical odds (especially 2021-2022 data may be sparse)
- **Fallback Strategy:**
  1. First try: consensus from available bookmakers
  2. If no odds found: use Pythagorean expectation (current fallback)
  3. Add column: `odds_source` ("real", "pythagorean_fallback")
- **Reporting:** Track % of games with real odds vs. fallback

### Task 2.4: Update Feature Matrix Schema
- **New Columns to Add:**
  - `market_home_prob` (already exists, but now populated with real data)
  - `market_away_prob` (= 1 - market_home_prob)
  - `odds_source` ("real" | "pythagorean" | "logistic")
  - `n_bookmakers` (how many books had lines for this game)
  - `bookmaker_consensus_spread` (max - min implied prob across books)
  - `bet_time` ("closing" | "10am" | "opening")

---

## PHASE 3: BACKTEST SYSTEM OVERHAUL

### Task 3.1: Modify `backtest.py` Market Simulation Logic
- **Current State:** Lines 226-283 generate synthetic market prices
- **Change:** Add new default mode: `--use-historical-odds`
- **New Argument Priority:**
  1. `--use-historical-odds` (NEW, should become default)
  2. `--use-saved-market` (existing, uses `market_prices.csv` from daily logs)
  3. `--logistic-market` (existing, LR baseline)
  4. `--sim-spread` (existing, flat probability)
  5. Pythagorean (existing fallback)

### Task 3.2: Create Historical Odds Loading Function
- **Function:** `_historical_odds_market(test: pd.DataFrame) -> np.ndarray`
- **Location:** Add to `backtest.py` around line 285
- **Logic:**
  ```python
  def _historical_odds_market(test: pd.DataFrame) -> np.ndarray:
      """
      Load real market prices from historical odds dataset.
      Uses market_home_prob column if present; otherwise loads from 
      data/processed/historical_odds_clean.csv and merges.
      """
      if "market_home_prob" in test.columns and "odds_source" in test.columns:
          # Already merged in feature matrix
          market_probs = test["market_home_prob"].values
          real_odds_mask = test["odds_source"] == "real"
          n_real = real_odds_mask.sum()
          n_total = len(test)
          print(f"Using historical odds: {n_real:,} real / {n_total:,} games ({n_real/n_total:.1%})")
          return market_probs
      else:
          # Load and merge on the fly (fallback if pipeline wasn't run with odds)
          odds_df = pd.read_csv("data/processed/historical_odds_clean.csv")
          # ... merge logic ...
  ```

### Task 3.3: Update P&L Simulation for Real Odds
- **Function:** `simulate_pnl()` (lines 86-158)
- **Key Changes:**
  - **Odds calculation:** Currently uses simplified Kalshi binary contract math
  - **Need:** Convert American moneyline odds to payout multipliers
  - **Add column:** `bookmaker_odds` (American format: -150, +130)
  - **Update payout logic:**
    ```python
    # Current (Kalshi):
    win_odds = (1.0 - mkt) / mkt  # binary contract payout
    
    # New (Sportsbook moneyline):
    if american_odds > 0:
        win_odds = american_odds / 100.0  # +150 → 1.5x payout
    else:
        win_odds = 100.0 / abs(american_odds)  # -150 → 0.667x payout
    ```

### Task 3.4: Add Bet Placement Time Simulation
- **New Parameter:** `--bet-time` ("10am" | "closing" | "opening")
- **Default:** "closing" (most accurate for backtest)
- **Logic:**
  - Filter odds data to specified timestamp window
  - If 10am selected but not available, use closest available or skip game
  - Track % of games where bet could be placed at target time

### Task 3.5: Enhanced Reporting for Real Odds
- **Function:** `print_report()` (lines 161-223)
- **New Stats to Add:**
  - % of bets placed on real odds vs. simulated
  - Average vig paid across all bets
  - Bookmaker-by-bookmaker breakdown (if multiple books used)
  - Comparison: ROI on real odds vs. ROI on Pythagorean (sanity check)
  - Edge realization rate: (actual ROI) / (expected ROI from edge)

---

## PHASE 4: VALIDATION & QUALITY CHECKS

### Task 4.1: Create Odds Data Quality Report
- **New Script:** `validate_historical_odds.py`
- **Checks:**
  - Coverage: % of games with odds per season (2021, 2022, 2023, 2024)
  - Bookmaker availability: which books are in dataset
  - Vig distribution: histogram of implied total (should cluster around 1.03-1.05)
  - Outliers: flag games with >1.10 total (stale/bad data)
  - Timestamp distribution: are these opening/closing/midday lines?
  - Team name mapping: verify all teams map correctly to FanGraphs abbreviations

### Task 4.2: Sanity Check Against Known Market Efficiency
- **Test:** Run backtest with `--min-edge 0.00` (bet all games)
- **Expected Result:** ROI near -3% to -5% (vig cost)
- **Interpretation:**
  - If ROI ≈ -4%: odds data is valid
  - If ROI > 0%: data quality issue (not true closing lines)
  - If ROI < -10%: data quality issue (stale lines, bad mapping)

### Task 4.3: Compare Real vs. Simulated Backtest
- **Action:** Run backtest in 3 modes on same data
  1. `--use-historical-odds` (real odds)
  2. `--logistic-market` (simulated LR baseline)
  3. Default Pythagorean (existing baseline)
- **Expected:**
  - Real odds backtest should show LOWER ROI (market is harder than Pythagorean)
  - Real odds backtest should show MORE bets triggered (real market is more varied)
  - Real odds backtest is the only "true" performance estimate

### Task 4.4: Date Alignment Validation
- **Risk:** Odds captured on different date than game played
- **Check:** Verify odds date matches game date (not day before/after)
- **Handle timezone issues:** Games after midnight, west coast late games

---

## PHASE 5: INTEGRATION WITH EXISTING WORKFLOW

### Task 5.1: Update `pipeline.py`
- **Add Step:** After feature engineering, merge historical odds
- **Add Flag:** `--include-historical-odds` (default: True)
- **Output:** `game_features.csv` now includes `market_home_prob` from real data

### Task 5.2: Update `backtest.py` Default Behavior
- **Change Default:** From Pythagorean to `--use-historical-odds`
- **Preserve Existing Modes:** Keep all simulation options for testing
- **Add Warning:** If historical odds not found, warn user and fall back

### Task 5.3: Modify `score_today.py` for Consistency
- **Current:** Uses live Odds API or Kalshi API
- **Ensure:** Same vig removal logic as historical data
- **Ensure:** Same bookmaker averaging logic
- **Goal:** Today's bets use same market interpretation as backtest

### Task 5.4: Update Documentation
- **Files to Update:**
  - `backtest.py` docstring (lines 1-26)
  - README or CLAUDE.md (if exists)
  - Add `docs/historical_odds_integration.md`
- **Document:**
  - How to download ArnavSaraogi dataset
  - How to run pipeline with odds
  - How to interpret new backtest metrics
  - Limitations of historical odds data

---

## PHASE 6: ADVANCED FEATURES (OPTIONAL)

### Task 6.1: Line Movement Analysis
- **Feature:** Track how odds moved from opening to closing
- **Use Case:** Detect sharp money (late line moves)
- **Implementation:** Add `opening_line` and `closing_line` columns
- **Analysis:** Compare model edge at opening vs. closing

### Task 6.2: Bookmaker-Specific Backtests
- **Feature:** Backtest against specific bookmaker (e.g., only DraftKings)
- **Use Case:** Some books have softer lines on MLB
- **Implementation:** Add `--bookmaker draftkings` argument

### Task 6.3: Time-of-Day Edge Analysis
- **Feature:** Compare performance of 10am bets vs. closing line bets
- **Hypothesis:** Early lines may be softer
- **Implementation:** Run backtest twice with different `--bet-time`

### Task 6.4: CLV (Closing Line Value) Calculation
- **Definition:** Did we bet better than the closing line?
- **Formula:** CLV = (closing_line_prob - bet_time_prob)
- **Use Case:** Positive CLV = we're beating the market even if bet loses

---

## FILE STRUCTURE AFTER IMPLEMENTATION

```
kalshi_predictions/
├── data/
│   ├── raw/
│   │   └── historical_odds/
│   │       ├── mlb_odds_2021_2025.json  (76MB from GitHub)
│   │       └── README_source.txt         (link to GitHub repo)
│   ├── processed/
│   │   ├── game_features.csv             (MODIFIED: now includes market_home_prob)
│   │   └── historical_odds_clean.csv     (NEW: cleaned odds data)
│   └── market_prices.csv                 (existing: live daily logs)
├── src/
│   ├── data/
│   │   ├── historical_odds_parser.py     (NEW)
│   │   ├── odds_api.py                   (existing)
│   │   └── kalshi_client.py              (existing)
│   └── edge/
│       ├── kelly.py                       (MODIFIED: handle moneyline odds)
│       └── edge_detector.py               (existing)
├── backtest.py                            (MODIFIED: add historical odds mode)
├── pipeline.py                            (MODIFIED: merge odds in feature build)
├── score_today.py                         (MODIFIED: consistent odds handling)
├── validate_historical_odds.py            (NEW: data quality checks)
└── download_historical_odds.sh            (NEW: fetch from GitHub)
```

---

## TESTING PLAN

### Unit Tests
1. Test `american_to_implied_prob()` with known values
2. Test `remove_vig()` math (should sum to 1.0)
3. Test team name mapping (all 30 teams)

### Integration Tests
1. Load 1 month of historical odds, verify merge with features
2. Run backtest on 10 games, manually verify P&L calculation
3. Compare manual sportsbook payout calculation vs. code

### Regression Tests
1. Ensure existing Pythagorean backtest still works
2. Ensure existing Logistic backtest still works
3. Ensure --use-saved-market still works with market_prices.csv

---

## ESTIMATED TIMELINE

- **Phase 1 (Data Acquisition):** 2-3 hours
- **Phase 2 (Feature Integration):** 2-3 hours  
- **Phase 3 (Backtest Overhaul):** 3-4 hours
- **Phase 4 (Validation):** 2 hours
- **Phase 5 (Integration):** 1-2 hours
- **Phase 6 (Optional Advanced):** 3-5 hours

**Total Core Implementation:** ~10-14 hours
**With Advanced Features:** ~15-20 hours

---

## KEY DECISIONS TO CONFIRM

1. **Bet placement time:** 10 AM EST (as specified) or closing line (more accurate)?
2. **Bookmaker selection:** Average all books or prefer specific ones (DraftKings/FanDuel)?
3. **Vig removal:** Multiplicative or additive? (Multiplicative is standard)
4. **Missing odds handling:** Skip game or use Pythagorean fallback?
5. **Odds format in backtest:** Show American odds (-150) or implied prob (0.60)?

---

## IMPLEMENTATION NOTES

- The ArnavSaraogi dataset is from SportsBookReview, which aggregates multiple bookmakers
- Coverage is March 2021 to August 2025, which overlaps with our training data perfectly
- The dataset includes Point Spread, Money Line, and Totals - we'll focus on Money Line
- Real odds will likely show our model has less edge than Pythagorean simulation suggests (this is expected and good - it's a reality check)

---

## NEXT STEPS

To begin implementation, start with **Phase 1, Task 1.1**: Download the historical odds dataset from GitHub and explore its structure. This will inform all subsequent implementation decisions.

**Command to start:**
```bash
git clone https://github.com/ArnavSaraogi/mlb-odds-scraper.git data/raw/historical_odds/
```
