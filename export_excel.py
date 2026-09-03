"""
Export bets_log.csv to a styled Excel workbook.

Usage:
    python export_excel.py                          # saves to data/picks_tracker.xlsx
    python export_excel.py --out my_tracker.xlsx
"""
import argparse
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import openpyxl
from openpyxl.styles import (
    PatternFill, Font, Alignment, Border, Side, numbers
)
from openpyxl.utils import get_column_letter

import os; os.chdir(Path(__file__).parent)

LOG_PATH        = Path("data/bets_log.csv")
OUT_PATH        = Path("data/picks_tracker.xlsx")
TRACKING_START  = pd.Timestamp("2026-08-06")  # Aug 5th excluded — no bets placed yet

# ── Palette ──────────────────────────────────────────────────────────────────
C_YELLOW    = "BDD7EE"   # on-model bet rows — soft cornflower blue
C_YELLOW_LT = "D1E3F3"   # off-script bet rows — slightly lighter blue
C_TAN       = "F5DEB3"   # no-bet / info rows
C_NAVY      = "1A3A4A"   # header background — dark teal
C_WHITE     = "FFFFFF"
C_GREEN     = "C6EFCE"   # win
C_RED       = "FFC7CE"   # loss
C_BLUE_LT   = "DEEAF1"   # stats panel background
C_HEADER_TXT= "FFFFFF"
C_DARK      = "000000"

def _fill(hex_color):
    return PatternFill("solid", fgColor=hex_color)

def _font(bold=False, color=C_DARK, size=10, italic=False):
    return Font(bold=bold, color=color, size=size, italic=italic)

def _border(style="thin"):
    s = Side(style=style)
    return Border(left=s, right=s, top=s, bottom=s)

def _center(wrap=False):
    return Alignment(horizontal="center", vertical="center", wrap_text=wrap)

def _right():
    return Alignment(horizontal="right", vertical="center")


