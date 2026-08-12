"""
Assembles the per-game feature matrix used for model training and live scoring.

Design principles:
- Rolling stats from game logs are computed strictly pre-game (no leakage)
- FanGraphs pitching/batting stats use the PREVIOUS season to avoid leakage
  (season-to-date stats for a game on May 1 would include September data if not lagged)
- Park factors are static per team-season
- Starting pitcher joined on name; unmatched starters get league-average fill
"""
import numpy as np
import pandas as pd

from src.features.park_factors import get_park_factor

PYTHAGOREAN_EXP = 1.83


def pythagorean_pct(rs: float, ra: float) -> float:
    if rs + ra == 0:
        return 0.5
    return rs**PYTHAGOREAN_EXP / (rs**PYTHAGOREAN_EXP + ra**PYTHAGOREAN_EXP)


# ── Rolling team stats from game log ─────────────────────────────────────────

def build_rolling_team_stats(games: pd.DataFrame) -> pd.DataFrame:
    """
    From game results (date, home_team_fg, away_team_fg, home_score, away_score),
    compute expanding cumulative stats for each team strictly before each game.

    Returns long-form DataFrame with columns:
      game_id, team_fg, is_home + rolling_* features.
    """
    games = games.copy().sort_values("date")

    home = games[["game_id", "date", "home_team_fg", "home_score", "away_score"]].copy()
    home.columns = ["game_id", "date", "team_fg", "rs", "ra"]
    home["is_home"] = True

    away = games[["game_id", "date", "away_team_fg", "away_score", "home_score"]].copy()
    away.columns = ["game_id", "date", "team_fg", "rs", "ra"]
    away["is_home"] = False

    long = (
        pd.concat([home, away], ignore_index=True)
        .sort_values(["team_fg", "date", "game_id"])
        .reset_index(drop=True)
    )
    long["win"] = (long["rs"] > long["ra"]).astype(float)

    parts = []
    for team, grp in long.groupby("team_fg"):
        grp = grp.sort_values("date").reset_index(drop=True)

        # Expanding sums, shifted by 1 so each row uses only prior games
        cum_rs = grp["rs"].cumsum().shift(1, fill_value=0.0)
        cum_ra = grp["ra"].cumsum().shift(1, fill_value=0.0)
        cum_w  = grp["win"].cumsum().shift(1, fill_value=0.0)
        n      = grp["rs"].shift(1).expanding().count().fillna(0.0)

        # Home-only and road-only expanding sums
        h_rs = (grp["rs"]  * grp["is_home"]).cumsum().shift(1, fill_value=0.0)
        h_ra = (grp["ra"]  * grp["is_home"]).cumsum().shift(1, fill_value=0.0)
        h_n  = grp["is_home"].cumsum().shift(1, fill_value=0.0)

        r_rs = (grp["rs"]  * ~grp["is_home"]).cumsum().shift(1, fill_value=0.0)
        r_ra = (grp["ra"]  * ~grp["is_home"]).cumsum().shift(1, fill_value=0.0)
        r_n  = (~grp["is_home"]).cumsum().shift(1, fill_value=0.0)

        grp["rolling_games"]     = n
        grp["rolling_win_pct"]   = cum_w / n.replace({0: np.nan})
        _pythag_num   = cum_rs**PYTHAGOREAN_EXP
        _pythag_denom = cum_rs**PYTHAGOREAN_EXP + cum_ra**PYTHAGOREAN_EXP
        _safe_denom   = np.where(_pythag_denom > 0, _pythag_denom, 1.0)
        grp["rolling_pythag"]    = np.where(
            (n > 0) & (_pythag_denom > 0),
            _pythag_num / _safe_denom,
            0.5,
        )
        grp["rolling_rs_pg"]     = cum_rs / n.replace({0: np.nan})
        grp["rolling_ra_pg"]     = cum_ra / n.replace({0: np.nan})
        grp["rolling_rd_pg"]     = (cum_rs - cum_ra) / n.replace({0: np.nan})

        # Short-window (last 20 games) features — capture recent form / collapses
        # Shift(1) ensures no same-day leakage; min_periods=5 avoids NaN early in season.
        shifted_win = grp["win"].shift(1)
        shifted_rs  = grp["rs"].shift(1)
        shifted_ra  = grp["ra"].shift(1)
        grp["rolling_win_pct_last20"] = (
            shifted_win.rolling(window=20, min_periods=5).mean()
        )
        grp["rolling_rd_pg_last20"] = (
            (shifted_rs - shifted_ra).rolling(window=20, min_periods=5).mean()
        )

        # Home-split run rates (what the team does specifically at home)
        grp["rolling_home_rs_pg"] = h_rs / h_n.replace({0: np.nan})
        grp["rolling_home_ra_pg"] = h_ra / h_n.replace({0: np.nan})

        # Road-split run rates
        grp["rolling_road_rs_pg"] = r_rs / r_n.replace({0: np.nan})
        grp["rolling_road_ra_pg"] = r_ra / r_n.replace({0: np.nan})


        parts.append(grp)

    return pd.concat(parts, ignore_index=True)


