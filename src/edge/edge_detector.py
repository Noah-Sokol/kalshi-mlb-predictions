"""
Compares model win probabilities to Kalshi market prices to find value bets.

Workflow:
  1. score_games()  — returns model probability for each game
  2. attach_kalshi() — fetches Kalshi implied probabilities (requires API key)
  3. find_edges()   — filters to games where |model - market| > threshold
"""
import pandas as pd

from src.data.kalshi_client import KalshiClient
from src.edge.kelly import bet_recommendation


def score_games(
    today_schedule: pd.DataFrame,
    model_artifacts: dict,
    today_features: pd.DataFrame,
) -> pd.DataFrame:
    """
    Attach model win probabilities to today's scheduled games.

    Parameters:
      today_schedule: from mlb_api.fetch_today_schedule()
      model_artifacts: from win_probability.load()
      today_features: feature row per game built with build_game_features()

    Returns schedule with added 'model_home_prob' column.
    """
    from src.models.win_probability import predict

    probs = predict(today_features, model_artifacts)
    out = today_schedule.copy()
    out["model_home_prob"] = probs.round(4)
    out["model_away_prob"] = (1.0 - probs).round(4)
    return out


def attach_kalshi(
    scored: pd.DataFrame,
    client: KalshiClient,
    ticker_col: str = "kalshi_home_ticker",
) -> pd.DataFrame:
    """
    Fetches Kalshi implied probabilities for each game.

    You need to manually look up Kalshi market tickers for each game and add them
    to `scored[ticker_col]`. Kalshi tickers follow a pattern like 'MLBWIN-NYY-20240815'.
    Check https://kalshi.com/markets/mlb for current tickers.

    Returns scored DataFrame with 'kalshi_home_prob' and 'kalshi_home_price' columns.
    """
    if ticker_col not in scored.columns:
        scored[ticker_col] = None

    kalshi_probs = []
    for _, row in scored.iterrows():
        ticker = row.get(ticker_col)
        if ticker and client._configured:
            prob = client.get_implied_prob(str(ticker))
            kalshi_probs.append(prob)
        else:
            kalshi_probs.append(None)

    scored = scored.copy()
    scored["kalshi_home_prob"] = kalshi_probs
    scored["kalshi_home_price"] = scored["kalshi_home_prob"].apply(
        lambda p: round(p * 100, 1) if p is not None else None
    )
    return scored


def find_edges(
    scored: pd.DataFrame,
    bankroll: float,
    min_edge: float = 0.04,
    kelly_frac: float = 0.25,
    max_pct_bankroll: float = 0.05,
    min_bet_mkt_prob: float = 0.40,
    min_team_win_pct: float = 0.33,
) -> pd.DataFrame:
    """
    Filter to games with a betting edge and attach Kelly sizing.

    Requires 'model_home_prob' and 'kalshi_home_prob' columns.
    Returns only rows with an edge above min_edge, sorted by absolute edge.

    Quality-gate filters (match backtest.py defaults):
      min_bet_mkt_prob  -- skip bets where the market gives the bet-team < this prob.
                           Prevents betting into heavy favorites (default 0.40).
      min_team_win_pct  -- skip bets on teams with h_/a_rolling_win_pct < this.
                           Prevents betting on collapsing rebuilding teams (default 0.33).
                           Set either to 0 to disable.
    """
    import pandas as _pd

    if "kalshi_home_prob" not in scored.columns:
        raise ValueError("Run attach_kalshi() first to get Kalshi implied probabilities.")

    bets = []
    for _, row in scored.iterrows():
        mkt = row.get("kalshi_home_prob")
        mdl = row.get("model_home_prob")
        if mkt is None or mdl is None:
            continue
        try:
            mkt = float(mkt)
            mdl = float(mdl)
        except (TypeError, ValueError):
            continue

        rec = bet_recommendation(
            model_prob=mdl,
            market_prob=mkt,
            bankroll=bankroll,
            min_edge=min_edge,
            kelly_frac=kelly_frac,
            max_pct_bankroll=max_pct_bankroll,
        )

        # Quality-gate filters (same logic as backtest.py simulate_pnl)
        if rec["bet_side"] is not None:
            home_bet    = rec["bet_side"] in ("YES", "home")
            bet_mkt     = mkt if home_bet else (1.0 - mkt)
            wpc_col     = "h_rolling_win_pct"       if home_bet else "a_rolling_win_pct"
            wpc20_col   = "h_rolling_win_pct_last20" if home_bet else "a_rolling_win_pct_last20"
            wpc_val     = row.get(wpc_col)
            wpc20_val   = row.get(wpc20_col)

            if min_bet_mkt_prob > 0 and bet_mkt < min_bet_mkt_prob:
                rec = {"bet_side": None, "kelly_pct": 0, "bet_dollars": 0}
            elif (min_team_win_pct > 0
                  and wpc_val is not None
                  and not _pd.isna(wpc_val)
                  and float(wpc_val) < min_team_win_pct):
                rec = {"bet_side": None, "kelly_pct": 0, "bet_dollars": 0}
            elif (min_team_win_pct > 0
                  and wpc20_val is not None
                  and not _pd.isna(wpc20_val)
                  and float(wpc20_val) < min_team_win_pct - 0.05):
                rec = {"bet_side": None, "kelly_pct": 0, "bet_dollars": 0}

        if rec["bet_side"] is not None:
            bets.append({
                "home_team": row.get("home_team", ""),
                "away_team": row.get("away_team", ""),
                "home_sp":   row.get("home_sp", "TBD"),
                "away_sp":   row.get("away_sp", "TBD"),
                "game_time": row.get("game_time", ""),
                **rec,
            })

    if not bets:
        return _pd.DataFrame()
    return _pd.DataFrame(bets).sort_values("edge", ascending=False, key=abs)


def print_summary(edges: pd.DataFrame) -> None:
    if edges.empty:
        print("No edges found today.")
        return
    print(f"\n{'='*70}")
    print(f"  VALUE BETS ({len(edges)} found)")
    print(f"{'='*70}")
    for _, r in edges.iterrows():
        matchup = f"{r['away_team']} @ {r['home_team']}"
        side_desc = f"Bet {r['bet_side']} on HOME ({r['home_team']})"
        if r["bet_side"] == "NO":
            side_desc = f"Bet {r['bet_side']} on HOME -> backing AWAY ({r['away_team']})"
        print(f"\n  {matchup}")
        print(f"  Starters: {r.get('away_sp','?')} vs {r.get('home_sp','?')}")
        print(f"  {side_desc}")
        print(f"  {r['rationale']}")
        print(f"  -> BET: ${r['bet_dollars']:.2f}")
    print(f"\n{'='*70}\n")