def load_log() -> pd.DataFrame:
    if not LOG_PATH.exists():
        return pd.DataFrame()
    df = pd.read_csv(LOG_PATH)
    df["date"] = pd.to_datetime(df["date"])
    for col in ["model_home_prob", "market_home_prob", "edge",
                "theoretical_pnl", "actual_pnl", "suggested_dollars", "actual_dollars"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[df["date"] >= TRACKING_START]


def write_picks_sheet(ws, df: pd.DataFrame, filtered: bool = False):
    year = date.today().year
    ws.title = f"{year} Filtered" if filtered else f"{year} All Bets"

    # ── Title row ────────────────────────────────────────────────────────────
    ws.merge_cells("A1:O1")
    title_cell = ws["A1"]
    title_cell.value = f"SPORTS PICKS TRACKER — {year}"
    title_cell.font      = _font(bold=True, color=C_WHITE, size=14)
    title_cell.fill      = _fill(C_NAVY)
    title_cell.alignment = _center()
    ws.row_dimensions[1].height = 24

    # ── Sub-header description ────────────────────────────────────────────────
    ws.merge_cells("A2:O2")
    desc = ws["A2"]
    if filtered:
        desc.value = ("FILTERED PICKS - Only bets passing quality filters (market >= 40%)  |  "
                      "Theoretical: what you'd make  |  Actual P&L: what you made on bets you placed")
    else:
        desc.value = ("ALL MODEL RECOMMENDATIONS - Includes filtered bets  |  "
                      "Theoretical: what you'd make taking every suggestion  |  Actual P&L: what you made")
    desc.font      = _font(italic=True, size=8)
    desc.fill      = _fill("D9E1F2")
    desc.alignment = _center(wrap=True)
    ws.row_dimensions[2].height = 18

    # ── Column headers ───────────────────────────────────────────────────────
    headers = [
        "#", "Date", "Matchup", "Pick", "Your Edge",
        "Market %", "Model %", "Recommended ($)", "Wagered ($)",
        "Status", "Theo P&L ($)", "Actual P&L ($)", "Notes",
        "Week #", "Month",
    ]
    for col_i, h in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col_i, value=h)
        cell.font      = _font(bold=True, color=C_WHITE, size=10)
        cell.fill      = _fill(C_NAVY)
        cell.alignment = _center(wrap=True)
        cell.border    = _border()
    ws.row_dimensions[3].height = 20

    # ── Column widths ─────────────────────────────────────────────────────────
    widths = [5, 12, 18, 8, 8, 10, 10, 10, 10, 8, 12, 12, 24, 8, 10]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # ── Data rows ─────────────────────────────────────────────────────────────
    rec_mask = df["recommended_side"].isin(["YES", "NO", "home", "away"])
    act_mask = df["actual_side"].isin(["home", "away", "YES", "NO"])
    bets = df[rec_mask | act_mask].copy()
    bets["_off_script"] = (
        ~bets["recommended_side"].isin(["YES", "NO", "home", "away"]) &
        bets["actual_side"].isin(["home", "away", "YES", "NO"])
    )

    # Apply filters if requested
    if filtered:
        MIN_BET_MKT_PROB = 0.40
        def passes_filter(row):
            # Use actual_side for off-script bets; recommended_side for on-model bets
            side = row["recommended_side"] if row["recommended_side"] in ("YES", "home", "NO", "away") else row.get("actual_side", "")
            if side in ("YES", "home"):
                bet_team_prob = row["market_home_prob"]
            else:
                bet_team_prob = 1 - row["market_home_prob"]
            # Round to nearest 1% so 39.9% is treated as 40% and passes the gate
            return round(bet_team_prob, 2) >= MIN_BET_MKT_PROB

        bets = bets[bets.apply(passes_filter, axis=1)].copy()

    bets = bets.sort_values("date", ascending=False).reset_index(drop=True)

    for row_i, (_, row) in enumerate(bets.iterrows(), 1):
        excel_row = row_i + 3

        # Matchup string
        home = row.get("home_team_fg", "?")
        away = row.get("away_team_fg", "?")
        matchup = f"{away} @ {home}"

        # Pick team (off-script bets use actual_side since recommended is "none")
        side = row.get("recommended_side", "")
        off_script = row.get("_off_script", False)
        if side in ("YES", "home"):
            pick = home
        elif side in ("NO", "away"):
            pick = away
        elif off_script and row.get("actual_side") in ("home", "YES"):
            pick = f"{home}*"
        elif off_script and row.get("actual_side") in ("away", "NO"):
            pick = f"{away}*"
        else:
            pick = "—"

        # Status
        hw = row.get("home_win")
        if pd.isna(hw):
            status = "—"
        elif (pick == home and hw == 1) or (pick == away and hw == 0):
            status = "W"
        else:
            status = "L"

        week_num  = row["date"].isocalendar()[1] if pd.notna(row["date"]) else ""
        month_str = row["date"].strftime("%b") if pd.notna(row["date"]) else ""

        values = [
            row_i,
            row["date"].strftime("%Y-%m-%d") if pd.notna(row["date"]) else "",
            matchup,
            pick,
            # Show edge from the bettor's perspective (positive = favorable for the bet placed)
        (f"{row['edge']:+.1%}" if (row.get('recommended_side') in ('YES','home') or
             (off_script and row.get('actual_side') in ('home','YES')))
         else f"{-row['edge']:+.1%}") if pd.notna(row.get('edge')) else "",
            f"{row['market_home_prob']:.1%}" if pd.notna(row.get("market_home_prob")) else "",
            f"{row['model_home_prob']:.1%}" if pd.notna(row.get("model_home_prob")) else "",
            row.get("suggested_dollars") or "",                           # col 8: Recommended
            row.get("actual_dollars")    if pd.notna(row.get("actual_dollars")) else "",  # col 9: Wagered
            status,
            row.get("theoretical_pnl") if pd.notna(row.get("theoretical_pnl")) else "",
            row.get("actual_pnl")      if pd.notna(row.get("actual_pnl"))      else "",
            row.get("notes", "") or "",
            week_num,
            month_str,
        ]

        # Row background: yellow for on-model bets, tan for off-script bets
        row_fill = _fill(C_YELLOW_LT) if row.get("_off_script") else _fill(C_YELLOW)
        for col_i, val in enumerate(values, 1):
            cell = ws.cell(row=excel_row, column=col_i, value=val)
            cell.fill      = row_fill
            cell.border    = _border()
            cell.alignment = _center() if col_i not in (3, 13) else Alignment(
                horizontal="left", vertical="center", wrap_text=(col_i == 13)
            )
            cell.font = _font(size=10)

        # Colour the status cell
        status_cell = ws.cell(row=excel_row, column=10)
        if status == "W":
            status_cell.fill = _fill(C_GREEN)
            status_cell.font = _font(bold=True, size=10)
        elif status == "L":
            status_cell.fill = _fill(C_RED)
            status_cell.font = _font(bold=True, size=10)

        # Format Recommended and Wagered columns as currency
        for _money_col in (8, 9):
            _mc = ws.cell(row=excel_row, column=_money_col)
            try:
                if _mc.value and float(_mc.value or 0) > 0:
                    _mc.number_format = '$#,##0.00'
            except (TypeError, ValueError):
                pass

        # Colour P&L cells
        for col_i in (11, 12):
            cell = ws.cell(row=excel_row, column=col_i)
            try:
                v = float(cell.value) if cell.value != "" and cell.value is not None else None
            except (TypeError, ValueError):
                v = None
            if v is not None:
                cell.number_format = '$#,##0.00;[Red]-$#,##0.00'
                cell.value = v
                if v > 0:
                    cell.fill = _fill(C_GREEN)
                elif v < 0:
                    cell.fill = _fill(C_RED)

    return bets