def build_sos(rolling: pd.DataFrame, window_games: int = 30) -> pd.DataFrame:
    """
    Strength of Schedule: for each game, compute the average Pythagorean win%
    of the opponents faced in the last `window_games` games (pre-game).

    Uses explicit merges (not set_index/reindex) to avoid game_id type-mismatch issues.
    """
    # Snapshot: each team's pythag quality at the time of each game
    pythag_snap = rolling[["game_id", "team_fg", "rolling_pythag"]].copy()

    # Home team games: opponent is the away team
    home = rolling[rolling["is_home"]][["game_id", "date", "team_fg"]].copy()
    away_as_opp = (
        rolling[~rolling["is_home"]][["game_id", "team_fg", "rolling_pythag"]]
        .rename(columns={"team_fg": "opp_fg", "rolling_pythag": "opp_pythag_at_game"})
    )
    home_matchups = home.merge(away_as_opp, on="game_id", how="left")

    # Away team games: opponent is the home team
    away = rolling[~rolling["is_home"]][["game_id", "date", "team_fg"]].copy()
    home_as_opp = (
        rolling[rolling["is_home"]][["game_id", "team_fg", "rolling_pythag"]]
        .rename(columns={"team_fg": "opp_fg", "rolling_pythag": "opp_pythag_at_game"})
    )
    away_matchups = away.merge(home_as_opp, on="game_id", how="left")

    matchups = pd.concat([home_matchups, away_matchups], ignore_index=True)

    sos_rows = []
    for team, grp in matchups.groupby("team_fg"):
        grp = grp.sort_values("date").reset_index(drop=True)
        sos_vals = (
            grp["opp_pythag_at_game"]
            .shift(1)
            .rolling(window=window_games, min_periods=5)
            .mean()
        )
        grp["rolling_sos"] = sos_vals
        sos_rows.append(grp[["game_id", "team_fg", "rolling_sos"]])

    return pd.concat(sos_rows, ignore_index=True)


# ── Feature matrix assembly ───────────────────────────────────────────────────

