import json, numpy as np, pandas as pd
from pathlib import Path
from typing import Optional

RAW_PATH   = Path("data/raw/historical_odds/mlb_odds_2021_2025.json")
CLEAN_PATH = Path("data/processed/historical_odds_clean.csv")
PREFERRED_BOOKS = ["draftkings", "fanduel", "betmgm", "caesars", "bet365"]
_SBR_TO_FG = {"ARI":"ARI","AZ":"ARI","ATL":"ATL","BAL":"BAL","BOS":"BOS","CHC":"CHC","CHW":"CHW","CIN":"CIN","CLE":"CLE","COL":"COL","DET":"DET","HOU":"HOU","KC":"KCR","LAA":"LAA","LAD":"LAD","MIA":"MIA","MIL":"MIL","MIN":"MIN","NYM":"NYM","NYY":"NYY","OAK":"OAK","ATH":"OAK","PHI":"PHI","PIT":"PIT","SD":"SDP","SEA":"SEA","SF":"SFG","STL":"STL","TB":"TBR","TEX":"TEX","TOR":"TOR","WAS":"WSN"}
_SKIP_TYPES = {"A","S","E"}

def map_team(n): return _SBR_TO_FG.get(n)
def american_to_implied_prob(o):
    if o is None: return np.nan
    return 100.0/(o+100.0) if o>0 else abs(o)/(abs(o)+100.0)
def remove_vig(h,a):
    t=h+a
    if t<=0 or np.isnan(t): return np.nan,np.nan
    return h/t,a/t
def _parse_game(ds,game):
    gv=game.get("gameView",{})
    if gv.get("gameType","R") in _SKIP_TYPES: return []
    hfg=map_team(gv.get("homeTeam",{}).get("shortName",""))
    afg=map_team(gv.get("awayTeam",{}).get("shortName",""))
    if not hfg or not afg: return []
    rows=[]
    for b in game.get("odds",{}).get("moneyline",[]):
        bk=b.get("sportsbook","unknown")
        for lt,lk in [("opening","openingLine"),("closing","currentLine")]:
            ln=b.get(lk,{}); ho=ln.get("homeOdds"); ao=ln.get("awayOdds")
            if ho is None or ao is None: continue
            hr=american_to_implied_prob(ho); ar=american_to_implied_prob(ao)
            hf,af=remove_vig(hr,ar)
            rows.append({"date":ds,"home_team_fg":hfg,"away_team_fg":afg,"bookmaker":bk,"line_type":lt,"home_ml_odds":ho,"away_ml_odds":ao,"home_implied_prob":round(hr,4),"away_implied_prob":round(ar,4),"home_fair_prob":round(hf,4),"away_fair_prob":round(af,4),"vig":round((hr+ar)-1.0,4)})
    return rows
def parse_raw_json(p=RAW_PATH):
    with open(p,encoding="utf-8") as f: data=json.load(f)
    rows=[]
    for ds,games in data.items():
        for g in games: rows.extend(_parse_game(ds,g))
    df=pd.DataFrame(rows); df["date"]=pd.to_datetime(df["date"]); return df
def compute_consensus(long_df,preferred_books=None,line_type="closing"):
    if preferred_books is None: preferred_books=PREFERRED_BOOKS
    df=long_df[(long_df["line_type"]==line_type)&(long_df["bookmaker"].isin(preferred_books))].copy()
    if df.empty: return pd.DataFrame()
    grp=df.groupby(["date","home_team_fg","away_team_fg"])
    out=grp.agg(market_home_prob=("home_fair_prob","mean"),market_away_prob=("away_fair_prob","mean"),n_bookmakers=("bookmaker","nunique"),bookmaker_spread=("home_fair_prob",lambda x:round(x.max()-x.min(),4)),avg_vig=("vig","mean")).reset_index()
    out["market_home_prob"]=out["market_home_prob"].round(4); out["market_away_prob"]=out["market_away_prob"].round(4)
    out["avg_vig"]=out["avg_vig"].round(4); out["line_type"]=line_type; out["suspicious"]=out["bookmaker_spread"]>0.03
    return out
def build_clean_odds(json_path=RAW_PATH,out_path=CLEAN_PATH):
    print("Parsing raw JSON..."); long_df=parse_raw_json(json_path)
    print(f"  {len(long_df):,} rows  |  {long_df.groupby(['date','home_team_fg','away_team_fg']).ngroups:,} games")
    print("Closing consensus..."); closing=compute_consensus(long_df,line_type="closing")
    print(f"  {len(closing):,} games | {closing['n_bookmakers'].mean():.1f} books avg | {closing['suspicious'].sum()} suspicious")
    print("Opening consensus..."); opening=compute_consensus(long_df,line_type="opening")
    merged=closing.merge(opening[["date","home_team_fg","away_team_fg","market_home_prob","market_away_prob","n_bookmakers","avg_vig"]].rename(columns={"market_home_prob":"open_home_prob","market_away_prob":"open_away_prob","n_bookmakers":"open_n_books","avg_vig":"open_avg_vig"}),on=["date","home_team_fg","away_team_fg"],how="left")
    merged["odds_source"]="real"
    out_path.parent.mkdir(parents=True,exist_ok=True); merged.to_csv(out_path,index=False)
    print(f"Saved -> {out_path}  ({len(merged):,} rows)"); return merged
def load_historical_odds(clean_path=CLEAN_PATH,raw_path=RAW_PATH):
    if clean_path.exists(): return pd.read_csv(clean_path,parse_dates=["date"])
    return build_clean_odds(raw_path,clean_path)
def get_market_odds(date,home_team_fg,away_team_fg,odds_df=None):
    if odds_df is None: odds_df=load_historical_odds()
    target=pd.to_datetime(date).strftime("%Y-%m-%d")
    mask=((pd.to_datetime(odds_df["date"]).dt.strftime("%Y-%m-%d")==target)&(odds_df["home_team_fg"]==home_team_fg)&(odds_df["away_team_fg"]==away_team_fg))
    rows=odds_df[mask]; return None if rows.empty else rows.iloc[0].to_dict()
if __name__=="__main__":
    df=build_clean_odds()
    print(df.head(3).to_string())
    print(df["avg_vig"].describe().round(4))
    print(df.groupby(df["date"].dt.year).size())
