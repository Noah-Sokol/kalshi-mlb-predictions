"""
Bullpen quality and fatigue features.

aggregate_team_bp_quality()  — IP-weighted xFIP/FIP from individual reliever stats.
                               Better than the FanGraphs team RP aggregate because a
                               50-IP closer counts 50x more than a 1-IP call-up.

compute_rolling_bp_load()    — How many innings the bullpen threw in the last 1/2/3
                               calendar days (fatigue) and how deep starters typically
                               go (affects expected bullpen demand). Computed strictly
                               pre-game from boxscore pitching lines.
"""
import numpy as np
import pandas as pd


def aggregate_team_bp_quality(pitchers_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute leverage-weighted bullpen quality per team from individual pitcher stats.

    Weights relievers by Games Finished (GF) to emphasize high-leverage arms.
    Closers and late-inning relievers get more weight than mop-up guys.

    pitchers_df: from fangraphs.fetch_pitcher_stats(); needs Team, IP, xFIP, FIP, GF, is_sp.

    Returns one row per team:
      Team, bp_ip_total, bp_leverage_wtd_xfip, bp_leverage_wtd_fip, bp_n_relievers
    """
    _empty = pd.DataFrame(columns=["Team", "bp_ip_total", "bp_leverage_wtd_xfip",
                                    "bp_leverage_wtd_fip", "bp_n_relievers"])
    if pitchers_df is None or pitchers_df.empty:
        return _empty

    rp = pitchers_df[pitchers_df["is_sp"].fillna(True) == False].copy()
    if rp.empty:
        return _empty

    # Resolve team column
    team_col = next((c for c in ["Team", "teamName", "team"] if c in rp.columns), None)
    if team_col is None or "IP" not in rp.columns:
        return _empty
    if team_col != "Team":
        rp = rp.rename(columns={team_col: "Team"})

    for col in ["IP", "xFIP", "FIP", "GF"]:
        if col in rp.columns:
            rp[col] = pd.to_numeric(rp[col], errors="coerce")

    rp = rp.dropna(subset=["Team", "IP"])
    rp = rp[pd.to_numeric(rp["IP"], errors="coerce") > 0]

    # Create leverage weight: IP * (1 + GF/20)
    # Closer with 30 GF gets 2.5x weight vs mop-up guy with 0 GF
    if "GF" in rp.columns:
        rp["GF"] = rp["GF"].fillna(0)
        rp["leverage_weight"] = rp["IP"] * (1 + rp["GF"] / 20.0)
    else:
        # Fallback to pure IP weighting if GF not available
        rp["leverage_weight"] = rp["IP"]

    rows = []
    for team, grp in rp.groupby("Team"):
        ip_total = grp["IP"].sum()

        def _leverage_wtd(col):
            if col not in grp.columns:
                return np.nan
            valid = grp.dropna(subset=[col])
            if valid.empty:
                return np.nan
            return (valid[col] * valid["leverage_weight"]).sum() / valid["leverage_weight"].sum()

        rows.append({
            "Team": team,
            "bp_ip_total": round(ip_total, 1),
            "bp_leverage_wtd_xfip": round(_leverage_wtd("xFIP"), 3),
            "bp_leverage_wtd_fip":  round(_leverage_wtd("FIP"),  3),
            "bp_n_relievers": len(grp),
        })

    return pd.DataFrame(rows)


def compute_rolling_starter_stats(pitching_lines: pd.DataFrame, n_starts: int = 5) -> pd.DataFrame:
    """
    Compute each starter's recent-form metrics from their last N starts (strictly pre-game).

    Captures slumps and hot streaks that season-average FIP misses.

    Parameters:
      pitching_lines: from mlb_boxscores.fetch_season_pitching_lines()
      n_starts: rolling window of starts (default 5 ≈ one calendar turn)

    Returns one row per (game_id, pitcher_name, team_fg):
      sp_era_last5  — ERA in last N starts
      sp_ip_last5   — avg IP per start over last N starts
      sp_starts     — total starts seen before this game (sample-size signal)
    """
    _empty = pd.DataFrame(columns=["game_id", "pitcher_name", "team_fg",
                                    "sp_era_last5", "sp_ip_last5", "sp_starts"])
    if pitching_lines is None or pitching_lines.empty:
        return _empty

    sp = pitching_lines[pitching_lines["is_starter"]].copy()
    if sp.empty:
        return _empty

    sp["date"] = pd.to_datetime(sp["date"])

    results = []
    for pitcher, grp in sp.groupby("pitcher_name"):
        grp = grp.sort_values("date").reset_index(drop=True)

        cum_er = grp["er"].cumsum().shift(1).fillna(0.0)
        cum_ip = grp["ip"].cumsum().shift(1).fillna(0.0)
        n_prior = grp["ip"].shift(1).expanding().count().fillna(0.0)

        # Rolling last-N window (shifted so each row uses only prior starts)
        roll_er = grp["er"].shift(1).rolling(n_starts, min_periods=1).sum()
        roll_ip = grp["ip"].shift(1).rolling(n_starts, min_periods=1).sum()
        roll_n  = grp["ip"].shift(1).rolling(n_starts, min_periods=1).count()

        for i, row in grp.iterrows():
            r_er = roll_er.iloc[i]
            r_ip = roll_ip.iloc[i]
            r_n  = roll_n.iloc[i]
            era = (r_er / r_ip * 9) if (r_ip and r_ip > 0) else np.nan
            avg_ip = (r_ip / r_n) if (r_n and r_n > 0) else np.nan

            results.append({
                "game_id":      row["game_id"],
                "pitcher_name": pitcher,
                "team_fg":      row["team_fg"],
                "sp_era_last5": round(era, 3) if era is not None and not np.isnan(era) else np.nan,
                "sp_ip_last5":  round(avg_ip, 2) if avg_ip is not None and not np.isnan(avg_ip) else np.nan,
                "sp_starts":    int(n_prior.iloc[i]),
            })

    return pd.DataFrame(results)


def compute_rolling_bp_load(pitching_lines: pd.DataFrame) -> pd.DataFrame:
    """
    Compute per-team pre-game bullpen fatigue and starter depth metrics.

    For each (game_id, team_fg):
      bp_ip_1d  — reliever IP the team threw in the previous 1 calendar day
      bp_ip_2d  — reliever IP in the previous 2 calendar days
      bp_ip_3d  — reliever IP in the previous 3 calendar days
      sp_ip_avg_10g — rolling 10-start average starter IP (how deep starters go)

    All stats are strictly pre-game (no same-day lookahead).

    Parameters:
      pitching_lines: from mlb_boxscores.fetch_season_pitching_lines()
        Needs: game_id, date, team_fg, is_starter, ip
    """
    _empty = pd.DataFrame(columns=["game_id", "team_fg",
                                    "bp_ip_1d", "bp_ip_2d", "bp_ip_3d", "sp_ip_avg_10g"])
    if pitching_lines is None or pitching_lines.empty:
        return _empty

    lines = pitching_lines.copy()
    lines["date"] = pd.to_datetime(lines["date"])
    lines["date_only"] = lines["date"].dt.normalize()

    # Daily reliever IP per team (for fatigue lookup)
    rp_lines = lines[~lines["is_starter"]]
    daily_bp = (
        rp_lines.groupby(["team_fg", "date_only"])["ip"]
        .sum()
        .reset_index()
        .rename(columns={"date_only": "date"})
    )

    # SP IP per game per team (for rolling depth average)
    sp_lines = lines[lines["is_starter"]]
    game_sp = (
        sp_lines.groupby(["team_fg", "game_id", "date_only"])["ip"]
        .sum()
        .reset_index()
        .rename(columns={"date_only": "date"})
        .sort_values(["team_fg", "date"])
    )

    # Unique (game_id, date, team_fg) rows — one row per team per game
    team_games = (
        lines[["game_id", "date_only", "team_fg"]]
        .rename(columns={"date_only": "date"})
        .drop_duplicates()
        .sort_values(["team_fg", "date"])
    )

    results = []
    for team, tg in team_games.groupby("team_fg"):
        tg = tg.sort_values("date").reset_index(drop=True)

        # Dict of date → daily BP IP for fast O(1) lookups
        team_daily = (
            daily_bp[daily_bp["team_fg"] == team]
            .set_index("date")["ip"]
            .to_dict()
        )

        # Rolling SP IP: expanding window shifted by 1 to avoid lookahead
        team_sp = game_sp[game_sp["team_fg"] == team].sort_values("date")
        sp_rolling = (
            team_sp["ip"]
            .rolling(window=10, min_periods=1)
            .mean()
            .shift(1)               # strictly pre-game
            .values
        )
        sp_game_ids = team_sp["game_id"].tolist()
        sp_dates = team_sp["date"].tolist()
        sp_ip_map = dict(zip(sp_game_ids, sp_rolling))

        for _, row in tg.iterrows():
            d = row["date"]
            bp_1d = float(team_daily.get(d - pd.Timedelta(days=1), 0.0) or 0.0)
            bp_2d = bp_1d + float(team_daily.get(d - pd.Timedelta(days=2), 0.0) or 0.0)
            bp_3d = bp_2d + float(team_daily.get(d - pd.Timedelta(days=3), 0.0) or 0.0)
            sp_avg = sp_ip_map.get(row["game_id"], np.nan)

            results.append({
                "game_id":       row["game_id"],
                "team_fg":       team,
                "bp_ip_1d":      round(bp_1d, 2),
                "bp_ip_2d":      round(bp_2d, 2),
                "bp_ip_3d":      round(bp_3d, 2),
                "sp_ip_avg_10g": round(float(sp_avg), 2) if not np.isnan(sp_avg) else np.nan,
            })

    return pd.DataFrame(results)