def write_stats_panel(ws, df: pd.DataFrame, start_col: int = 17):
    """Write the Season Totals / Monthly / Weekly stats panel."""
    sc = start_col  # stats start column

    def stat_header(row, col, text, width_cols=2):
        cell = ws.cell(row=row, column=col, value=text)
        cell.font      = _font(bold=True, color=C_WHITE, size=10)
        cell.fill      = _fill(C_NAVY)
        cell.alignment = _center()
        cell.border    = _border()
        if width_cols > 1:
            ws.merge_cells(
                start_row=row, start_column=col,
                end_row=row, end_column=col + width_cols - 1
            )

    def stat_row(row, col, label, value, value_fmt=None):
        lc = ws.cell(row=row, column=col,     value=label)
        vc = ws.cell(row=row, column=col + 1, value=value)
        lc.font = _font(bold=True, size=9); lc.fill = _fill(C_BLUE_LT)
        vc.font = _font(size=9);            vc.fill = _fill(C_BLUE_LT)
        lc.alignment = Alignment(horizontal="left",  vertical="center")
        vc.alignment = Alignment(horizontal="right", vertical="center")
        lc.border = _border(); vc.border = _border()
        if value_fmt:
            vc.number_format = value_fmt
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value > 0 and "P&L" in label or "Profit" in label:
                vc.fill = _fill(C_GREEN)
            elif isinstance(value, float) and value < 0:
                vc.fill = _fill(C_RED)

    bets = df[df["recommended_side"].isin(["YES", "NO", "home", "away"])].copy()
    # For actual P&L include off-script bets placed outside of recommendations
    all_placed = df[
        df["recommended_side"].isin(["YES", "NO", "home", "away"]) |
        df["actual_side"].isin(["home", "away", "YES", "NO"])
    ].copy()
    settled = bets.dropna(subset=["home_win"])

    # Theoretical stats (all model suggestions)
    theo_wins   = int((settled["theoretical_pnl"].fillna(0) > 0).sum())
    theo_losses = int((settled["theoretical_pnl"].fillna(0) < 0).sum())
    theo_total  = theo_wins + theo_losses
    theo_win_pct = theo_wins / theo_total if theo_total > 0 else 0
    total_staked = float(bets["suggested_dollars"].fillna(0).sum())
    total_theo   = float(settled["theoretical_pnl"].fillna(0).sum())
    theo_roi   = total_theo / total_staked if total_staked > 0 else 0.0

    # Actual stats (all bets placed, including off-script)
    all_placed_settled = all_placed.dropna(subset=["home_win"])
    actual_settled = all_placed_settled[all_placed_settled["actual_pnl"].notna()]
    actual_wins   = int((actual_settled["actual_pnl"].fillna(0) > 0).sum())
    actual_losses = int((actual_settled["actual_pnl"].fillna(0) < 0).sum())
    actual_total  = actual_wins + actual_losses
    actual_win_pct = actual_wins / actual_total if actual_total > 0 else 0
    total_actual = float(all_placed["actual_pnl"].fillna(0).sum())
    actual_staked = float(all_placed["actual_dollars"].fillna(0).sum())
    actual_roi = total_actual / actual_staked if actual_staked > 0 else 0.0

    def _pnl_stat(row, col, label, value, fmt='$#,##0.00'):
        lc = ws.cell(row=row, column=col,     value=label)
        vc = ws.cell(row=row, column=col + 1, value=value)
        lc.font = _font(bold=True, size=9); lc.fill = _fill(C_BLUE_LT)
        lc.border = _border(); lc.alignment = Alignment(horizontal="left", vertical="center")
        vc.font = _font(bold=True, size=10)
        vc.border = _border(); vc.alignment = Alignment(horizontal="right", vertical="center")
        vc.number_format = fmt
        if isinstance(value, (int, float)):
            if value > 0:   vc.fill = _fill(C_GREEN)
            elif value < 0: vc.fill = _fill(C_RED)
            else:           vc.fill = _fill(C_BLUE_LT)

    # SEASON TOTALS
    r = 3
    stat_header(r, sc, "SEASON TOTALS", 2); r += 1
    _pnl_stat(r, sc, "Actual P&L",    total_actual,  '$+#,##0.00;$-#,##0.00'); r += 1
    _pnl_stat(r, sc, "Actual ROI",    actual_roi,    '+0.0%;-0.0%'); r += 1
    stat_row(r, sc, "Actual Win %",   actual_win_pct, "0.0%"); r += 1
    stat_row(r, sc, "Actual W-L",     f"{actual_wins}-{actual_losses}" if actual_total > 0 else "0-0"); r += 1
    r += 1  # Blank row separator
    _pnl_stat(r, sc, "Theo P&L",      total_theo,    '$+#,##0.00;$-#,##0.00'); r += 1
    _pnl_stat(r, sc, "Theo ROI",      theo_roi,      '+0.0%;-0.0%'); r += 1
    stat_row(r, sc, "Theo Win %",     theo_win_pct,  "0.0%"); r += 1
    stat_row(r, sc, "Theo W-L",       f"{theo_wins}-{theo_losses}" if theo_total > 0 else "0-0"); r += 1
    r += 1  # Blank row separator
    stat_row(r, sc, "Pending",        int(len(bets) - len(settled))); r += 2

    # MONTHLY P&L  (actual bets only — rows where a real wager was recorded)
    stat_header(r, sc, "MONTHLY P&L (Actual)", 2); r += 1
    months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    actually_placed = all_placed[
        all_placed["actual_side"].isin(["home", "away", "YES", "NO"]) &
        all_placed["actual_pnl"].notna()
    ].copy()
    if not actually_placed.empty:
        actually_placed["month_str"] = actually_placed["date"].dt.strftime("%b")
        monthly = actually_placed.groupby("month_str")["actual_pnl"].sum()
    else:
        monthly = pd.Series(dtype=float)
    for m in months:
        val = float(monthly.get(m, 0))
        lc = ws.cell(row=r, column=sc,     value=m)
        vc = ws.cell(row=r, column=sc + 1, value=val)
        lc.font = _font(size=9);  lc.fill = _fill(C_BLUE_LT)
        lc.border = _border();    lc.alignment = Alignment(horizontal="left", vertical="center")
        vc.font = _font(size=9);  vc.number_format = '$#,##0.00'
        vc.border = _border();    vc.alignment = Alignment(horizontal="right", vertical="center")
        if val > 0:   vc.fill = _fill(C_GREEN)
        elif val < 0: vc.fill = _fill(C_RED)
        else:         vc.fill = _fill(C_BLUE_LT)
        r += 1
    r += 1

    # WEEKLY P&L
    stat_header(r, sc, "WEEKLY P&L", 2); r += 1
    if not settled.empty:
        settled_copy = settled.copy()
        settled_copy["week"] = settled_copy["date"].dt.isocalendar().week.astype(int)
        weekly = settled_copy.groupby("week")["theoretical_pnl"].sum().sort_index()
    else:
        weekly = pd.Series(dtype=float)
    for week_num, val in weekly.items():
        lc = ws.cell(row=r, column=sc,     value=f"Week {week_num}")
        vc = ws.cell(row=r, column=sc + 1, value=float(val))
        lc.font = _font(size=9);  lc.fill = _fill(C_BLUE_LT)
        lc.border = _border();    lc.alignment = Alignment(horizontal="left", vertical="center")
        vc.font = _font(size=9);  vc.number_format = '$#,##0.00'
        vc.border = _border();    vc.alignment = Alignment(horizontal="right", vertical="center")
        if val > 0:   vc.fill = _fill(C_GREEN)
        elif val < 0: vc.fill = _fill(C_RED)
        else:         vc.fill = _fill(C_BLUE_LT)
        r += 1

    # Column widths for stats panel
    ws.column_dimensions[get_column_letter(sc)].width     = 14
    ws.column_dimensions[get_column_letter(sc + 1)].width = 12


