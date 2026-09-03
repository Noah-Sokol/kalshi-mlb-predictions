"""
Kelly criterion bet sizing for binary moneyline bets (sportsbook YES/NO sides).

Full Kelly maximizes long-run log-wealth but has high variance.
Fractional Kelly (typically 0.25-0.5) reduces variance at the cost of slower growth.
"""
import numpy as np


def kelly_fraction(model_prob: float, market_prob: float) -> float:
    """
    Kelly fraction for a binary moneyline bet.

    A winning YES bet pays out at the market's vig-removed implied odds
    (cost = market_prob dollars per dollar of exposure). So the odds received
    on a YES bet are: (1 - market_prob) / market_prob.

    Kelly formula: f = (p * b - q) / b
      where b = net odds per dollar risked, p = model win prob, q = 1 - p.

    Returns a fraction of bankroll to bet (can be negative if no edge).
    """
    if market_prob <= 0 or market_prob >= 1:
        return 0.0
    b = (1.0 - market_prob) / market_prob
    p = model_prob
    q = 1.0 - p
    return (p * b - q) / b


def fractional_kelly(model_prob: float, market_prob: float, fraction: float = 0.25) -> float:
    """Fractional Kelly - recommended for live betting due to lower variance."""
    return fraction * kelly_fraction(model_prob, market_prob)


def edge(model_prob: float, market_prob: float) -> float:
    """
    Simple edge = model probability - market-implied probability.
    Positive edge means the model thinks the YES side is underpriced.
    """
    return model_prob - market_prob


def bet_recommendation(
    model_prob: float,
    market_prob: float,
    bankroll: float,
    min_edge: float = 0.04,
    kelly_frac: float = 0.40,
    max_pct_bankroll: float = 0.075,
) -> dict:
    """
    Full bet recommendation for one game.

    Parameters:
      model_prob: calibrated model home-win probability
      market_prob: market-implied home-win probability (vig-removed from sportsbook odds)
      bankroll: total available bankroll in dollars
      min_edge: minimum edge to recommend a bet (default 4%)
      kelly_frac: fractional Kelly multiplier (default 0.40)
      max_pct_bankroll: hard cap per bet (default 7.5% of bankroll)

    Returns dict with: bet_side, edge, kelly_pct, bet_dollars, rationale.
    """
    e = edge(model_prob, market_prob)
    bet_side = "YES" if e > 0 else "NO"
    abs_e = abs(e)

    if abs_e < min_edge:
        return {
            "bet_side": None,
            "edge": round(e, 4),
            "kelly_pct": 0.0,
            "bet_dollars": 0.0,
            "rationale": f"No edge - {abs_e:.1%} below {min_edge:.1%} threshold",
        }

    if bet_side == "YES":
        fk = fractional_kelly(model_prob, market_prob, kelly_frac)
    else:
        fk = fractional_kelly(1.0 - model_prob, 1.0 - market_prob, kelly_frac)

    fk = max(0.0, fk)
    pct = min(fk, max_pct_bankroll)
    dollars = round(pct * bankroll, 2)

    return {
        "bet_side": bet_side,
        "edge": round(e, 4),
        "kelly_pct": round(pct, 4),
        "bet_dollars": dollars,
        "rationale": (
            f"Model: {model_prob:.1%}  |  Market: {market_prob:.1%}  |  "
            f"Edge: {e:+.1%}  |  {kelly_frac:.0%} Kelly -> {pct:.1%} of bankroll"
        ),
    }


def size_bets(predictions: list[dict], bankroll: float, **kwargs) -> list[dict]:
    """Apply bet sizing to a list of game prediction dicts."""
    results = []
    for pred in predictions:
        rec = bet_recommendation(
            pred["model_prob"], pred["market_prob"], bankroll, **kwargs
        )
        results.append({**pred, **rec})
    return results