def build_game_features(
    games: pd.DataFrame,
    team_bat_prev: pd.DataFrame,             # FanGraphs team batting, previous season
    team_pit_sp_prev: pd.DataFrame,          # FanGraphs team pitching (starters), previous season
    team_pit_rp_prev: pd.DataFrame,          # FanGraphs team pitching (relievers), previous season
    pitchers_prev: pd.DataFrame,             # FanGraphs individual pitchers, previous season
    bp_quality_prev: pd.DataFrame = None,    # IP-weighted RP quality (bullpen.aggregate_team_bp_quality)
    bp_load: pd.DataFrame = None,            # rolling fatigue (bullpen.compute_rolling_bp_load)
    sc_bat_prev: pd.DataFrame = None,        # Statcast team batting (statcast.fetch_team_batting_sc)
    sc_pit_prev: pd.DataFrame = None,        # Statcast team pitching (statcast.fetch_team_pitching_sc)
    sp_rolling_stats: pd.DataFrame = None,   # current-season rolling starter form (bullpen.compute_rolling_starter_stats)
) -> pd.DataFrame:
    """
    Assembles one feature row per game.

    FanGraphs/Statcast stats are always from the PREVIOUS season (no lookahead). The
    rolling game-log stats (win%, pythag, run rates, SoS) are computed strictly
    pre-game from the current season's game results. Bullpen fatigue (bp_load) uses
    the current season's boxscore pitching lines, also strictly pre-game.
    """
    # Deduplicate game_ids upfront — the MLB Stats API occasionally double-lists
    # makeup/rescheduled games. Keep the row with actual scores (or the later date).
    games = games.copy()
    games["date"] = pd.to_datetime(games["date"], errors="coerce")
    games = (
        games
        .sort_values(["home_score", "date"], na_position="first")
        .drop_duplicates(subset=["game_id"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )

    # ── 1. Rolling game-log stats ────────────────────────────────────────────
    rolling = build_rolling_team_stats(games)
    sos = build_sos(rolling)
    rolling = rolling.merge(sos, on=["game_id", "team_fg"], how="left")

    home_roll = rolling[rolling["is_home"]].add_prefix("h_").rename(columns={"h_game_id": "game_id"})
    away_roll = rolling[~rolling["is_home"]].add_prefix("a_").rename(columns={"a_game_id": "game_id"})

    feat = (
        games[["game_id", "date", "home_team_fg", "away_team_fg", "home_win", "home_sp", "away_sp"]]
        .merge(home_roll[["game_id"] + [c for c in home_roll.columns if c.startswith("h_rolling")]],
               on="game_id", how="left")
        .merge(away_roll[["game_id"] + [c for c in away_roll.columns if c.startswith("a_rolling")]],
               on="game_id", how="left")
    )

    # ── 2. FanGraphs team batting (previous season) ─────────────────────────
    if "Team" in team_bat_prev.columns and "wRC+" in team_bat_prev.columns:
        bat = team_bat_prev[["Team", "wRC+"]].copy()
        bat["wRC+"] = pd.to_numeric(bat["wRC+"], errors="coerce")
        feat = feat.merge(
            bat.rename(columns={"Team": "home_team_fg", "wRC+": "home_wrc_plus"}),
            on="home_team_fg", how="left",
        ).merge(
            bat.rename(columns={"Team": "away_team_fg", "wRC+": "away_wrc_plus"}),
            on="away_team_fg", how="left",
        )

    # ── 3. FanGraphs team pitching — starters (previous season) ─────────────
    if "Team" in team_pit_sp_prev.columns:
        # Select only key+target columns to prevent extra cols polluting feat via merge
        sp_stat_cols = [c for c in ["FIP", "xFIP", "ERA"] if c in team_pit_sp_prev.columns]
        sp = team_pit_sp_prev[["Team"] + sp_stat_cols].copy()
        for c in sp_stat_cols:
            sp[c] = pd.to_numeric(sp[c], errors="coerce")
        sp_rename = {c: f"home_staff_{c.lower().replace('+','plus')}" for c in sp_stat_cols}
        sp_rename["Team"] = "home_team_fg"
        feat = feat.merge(sp.rename(columns=sp_rename), on="home_team_fg", how="left")
        sp_rename_away = {c: f"away_staff_{c.lower().replace('+','plus')}" for c in sp_stat_cols}
        sp_rename_away["Team"] = "away_team_fg"
        feat = feat.merge(sp.rename(columns=sp_rename_away), on="away_team_fg", how="left")

    # ── 4. FanGraphs team pitching — relievers / bullpen (previous season) ──
    if "Team" in team_pit_rp_prev.columns:
        rp_stat_cols = [c for c in ["FIP", "xFIP", "ERA"] if c in team_pit_rp_prev.columns]
        rp = team_pit_rp_prev[["Team"] + rp_stat_cols].copy()
        for c in rp_stat_cols:
            rp[c] = pd.to_numeric(rp[c], errors="coerce")
        rp_rename = {c: f"home_bp_{c.lower().replace('+','plus')}" for c in rp_stat_cols}
        rp_rename["Team"] = "home_team_fg"
        feat = feat.merge(rp.rename(columns=rp_rename), on="home_team_fg", how="left")
        rp_rename_away = {c: f"away_bp_{c.lower().replace('+','plus')}" for c in rp_stat_cols}
        rp_rename_away["Team"] = "away_team_fg"
        feat = feat.merge(rp.rename(columns=rp_rename_away), on="away_team_fg", how="left")

    # ── 5. Individual starter stats (previous season) ────────────────────────
    if "Name" in pitchers_prev.columns and "xFIP" in pitchers_prev.columns:
        # Filter to SP *first*, then dedup by name — prevents the dedup overwriting the SP filter
        sp_only = pitchers_prev[pitchers_prev["is_sp"].fillna(False)].copy()
        if "Season" in sp_only.columns:
            sp_only = sp_only.sort_values("Season").groupby("Name").last().reset_index()

        pit_stat_cols = [c for c in ["FIP", "xFIP", "ERA"] if c in sp_only.columns]
        pit = sp_only[["Name"] + pit_stat_cols].copy()
        for c in pit_stat_cols:
            pit[c] = pd.to_numeric(pit[c], errors="coerce")

        feat = feat.merge(
            pit.rename(columns={"Name": "home_sp", "FIP": "home_sp_fip",
                                 "xFIP": "home_sp_xfip", "ERA": "home_sp_era"}),
            on="home_sp", how="left",
        ).merge(
            pit.rename(columns={"Name": "away_sp", "FIP": "away_sp_fip",
                                 "xFIP": "away_sp_xfip", "ERA": "away_sp_era"}),
            on="away_sp", how="left",
        )

    # ── 5b. Rolling starter form (current season, pre-game) ─────────────────
    # Captures slumps/hot-streaks missed by the prev-season FIP in section 5.
    # Joined on (game_id, pitcher_name) so only the actual scheduled starter is matched.
    if sp_rolling_stats is not None and not sp_rolling_stats.empty:
        sr_cols = [c for c in ["sp_era_last5", "sp_ip_last5"] if c in sp_rolling_stats.columns]
        if sr_cols and "pitcher_name" in sp_rolling_stats.columns:
            sr = sp_rolling_stats[["game_id", "pitcher_name"] + sr_cols].copy()

            # For unplayed games (today/future), the game_id won't exist in sr because
            # the boxscore hasn't happened yet. Project each scheduled starter's most
            # recent rolling stats forward to that game_id so the join succeeds.
            unplayed = feat[feat["home_win"].isna()][["game_id", "home_sp", "away_sp"]].dropna(subset=["game_id"])
            if not unplayed.empty:
                latest = sr.sort_values("game_id").groupby("pitcher_name").last().reset_index()
                latest_map = latest.set_index("pitcher_name")[sr_cols].to_dict("index")
                proj_rows = []
                for _, ug in unplayed.iterrows():
                    for sp_col in ["home_sp", "away_sp"]:
                        name = ug[sp_col]
                        if pd.notna(name) and name in latest_map:
                            row = {"game_id": ug["game_id"], "pitcher_name": name}
                            row.update(latest_map[name])
                            proj_rows.append(row)
                if proj_rows:
                    sr = pd.concat([sr, pd.DataFrame(proj_rows)], ignore_index=True)

            feat = feat.merge(
                sr.rename(columns={"pitcher_name": "home_sp",
                                    **{c: f"home_{c}" for c in sr_cols}}),
                on=["game_id", "home_sp"], how="left",
            ).merge(
                sr.rename(columns={"pitcher_name": "away_sp",
                                    **{c: f"away_{c}" for c in sr_cols}}),
                on=["game_id", "away_sp"], how="left",
            )

    # ── 6. Leverage-weighted bullpen quality (previous season) ────────────────────
    if bp_quality_prev is not None and not bp_quality_prev.empty and "Team" in bp_quality_prev.columns:
        # Try new leverage-weighted columns first, fall back to old IP-weighted
        bpq_cols = [c for c in ["bp_leverage_wtd_xfip", "bp_leverage_wtd_fip", "bp_ip_wtd_xfip", "bp_ip_wtd_fip"]
                    if c in bp_quality_prev.columns]
        if bpq_cols:
            bpq = bp_quality_prev[["Team"] + bpq_cols].copy()
            # Create consistent column names
            rename_home = {"Team": "home_team_fg"}
            rename_away = {"Team": "away_team_fg"}
            for col in bpq_cols:
                if "leverage" in col:
                    rename_home[col] = col.replace("bp_leverage", "home_bp_leverage")
                    rename_away[col] = col.replace("bp_leverage", "away_bp_leverage")
                else:
                    rename_home[col] = col.replace("bp_ip", "home_bp_ip")
                    rename_away[col] = col.replace("bp_ip", "away_bp_ip")

            feat = feat.merge(bpq.rename(columns=rename_home), on="home_team_fg", how="left")
            feat = feat.merge(bpq.rename(columns=rename_away), on="away_team_fg", how="left")

    # ── 7. Bullpen fatigue from boxscores (current season, pre-game) ─────────
    if bp_load is not None and not bp_load.empty:
        load_cols = [c for c in ["bp_ip_1d", "bp_ip_2d", "bp_ip_3d", "sp_ip_avg_10g"]
                     if c in bp_load.columns]
        if load_cols:
            home_load = (
                bp_load[["game_id", "team_fg"] + load_cols]
                .rename(columns={"team_fg": "home_team_fg",
                                  **{c: f"home_{c}" for c in load_cols}})
            )
            away_load = (
                bp_load[["game_id", "team_fg"] + load_cols]
                .rename(columns={"team_fg": "away_team_fg",
                                  **{c: f"away_{c}" for c in load_cols}})
            )
            feat = (
                feat
                .merge(home_load, on=["game_id", "home_team_fg"], how="left")
                .merge(away_load, on=["game_id", "away_team_fg"], how="left")
            )

    # ── 8. Statcast team batting (previous season) ───────────────────────────
    if sc_bat_prev is not None and not sc_bat_prev.empty and "team_fg" in sc_bat_prev.columns:
        sc_bat_cols = [c for c in ["xwoba_gap", "brl_pct", "hard_hit_pct", "avg_ev"]
                       if c in sc_bat_prev.columns]
        if sc_bat_cols:
            scb = sc_bat_prev[["team_fg"] + sc_bat_cols].copy()
            feat = feat.merge(
                scb.rename(columns={"team_fg": "home_team_fg",
                                     **{c: f"home_{c}" for c in sc_bat_cols}}),
                on="home_team_fg", how="left",
            ).merge(
                scb.rename(columns={"team_fg": "away_team_fg",
                                     **{c: f"away_{c}" for c in sc_bat_cols}}),
                on="away_team_fg", how="left",
            )

    # ── 9. Statcast team pitching (previous season) ──────────────────────────
    if sc_pit_prev is not None and not sc_pit_prev.empty and "team_fg" in sc_pit_prev.columns:
        sc_pit_cols = [c for c in ["pit_xwoba_gap", "pit_brl_pct_allowed", "pit_hard_hit_allowed"]
                       if c in sc_pit_prev.columns]
        if sc_pit_cols:
            scp = sc_pit_prev[["team_fg"] + sc_pit_cols].copy()
            feat = feat.merge(
                scp.rename(columns={"team_fg": "home_team_fg",
                                     **{c: f"home_{c}" for c in sc_pit_cols}}),
                on="home_team_fg", how="left",
            ).merge(
                scp.rename(columns={"team_fg": "away_team_fg",
                                     **{c: f"away_{c}" for c in sc_pit_cols}}),
                on="away_team_fg", how="left",
            )

    # ── 10. Park factors ─────────────────────────────────────────────────────
    feat["park_run_factor"] = feat["home_team_fg"].map(get_park_factor).fillna(1.0)

    # ── 11. Calendar features ─────────────────────────────────────────────────
    feat["game_month"] = pd.to_datetime(feat["date"]).dt.month

    # Final dedup — any 1:many merge artifact (e.g. bp_load or SP join) is removed here
    feat = feat.drop_duplicates(subset=["game_id"], keep="last").reset_index(drop=True)

    return feat


FEATURE_COLS = [
    # ── Rolling game-log (no leakage) ────────────────────────────────────────
    "h_rolling_win_pct", "h_rolling_pythag", "h_rolling_rd_pg",
    "h_rolling_rs_pg",   "h_rolling_ra_pg",
    "h_rolling_home_rs_pg", "h_rolling_home_ra_pg",
    "a_rolling_win_pct", "a_rolling_pythag", "a_rolling_rd_pg",
    "a_rolling_rs_pg",   "a_rolling_ra_pg",
    "a_rolling_road_rs_pg", "a_rolling_road_ra_pg",
    "h_rolling_sos",     "a_rolling_sos",
    # Note: rolling_win_pct_last20 and rolling_rd_pg_last20 are computed in the feature
    # matrix (available for quality-gate filters in backtest.py / edge_detector.py) but
    # intentionally NOT in FEATURE_COLS — testing showed they add noise vs the
    # season-to-date rolling stats when used as direct model features.
    # ── FanGraphs batting (prev season) ──────────────────────────────────────
    "home_wrc_plus",     "away_wrc_plus",
    # ── FanGraphs pitching staff (prev season) ───────────────────────────────
    "home_staff_xfip",   "away_staff_xfip",
    "home_staff_fip",    "away_staff_fip",
    # ── FanGraphs bullpen aggregate (prev season, fallback) ──────────────────
    "home_bp_xfip",      "away_bp_xfip",
    "home_bp_fip",       "away_bp_fip",
    "home_bp_era",       "away_bp_era",
    # ── Leverage-weighted bullpen quality (prev season) ──────────────────────
    "home_bp_leverage_wtd_xfip", "away_bp_leverage_wtd_xfip",
    "home_bp_leverage_wtd_fip",  "away_bp_leverage_wtd_fip",
    # ── Bullpen fatigue — reliever IP in last 1/2/3 days (current season) ────
    "home_bp_ip_1d",  "away_bp_ip_1d",
    "home_bp_ip_2d",  "away_bp_ip_2d",
    "home_bp_ip_3d",  "away_bp_ip_3d",
    # ── Starter depth — rolling avg IP/start (current season) ────────────────
    "home_sp_ip_avg_10g", "away_sp_ip_avg_10g",
    # ── Starting pitcher quality (prev season) ────────────────────────────────
    "home_sp_xfip",      "away_sp_xfip",
    "home_sp_fip",       "away_sp_fip",
    # ── Starter recent form (current season, rolling last 5 starts) ──────────
    "home_sp_era_last5", "away_sp_era_last5",
    "home_sp_ip_last5",  "away_sp_ip_last5",
    # ── Statcast team batting (prev season) ──────────────────────────────────
    "home_xwoba_gap",    "away_xwoba_gap",        # regression signal
    # ── Statcast team pitching (prev season) ─────────────────────────────────
    "home_pit_xwoba_gap",         "away_pit_xwoba_gap",
    # ── Park + calendar ───────────────────────────────────────────────────────
    "park_run_factor",   "game_month",
    # Sportsbook consensus (closing line, 2021+; NaN handled by XGBoost natively)
    # Adding market signal directly addresses probability range compression.
    "market_home_prob",
]

# Features where NaN is semantically meaningful and should NOT be filled with median.
# XGBoost routes NaN samples to a learned default direction (sparsity-aware splits),
# so keeping NaN here lets the model distinguish "pre-2021 (no odds data)" from
# "2021+ game with real market probability".
NULLABLE_FEATURE_COLS = {"market_home_prob"}