def write_running_pnl(ws, bets: pd.DataFrame, start_col: int = 20, actual_df: pd.DataFrame = None):
    """
    Daily P&L table — summarizes by day instead of individual bets.
    Shows: Date | Daily Actual P&L | Cumulative Actual | Daily Theo | Cumulative Theo | Actual W-L | Theo W-L

    actual_df: full unfiltered log — always use ALL placed bets for actual P&L,
    regardless of which filtered view bets comes from. Prevents the market_prob
    quality gate from hiding real wins/losses from the summary table.
    """
    sc = start_col

    # Column widths
    ws.column_dimensions[get_column_letter(sc)].width     = 12  # Date
    ws.column_dimensions[get_column_letter(sc + 1)].width = 12  # Daily Actual
    ws.column_dimensions[get_column_letter(sc + 2)].width = 14  # Cumulative Actual
    ws.column_dimensions[get_column_letter(sc + 3)].width = 12  # Daily Theo
    ws.column_dimensions[get_column_letter(sc + 4)].width = 14  # Cumulative Theo
    ws.column_dimensions[get_column_letter(sc + 5)].width = 10  # Actual W-L
    ws.column_dimensions[get_column_letter(sc + 6)].width = 10  # Theo W-L

    # Headers
    headers = ["Date", "Daily Actual ($)", "Cum. Actual ($)", "Daily Theo ($)", "Cum. Theo ($)", "Actual W-L", "Theo W-L"]
    for i, hdr_text in enumerate(headers):
        hdr = ws.cell(row=3, column=sc + i, value=hdr_text)
        hdr.font = _font(bold=True, color=C_WHITE)
        hdr.fill = _fill(C_NAVY)
        hdr.alignment = _center(wrap=True)
        hdr.border = _border()

    # Group by date
    # Actual P&L always uses the FULL log (actual_df) so the quality-gate filter
    # never hides real bets from the summary. Theoretical uses the filtered bets.
    actual_source = actual_df.copy() if actual_df is not None else bets.copy()
    actual_source["date_str"] = actual_source["date"].dt.date

    bets_with_date = bets.copy()
    bets_with_date["date_str"] = bets_with_date["date"].dt.date

    all_dates = sorted(set(actual_source["date_str"]) | set(bets_with_date["date_str"]))

    daily_groups = []
    for date_val in all_dates:
        day_actual = actual_source[actual_source["date_str"] == date_val]
        day_bets   = bets_with_date[bets_with_date["date_str"] == date_val]

        # Actual P&L — ALL bets placed that day (unfiltered)
        actual_bets = day_actual[day_actual["actual_pnl"].notna()]
        daily_actual_pnl = float(actual_bets["actual_pnl"].sum()) if not actual_bets.empty else 0.0
        actual_wins = int((actual_bets["actual_pnl"] > 0).sum()) if not actual_bets.empty else 0
        actual_losses = int((actual_bets["actual_pnl"] < 0).sum()) if not actual_bets.empty else 0

        # Theoretical P&L (recommended bets, may be filtered)
        theo_bets = day_bets[day_bets["theoretical_pnl"].notna()]
        daily_theo_pnl = float(theo_bets["theoretical_pnl"].sum()) if not theo_bets.empty else 0.0
        theo_wins = int((theo_bets["theoretical_pnl"] > 0).sum()) if not theo_bets.empty else 0
        theo_losses = int((theo_bets["theoretical_pnl"] < 0).sum()) if not theo_bets.empty else 0

        daily_groups.append({
            "date": date_val,
            "daily_actual": daily_actual_pnl,
            "daily_theo": daily_theo_pnl,
            "actual_w": actual_wins,
            "actual_l": actual_losses,
            "theo_w": theo_wins,
            "theo_l": theo_losses,
        })

    # Compute cumulative totals chronologically (old to new), then display newest-first
    daily_groups.sort(key=lambda x: x["date"])
    cum_act = 0.0
    cum_tho = 0.0
    for day_data in daily_groups:
        cum_act += day_data["daily_actual"]
        cum_tho += day_data["daily_theo"]
        day_data["cum_actual"] = cum_act
        day_data["cum_theo"]   = cum_tho

    daily_groups.sort(key=lambda x: x["date"], reverse=True)

    for row_i, day_data in enumerate(daily_groups):
        excel_row = row_i + 4
        cumulative_actual = day_data["cum_actual"]
        cumulative_theo   = day_data["cum_theo"]

        # Date
        date_cell = ws.cell(row=excel_row, column=sc, value=str(day_data["date"]))
        date_cell.alignment = _center()
        date_cell.border = _border()
        date_cell.font = _font(size=9)
        date_cell.fill = _fill(C_BLUE_LT)

        # Daily Actual P&L
        daily_act_cell = ws.cell(row=excel_row, column=sc + 1, value=day_data["daily_actual"])
        daily_act_cell.number_format = '+$#,##0.00;-$#,##0.00'
        daily_act_cell.alignment = _right()
        daily_act_cell.border = _border()
        daily_act_cell.font = _font(bold=True, size=10)
        if day_data["daily_actual"] > 0:
            daily_act_cell.fill = _fill(C_GREEN)
        elif day_data["daily_actual"] < 0:
            daily_act_cell.fill = _fill(C_RED)
        else:
            daily_act_cell.fill = _fill(C_TAN)

        # Cumulative Actual
        cum_act_cell = ws.cell(row=excel_row, column=sc + 2, value=cumulative_actual)
        cum_act_cell.number_format = '+$#,##0.00;-$#,##0.00'
        cum_act_cell.alignment = _right()
        cum_act_cell.border = _border()
        cum_act_cell.font = _font(bold=True, size=10)
        if cumulative_actual > 0:
            cum_act_cell.fill = _fill(C_GREEN)
        elif cumulative_actual < 0:
            cum_act_cell.fill = _fill(C_RED)
        else:
            cum_act_cell.fill = _fill(C_TAN)

        # Daily Theo P&L
        daily_theo_cell = ws.cell(row=excel_row, column=sc + 3, value=day_data["daily_theo"])
        daily_theo_cell.number_format = '+$#,##0.00;-$#,##0.00'
        daily_theo_cell.alignment = _right()
        daily_theo_cell.border = _border()
        daily_theo_cell.font = _font(size=9)
        if day_data["daily_theo"] > 0:
            daily_theo_cell.fill = _fill(C_GREEN)
        elif day_data["daily_theo"] < 0:
            daily_theo_cell.fill = _fill(C_RED)
        else:
            daily_theo_cell.fill = _fill(C_TAN)

        # Cumulative Theo
        cum_theo_cell = ws.cell(row=excel_row, column=sc + 4, value=cumulative_theo)
        cum_theo_cell.number_format = '+$#,##0.00;-$#,##0.00'
        cum_theo_cell.alignment = _right()
        cum_theo_cell.border = _border()
        cum_theo_cell.font = _font(size=9)
        if cumulative_theo > 0:
            cum_theo_cell.fill = _fill(C_GREEN)
        elif cumulative_theo < 0:
            cum_theo_cell.fill = _fill(C_RED)
        else:
            cum_theo_cell.fill = _fill(C_TAN)

        # Actual W-L
        actual_wl = f"{day_data['actual_w']}-{day_data['actual_l']}"
        actual_wl_cell = ws.cell(row=excel_row, column=sc + 5, value=actual_wl if day_data['actual_w'] + day_data['actual_l'] > 0 else "—")
        actual_wl_cell.alignment = _center()
        actual_wl_cell.border = _border()
        actual_wl_cell.font = _font(size=9)
        actual_wl_cell.fill = _fill(C_BLUE_LT)

        # Theo W-L
        theo_wl = f"{day_data['theo_w']}-{day_data['theo_l']}"
        theo_wl_cell = ws.cell(row=excel_row, column=sc + 6, value=theo_wl if day_data['theo_w'] + day_data['theo_l'] > 0 else "—")
        theo_wl_cell.alignment = _center()
        theo_wl_cell.border = _border()
        theo_wl_cell.font = _font(size=9)
        theo_wl_cell.fill = _fill(C_BLUE_LT)


