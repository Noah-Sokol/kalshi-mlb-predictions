"""
SHAP-based per-bet explanations for the XGBoost win probability model.

SHAP decomposes each prediction into per-feature contributions.  The model
outputs raw log-odds which are then Platt-scaled to a calibrated probability.
We convert SHAP log-odds contributions to approximate probability-point
contributions using the local derivative at the prediction point:
    dp ≈ shap_logodds × p × (1 - p)
This gives the "+X.X%" numbers shown in the explanation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Human-readable labels for each model feature
_LABELS: dict[str, str] = {
    # Rolling game-log
    "h_rolling_win_pct":        "Home season win %",
    "h_rolling_pythag":         "Home Pythagorean win %",
    "h_rolling_rd_pg":          "Home run differential/game",
    "h_rolling_rs_pg":          "Home runs scored/game",
    "h_rolling_ra_pg":          "Home runs allowed/game",
    "h_rolling_home_rs_pg":     "Home runs scored at home",
    "h_rolling_home_ra_pg":     "Home runs allowed at home",
    "a_rolling_win_pct":        "Away season win %",
    "a_rolling_pythag":         "Away Pythagorean win %",
    "a_rolling_rd_pg":          "Away run differential/game",
    "a_rolling_rs_pg":          "Away runs scored/game",
    "a_rolling_ra_pg":          "Away runs allowed/game",
    "a_rolling_road_rs_pg":     "Away runs scored on road",
    "a_rolling_road_ra_pg":     "Away runs allowed on road",
    "h_rolling_sos":            "Home strength of schedule",
    "a_rolling_sos":            "Away strength of schedule",
    # FanGraphs batting
    "home_wrc_plus":            "Home lineup wRC+ (offense)",
    "away_wrc_plus":            "Away lineup wRC+ (offense)",
    # FanGraphs pitching staff
    "home_staff_xfip":          "Home pitching staff xFIP",
    "away_staff_xfip":          "Away pitching staff xFIP",
    "home_staff_fip":           "Home pitching staff FIP",
    "away_staff_fip":           "Away pitching staff FIP",
    # Bullpen aggregate
    "home_bp_xfip":             "Home bullpen xFIP",
    "away_bp_xfip":             "Away bullpen xFIP",
    "home_bp_fip":              "Home bullpen FIP",
    "away_bp_fip":              "Away bullpen FIP",
    "home_bp_era":              "Home bullpen ERA",
    "away_bp_era":              "Away bullpen ERA",
    # Leverage-weighted bullpen
    "home_bp_leverage_wtd_xfip": "Home bullpen xFIP (leverage-wtd)",
    "away_bp_leverage_wtd_xfip": "Away bullpen xFIP (leverage-wtd)",
    "home_bp_leverage_wtd_fip":  "Home bullpen FIP (leverage-wtd)",
    "away_bp_leverage_wtd_fip":  "Away bullpen FIP (leverage-wtd)",
    # Bullpen fatigue
    "home_bp_ip_1d":            "Home bullpen innings (last 1 day)",
    "away_bp_ip_1d":            "Away bullpen innings (last 1 day)",
    "home_bp_ip_2d":            "Home bullpen innings (last 2 days)",
    "away_bp_ip_2d":            "Away bullpen innings (last 2 days)",
    "home_bp_ip_3d":            "Home bullpen innings (last 3 days)",
    "away_bp_ip_3d":            "Away bullpen innings (last 3 days)",
    # Starter depth
    "home_sp_ip_avg_10g":       "Home starter avg IP/start",
    "away_sp_ip_avg_10g":       "Away starter avg IP/start",
    # Starter quality
    "home_sp_xfip":             "Home starter xFIP",
    "away_sp_xfip":             "Away starter xFIP",
    "home_sp_fip":              "Home starter FIP",
    "away_sp_fip":              "Away starter FIP",
    # Starter recent form
    "home_sp_era_last5":        "Home starter ERA (last 5 starts)",
    "away_sp_era_last5":        "Away starter ERA (last 5 starts)",
    "home_sp_ip_last5":         "Home starter IP/start (last 5)",
    "away_sp_ip_last5":         "Away starter IP/start (last 5)",
    # Statcast
    "home_xwoba_gap":           "Home lineup xwOBA vs expected",
    "away_xwoba_gap":           "Away lineup xwOBA vs expected",
    "home_pit_xwoba_gap":       "Home pitching xwOBA allowed gap",
    "away_pit_xwoba_gap":       "Away pitching xwOBA allowed gap",
    # Park + calendar
    "park_run_factor":          "Park run factor",
    "game_month":               "Month of season",
    # Market
    "market_home_prob":         "Sportsbook closing line",
}


def explain_bet(
    feat_row: pd.DataFrame,
    artifacts: dict,
    calibrated_prob: float,
    top_n: int = 6,
) -> list[dict]:
    """
    Return the top_n features driving this prediction, with approximate
    probability-point contributions.

    Parameters
    ----------
    feat_row : one-row DataFrame in FEATURE_COLS order (after _fill_features)
    artifacts : model artifacts dict from win_probability.load()
    calibrated_prob : the Platt-calibrated probability for this row
    top_n : how many features to return

    Returns
    -------
    List of dicts sorted by |contribution|, largest first:
        {"feature": str, "label": str, "contribution": float, "value": float}
    """
    try:
        import shap
    except ImportError:
        return []

    model = artifacts["model"]
    model_features = list(model.feature_names_in_)

    # Align feature row to model's expected columns
    X = feat_row.copy()
    for col in model_features:
        if col not in X.columns:
            X[col] = np.nan
    X = X[model_features]

    explainer   = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X)   # shape (1, n_features), log-odds space

    if shap_values.ndim == 1:
        sv = shap_values
    else:
        sv = shap_values[0]

    # Convert log-odds contributions → approximate probability-point contributions
    p = calibrated_prob
    scale = p * (1 - p)   # derivative of sigmoid at this point
    contributions = sv * scale

    rows = []
    for feat, contrib, val in zip(model_features, contributions, X.values[0]):
        rows.append({
            "feature":      feat,
            "label":        _LABELS.get(feat, feat),
            "contribution": float(contrib),
            "value":        float(val) if not np.isnan(val) else None,
        })

    rows.sort(key=lambda r: abs(r["contribution"]), reverse=True)
    return rows[:top_n]


def format_explanation(
    home_team: str,
    away_team: str,
    model_prob: float,
    market_prob: float,
    bet_side: str,
    contributions: list[dict],
) -> str:
    """Format SHAP contributions as a human-readable explanation block."""
    edge = model_prob - market_prob
    bet_team = home_team if bet_side in ("YES", "home") else away_team

    lines = [
        f"  Why the model likes {bet_team}:",
    ]
    for r in contributions:
        sign   = "+" if r["contribution"] >= 0 else ""
        pct    = f"{sign}{r['contribution']*100:.1f}%"
        lines.append(f"    {pct:>7}  {r['label']}")

    return "\n".join(lines)