def freeze_and_filter(ws):
    ws.freeze_panes = "A4"
    ws.auto_filter.ref = f"A3:{get_column_letter(15)}3"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    df = load_log()
    if df.empty:
        print("No bets logged yet — run update.py first.")
        return

    wb = openpyxl.Workbook()

    # ── Sheet 1: Filtered Picks (only bets that pass quality gates) ────────
    ws_filtered = wb.active
    print("Writing FILTERED picks sheet...")
    bets_filtered = write_picks_sheet(ws_filtered, df, filtered=True)
    print("Writing stats panel (filtered)...")
    write_stats_panel(ws_filtered, df, start_col=17)
    print("Writing running P&L (filtered)...")
    write_running_pnl(ws_filtered, bets_filtered, start_col=20, actual_df=df)
    freeze_and_filter(ws_filtered)

    # ── Sheet 2: All Bets (no filters applied) ─────────────────────────────
    ws_all = wb.create_sheet(title="All Bets")
    print("Writing ALL bets sheet...")
    bets_all = write_picks_sheet(ws_all, df, filtered=False)
    print("Writing stats panel (all)...")
    write_stats_panel(ws_all, df, start_col=17)
    print("Writing running P&L (all)...")
    write_running_pnl(ws_all, bets_all, start_col=20, actual_df=df)
    freeze_and_filter(ws_all)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(f"\nSaved to {out}")
    print(f"  Filtered sheet: {len(bets_filtered)} bet rows")
    print(f"  All bets sheet: {len(bets_all)} bet rows")


if __name__ == "__main__":
    main()
