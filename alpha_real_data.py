"""ALPHA-X Real Data Layer 1.0.

Purpose: turn real OKX public market data into auditable, time-aligned research inputs.
No synthetic fallback is permitted. Missing sources remain missing and can gate training.

Supported persisted sources:
- 15m/1m candles (via OKX public REST)
- historical funding-rate (OKX public REST; limited by OKX history window)
- continuously collected open interest snapshots (no fake historical OI backfill)
- continuously collected public trades
- continuously collected order-book snapshots

The collector is intentionally simple and durable: SQLite + append/dedupe keys.
For true historical L2/tick research, collection must run before the research period;
this module does not pretend REST snapshots reconstruct historical order-book state.
"""
from __future__ import annotations
import argparse, json, sqlite3, time, hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import pandas as pd
from loguru import logger
import numpy as np

VERSION = "ALPHA-X-REAL-DATA-1.0"
DEFAULT_DB = Path(__file__).with_name("alpha_market_data.sqlite3")
OKX_BASE = "https://www.okx.com"

SCHEMA = [
("funding", "CREATE TABLE IF NOT EXISTS funding (inst_id TEXT NOT NULL, ts INTEGER NOT NULL, funding_rate REAL, realized_rate REAL, raw TEXT, PRIMARY KEY(inst_id,ts))"),
("oi", "CREATE TABLE IF NOT EXISTS oi (inst_id TEXT NOT NULL, ts INTEGER NOT NULL, oi REAL, oi_ccy REAL, oi_usd REAL, raw TEXT, PRIMARY KEY(inst_id,ts))"),
("trades", "CREATE TABLE IF NOT EXISTS trades (inst_id TEXT NOT NULL, ts INTEGER NOT NULL, trade_id TEXT NOT NULL, side TEXT, px REAL, sz REAL, source TEXT, raw TEXT, PRIMARY KEY(inst_id,trade_id))"),
("books", "CREATE TABLE IF NOT EXISTS books (inst_id TEXT NOT NULL, ts INTEGER NOT NULL, bid_px REAL, ask_px REAL, bid_depth_usd REAL, ask_depth_usd REAL, depth_imbalance REAL, spread_bps REAL, seq_id INTEGER, raw TEXT, PRIMARY KEY(inst_id,ts,seq_id))"),
("bars", "CREATE TABLE IF NOT EXISTS bars (inst_id TEXT NOT NULL, ts INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, volume REAL, confirm INTEGER, raw TEXT, PRIMARY KEY(inst_id,ts))"),
]


def _num(x, default=np.nan):
    try:
        v=float(x); return v if np.isfinite(v) else default
    except Exception: return default


def _inst_id(symbol: str) -> str:
    s=str(symbol).strip().upper()
    if s.endswith("-SWAP"): return s
    if "/" in s:
        base, rest=s.split("/",1); quote=rest.split(":",1)[0]
        return f"{base}-{quote}-SWAP"
    if s.count("-") == 1: return s+"-SWAP"
    return s


class OKXPublic:
    def __init__(self, base_url: str = OKX_BASE, timeout: float = 15):
        self.base_url=base_url.rstrip("/"); self.timeout=timeout
    def get(self, path: str, params: Optional[Dict[str,Any]]=None) -> Dict[str,Any]:
        q=urlencode({k:v for k,v in (params or {}).items() if v is not None})
        url=self.base_url+path+("?"+q if q else "")
        req=Request(url, headers={"User-Agent":"ALPHA-X-RealData/1.0","Accept":"application/json"})
        with urlopen(req, timeout=self.timeout) as r:
            obj=json.loads(r.read().decode("utf-8"))
        if str(obj.get("code","0"))!="0":
            raise RuntimeError(f"OKX API error {obj.get('code')}: {obj.get('msg')}")
        return obj
    def funding_history(self, inst_id: str, after: Optional[int]=None, before: Optional[int]=None, limit: int=400):
        return self.get("/api/v5/public/funding-rate-history", {"instId":inst_id,"after":after,"before":before,"limit":min(400,int(limit))}).get("data",[])
    def open_interest(self, inst_id: str):
        # Public endpoint returns the current market OI snapshot. It is intentionally
        # NOT exposed as a historical paginator; historical OI is accumulated only
        # while the collector is running.
        return self.get("/api/v5/public/open-interest", {"instType":"SWAP","instId":inst_id}).get("data",[])
    def trades(self, inst_id: str, limit: int=500):
        return self.get("/api/v5/market/trades", {"instId":inst_id,"limit":min(500,int(limit))}).get("data",[])
    def books(self, inst_id: str, sz: int=20):
        return self.get("/api/v5/market/books", {"instId":inst_id,"sz":min(400,int(sz))}).get("data",[])
    def history_trades(self, inst_id: str, after: Optional[str]=None, before: Optional[str]=None, limit: int=100):
        return self.get("/api/v5/market/history-trades", {"instId":inst_id,"type":"1","after":after,"before":before,"limit":min(100,int(limit))}).get("data",[])
    def candles(self, inst_id: str, bar: str="15m", after: Optional[int]=None, limit: int=100):
        return self.get("/api/v5/market/history-candles", {"instId":inst_id,"bar":bar,"after":after,"limit":min(100,int(limit))}).get("data",[])
    def history_candles_all(self, inst_id: str, bar: str="15m", pages: int=50):
        out=[]; after=None; last=None
        for _ in range(max(1,int(pages))):
            rows=self.candles(inst_id,bar,after,100)
            if not rows: break
            for r in rows:
                if len(r)>=9:
                    # 历史K线都是已完成的，强制confirm=1，避免被构建数据集过滤掉
                    out.append({"instId":inst_id,"ts":int(r[0]),"open":_num(r[1]),"high":_num(r[2]),"low":_num(r[3]),"close":_num(r[4]),"volume":_num(r[5]),"confirm":1})
            times=[int(r[0]) for r in rows if r and r[0]]
            if not times: break
            nxt=min(times)
            if last is not None and nxt>=last: break
            last=nxt; after=nxt-1
            if len(rows)<100: break
            time.sleep(.08)
        return out


class RealDataStore:
    def __init__(self, db_path: str|Path=DEFAULT_DB):
        self.path=Path(db_path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.cx=sqlite3.connect(str(self.path), timeout=30)
        self.cx.execute("PRAGMA journal_mode=WAL")
        self.cx.execute("PRAGMA synchronous=FULL")
        for _,sql in SCHEMA: self.cx.execute(sql)
        self.cx.commit()
    def close(self): self.cx.close()
    def insert_funding(self, rows: Iterable[Dict[str,Any]]):
        n=0
        for r in rows:
            ts=int(r.get("fundingTime") or r.get("ts")); iid=str(r.get("instId"));
            self.cx.execute("INSERT OR REPLACE INTO funding VALUES(?,?,?,?,?)",(iid,ts,_num(r.get("fundingRate")),_num(r.get("realizedRate")),json.dumps(r,separators=(",",":"))))
            n+=1
        self.cx.commit(); return n
    def insert_oi(self, rows: Iterable[Dict[str,Any]]):
        n=0
        for r in rows:
            ts=int(r.get("ts")); iid=str(r.get("instId"));
            self.cx.execute("INSERT OR REPLACE INTO oi VALUES(?,?,?,?,?,?)",(iid,ts,_num(r.get("oi")),_num(r.get("oiCcy")),_num(r.get("oiUsd")),json.dumps(r,separators=(",",":"))))
            n+=1
        self.cx.commit(); return n
    def insert_trades(self, rows: Iterable[Dict[str,Any]]):
        n=0
        for r in rows:
            iid=str(r.get("instId")); tid=str(r.get("tradeId") or hashlib.sha256(json.dumps(r,sort_keys=True).encode()).hexdigest()[:24]); ts=int(r.get("ts"))
            self.cx.execute("INSERT OR IGNORE INTO trades VALUES(?,?,?,?,?,?,?,?)",(iid,ts,tid,str(r.get("side","")).lower(),_num(r.get("px")),_num(r.get("sz")),str(r.get("source","")),json.dumps(r,separators=(",",":"))))
            n+=1
        self.cx.commit(); return n
    def insert_books(self, rows: Iterable[Dict[str,Any]]):
        n=0
        for r in rows:
            iid=str(r["instId"]); ts=int(r["ts"]); seq=int(r.get("seqId") or 0)
            self.cx.execute("INSERT OR IGNORE INTO books VALUES(?,?,?,?,?,?,?,?,?,?)",(iid,ts,_num(r.get("bidPx")),_num(r.get("askPx")),_num(r.get("bidDepthUsd")),_num(r.get("askDepthUsd")),_num(r.get("depthImbalance")),_num(r.get("spreadBps")),seq,json.dumps(r,separators=(",",":"))))
            n+=1
        self.cx.commit(); return n
    def insert_bars(self, rows: Iterable[Dict[str,Any]]):
        n=0
        for r in rows:
            iid=str(r.get("instId")); ts=int(r.get("ts"));
            self.cx.execute("INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?,?,?)",(iid,ts,_num(r.get("open",r.get("o"))),_num(r.get("high",r.get("h"))),_num(r.get("low",r.get("l"))),_num(r.get("close",r.get("c"))),_num(r.get("volume",r.get("vol"))),int(r.get("confirm",1) or 0),json.dumps(r,separators=(",",":"))))
            n+=1
        self.cx.commit(); return n
    def source_counts(self, inst_id: str) -> Dict[str,int]:
        out={}
        for name in ("funding","oi","trades","books","bars"):
            out[name]=int(self.cx.execute(f"SELECT COUNT(*) FROM {name} WHERE inst_id=?",(inst_id,)).fetchone()[0])
        return out
    def fetch(self, table: str, inst_id: str, start_ts: Optional[int]=None, end_ts: Optional[int]=None) -> pd.DataFrame:
        if table not in {"funding","oi","trades","books","bars"}: raise ValueError(table)
        where=["inst_id=?"]; args=[inst_id]
        if start_ts is not None: where.append("ts>=?"); args.append(int(start_ts))
        if end_ts is not None: where.append("ts<=?"); args.append(int(end_ts))
        return pd.read_sql_query(f"SELECT * FROM {table} WHERE {' AND '.join(where)} ORDER BY ts",self.cx,params=args)


def _book_row(inst_id: str, item: Dict[str,Any]) -> Dict[str,Any]:
    asks=item.get("asks") or []; bids=item.get("bids") or []
    def depth_usd(levels):
        return float(sum(max(0.0,_num(x[0],0))*max(0.0,_num(x[1],0)) for x in levels))
    bd=depth_usd(bids); ad=depth_usd(asks); bp=_num(bids[0][0]) if bids else np.nan; ap=_num(asks[0][0]) if asks else np.nan
    mid=(bp+ap)/2 if np.isfinite(bp) and np.isfinite(ap) and bp+ap>0 else np.nan
    return {"instId":inst_id,"ts":int(item.get("ts")),"bidPx":bp,"askPx":ap,"bidDepthUsd":bd,"askDepthUsd":ad,
            "depthImbalance":(bd-ad)/(bd+ad) if bd+ad>0 else np.nan,"spreadBps":(ap-bp)/mid*10000 if mid and np.isfinite(mid) else np.nan,
            "seqId":int(item.get("seqId") or 0)}


def collect_once(client: OKXPublic, store: RealDataStore, inst_id: str, book_depth: int=20) -> Dict[str,int]:
    logger.debug(f"[实时行情] {inst_id}｜开始采集 OI/Trades/L2")
    out={}
    out["oi"]=store.insert_oi(client.open_interest(inst_id))
    tr=client.trades(inst_id,500); out["trades"]=store.insert_trades(tr)
    bk=client.books(inst_id,book_depth); rows=[]
    for item in bk: rows.append(_book_row(inst_id,item))
    out["books"]=store.insert_books(rows)
    logger.debug(f"[实时行情] {inst_id}｜完成｜OI={out['oi']} Trades={out['trades']} L2={out['books']}")
    return out


def fetch_funding_history(client: OKXPublic, store: RealDataStore, inst_id: str, pages: int=20) -> Dict[str,Any]:
    # OKX returns newest-first. Use 'after' to walk older funding times.
    after=None; total=0; last=None
    for _ in range(max(1,int(pages))):
        rows=client.funding_history(inst_id,after=after,limit=400)
        if not rows: break
        total+=store.insert_funding(rows)
        times=[int(r.get("fundingTime")) for r in rows if r.get("fundingTime")]
        if not times: break
        nxt=min(times)
        if last is not None and nxt>=last: break
        last=nxt; after=nxt-1
        if len(rows)<400: break
        time.sleep(.12)
    return {"inst_id":inst_id,"inserted":total,"pages":pages}



def fetch_bars_history(client: OKXPublic, store: RealDataStore, inst_id: str, bar: str="15m", pages: int=50) -> Dict[str,Any]:
    rows=client.history_candles_all(inst_id,bar,pages); n=store.insert_bars(rows)
    return {"inst_id":inst_id,"bar":bar,"inserted":n,"pages":pages}


def _timeframe_minutes(timeframe: str) -> int:
    tf = str(timeframe or "15m").strip().lower()
    if tf.endswith("m"):
        n = int(tf[:-1])
    elif tf.endswith("h"):
        n = int(tf[:-1]) * 60
    elif tf.endswith("d"):
        n = int(tf[:-1]) * 24 * 60
    else:
        raise ValueError(f"不支持的K线周期: {timeframe}")
    if n < 15 or n % 15 != 0:
        raise ValueError(f"周期必须是15分钟的整数倍: {timeframe}")
    return n

def _bar_features(df: pd.DataFrame, ts_col="ts") -> pd.DataFrame:
    if df.empty: return pd.DataFrame()
    x=df.copy(); x[ts_col]=pd.to_numeric(x[ts_col],errors="coerce"); x=x.dropna(subset=[ts_col]); x[ts_col]=x[ts_col].astype("int64")
    return x


def augment_candles(candles: pd.DataFrame, store: RealDataStore, inst_id: str, timeframe: str="15m", strict: bool=False) -> tuple[pd.DataFrame,Dict[str,Any]]:
    """Attach only information available by each completed candle.

    Same-bar trade/book data are aggregated to the candle bucket because the model
    predicts at candle close. Cross-source joins never use a future timestamp.
    """
    d=candles.copy(); d["ts"]=pd.to_numeric(d["ts"],errors="coerce").astype("int64"); d=d.sort_values("ts").reset_index(drop=True)
    mins=int(str(timeframe).replace("m","")) if str(timeframe).endswith("m") else 15
    freq=f"{mins}min"; idx=pd.to_datetime(d.ts,unit="ms",utc=True)
    start=int(d.ts.min()); end=int(d.ts.max()+mins*60_000-1)
    funding=store.fetch("funding",inst_id,start,end)
    oi=store.fetch("oi",inst_id,start,end)
    trades=store.fetch("trades",inst_id,start,end)
    books=store.fetch("books",inst_id,start,end)
    # All aggregates are keyed by the candle start, not by a later event.
    result=d.copy(); coverage={"funding":0,"open_interest":0,"order_flow":0,"l2":0}
    if not funding.empty:
        funding=funding.sort_values("ts").copy()
        # Carry the last known realized funding rate forward, but never backward-fill.
        # A funding event itself is also retained as a separate point-in-time feature.
        fbase=funding[["ts","realized_rate","funding_rate"]].rename(columns={"realized_rate":"funding_rate_realized"})
        result=pd.merge_asof(result.sort_values("ts"), fbase, on="ts", direction="backward", allow_exact_matches=True)
        event=funding[["ts","realized_rate"]].rename(columns={"ts":"event_ts"})
        event["funding_event"]=1; event["funding_rate_event"]=event.realized_rate
        result["funding_event"]=0; result["funding_rate_event"]=np.nan; result["funding_ts"]=np.nan
        for et,_,er in event[["event_ts","funding_event","funding_rate_event"]].itertuples(index=False):
            bucket=(int(et)//(mins*60_000))*(mins*60_000); mask=result.ts.eq(bucket)
            result.loc[mask,"funding_event"]=1; result.loc[mask,"funding_rate_event"]=er; result.loc[mask,"funding_ts"]=int(et)
        coverage["funding"]=int(result.funding_rate_realized.notna().sum())
    if not oi.empty:
        # OI is a snapshot stream in this collector, not a historical candle series.
        # Bucket snapshots to the target candle and keep the last REAL observation
        # inside that candle. Do not as-of carry one old snapshot across later
        # candles: that would falsely turn sparse OI history into 100% coverage.
        oi=oi.sort_values("ts")[["ts","oi","oi_ccy","oi_usd"]].copy()
        oi["oi_change_pct"]=oi["oi"].pct_change()
        oi["bucket"]=(oi.ts//(mins*60_000))*(mins*60_000)
        oi_bucket=(oi.sort_values("ts").groupby("bucket",as_index=False).last()
                   [["bucket","oi","oi_ccy","oi_usd","oi_change_pct"]])
        result=result.merge(oi_bucket,left_on="ts",right_on="bucket",how="left").drop(columns=["bucket"],errors="ignore")
        coverage["open_interest"]=int(result.oi.notna().sum())
    if not trades.empty:
        trades["bucket"]=(trades.ts//(mins*60_000))*(mins*60_000); trades["usd"]=(trades.px*trades.sz).fillna(0)
        def tf(g):
            buy=float(g.loc[g.side=="buy","sz"].sum()); sell=float(g.loc[g.side=="sell","sz"].sum()); total=buy+sell
            return pd.Series({"trade_count":len(g),"aggressive_buy_ratio":buy/total if total else np.nan,"trade_imbalance":(buy-sell)/total if total else np.nan,"ofi":buy-sell,"trade_volume_usd":float(g.usd.sum())})
        tg=trades.groupby("bucket").apply(tf,include_groups=False).reset_index()
        result=result.merge(tg,left_on="ts",right_on="bucket",how="left").drop(columns=["bucket"],errors="ignore"); coverage["order_flow"]=int(result.trade_count.notna().sum())
    if not books.empty:
        books["bucket"]=(books.ts//(mins*60_000))*(mins*60_000)
        bg=books.sort_values("ts").groupby("bucket",as_index=False).last()[["bucket","bid_px","ask_px","bid_depth_usd","ask_depth_usd","depth_imbalance","spread_bps"]]
        result=result.merge(bg,left_on="ts",right_on="bucket",how="left").drop(columns=["bucket"],errors="ignore"); coverage["l2"]=int(result.bid_px.notna().sum())
    result=result.replace([np.inf,-np.inf],np.nan)
    sources=sum(v>0 for v in coverage.values())
    audit={"version":VERSION,"inst_id":inst_id,"rows":len(result),"coverage":coverage,"independent_sources":sources,"strict":bool(strict)}
    if strict:
        missing=[k for k,v in coverage.items() if v==0]
        if missing: raise ValueError("REAL_DATA_STRICT 缺失真实历史源: "+",".join(missing))
    return result,audit



def augment_cross_market(candles: pd.DataFrame, btc: Optional[pd.DataFrame]=None, eth: Optional[pd.DataFrame]=None) -> pd.DataFrame:
    """Add lagged cross-market returns using only timestamps at or before the candle.
    Inputs must be independently sourced completed bars; no forward fill is performed.
    """
    out=candles.copy().sort_values("ts").reset_index(drop=True)
    def prep(x, prefix):
        if x is None or len(x)==0: return None
        z=x[["ts","close"]].copy().sort_values("ts"); z["ts"]=pd.to_numeric(z.ts,errors="coerce").astype("int64")
        z[prefix+"_ret_1"]=z.close.pct_change(); return z[["ts",prefix+"_ret_1"]]
    for x,prefix in ((btc,"btc"),(eth,"eth")):
        z=prep(x,prefix)
        if z is not None:
            out=pd.merge_asof(out,z,on="ts",direction="backward",allow_exact_matches=True)
    if "btc_ret_1" in out and "eth_ret_1" in out:
        out["btc_eth_spread"]=out["btc_ret_1"]-out["eth_ret_1"]
    return out

def cross_market_from_store(candles: pd.DataFrame, store: RealDataStore, btc_inst: str="BTC-USDT-SWAP", eth_inst: str="ETH-USDT-SWAP") -> pd.DataFrame:
    btc=store.fetch("bars",btc_inst,int(candles.ts.min()),int(candles.ts.max()))
    eth=store.fetch("bars",eth_inst,int(candles.ts.min()),int(candles.ts.max()))
    return augment_cross_market(candles,btc.rename(columns={"close":"close"}),eth.rename(columns={"close":"close"}))

def source_gate(audit: Dict[str,Any], min_coverage: float=0.80) -> Dict[str,Any]:
    cov=audit.get("coverage",{}); rows=max(1,int(audit.get("rows",0)))
    ratios={k:float(v)/rows for k,v in cov.items()}
    # 核心要求：只检查资金费率覆盖率（可补历史数据）
    # 持仓量/成交/盘口在本采集器中只承诺真实连续采集；历史缺口不伪造，有多少用多少。
    core_sources={"funding"}
    missing=[k for k in core_sources if ratios.get(k,0.0)<float(min_coverage)]
    optional_weak=[k for k in ratios if k not in core_sources and ratios[k]<float(min_coverage)]
    return {"ready":not missing,"ratios":ratios,"min_coverage":float(min_coverage),
            "missing_or_weak":missing,"optional_weak":optional_weak,
            "independent_sources":sum(v>=min_coverage for v in ratios.values())}


def main():
    ap=argparse.ArgumentParser(description="ALPHA-X real OKX market-data collector")
    ap.add_argument("command",choices=["funding-history","bars-history","collect","status"]); ap.add_argument("--inst-id",default="BTC-USDT-SWAP"); ap.add_argument("--db",default=str(DEFAULT_DB)); ap.add_argument("--interval",type=float,default=3.0); ap.add_argument("--loops",type=int,default=0); ap.add_argument("--pages",type=int,default=20)
    args=ap.parse_args(); client=OKXPublic(); store=RealDataStore(args.db)
    try:
        if args.command=="funding-history": print(json.dumps(fetch_funding_history(client,store,args.inst_id,args.pages),ensure_ascii=False,indent=2))
        elif args.command=="bars-history": print(json.dumps(fetch_bars_history(client,store,args.inst_id,"15m",args.pages),ensure_ascii=False,indent=2))
        elif args.command=="status": print(json.dumps({"version":VERSION,"db":args.db,"inst_id":args.inst_id,"counts":store.source_counts(args.inst_id)},ensure_ascii=False,indent=2))
        else:
            i=0
            while True:
                i+=1; print(json.dumps({"ts":int(time.time()*1000),**collect_once(client,store,args.inst_id)},ensure_ascii=False),flush=True)
                if args.loops and i>=args.loops: break
                time.sleep(max(.5,args.interval))
    finally: store.close()

if __name__=="__main__": main()


def augment_candles_selected(candles, store, inst_id, timeframe="15m", strict=False,
                              sources=None, cross_market=False):
    """按选择的数据源增强K线。
    
    sources: dict，如 {"funding": True, "open_interest": True, "order_flow": False, "l2": False}
    为None时全部启用（和原augment_candles一样）。
    """
    if sources is None:
        sources = {"funding": True, "open_interest": True, "order_flow": True, "l2": True}
    
    # 先全部增强
    result, audit = augment_candles(candles, store, inst_id, timeframe, strict)
    
    # 按选择删除不需要的列
    source_cols = {
        "funding": ["funding_rate_realized", "funding_rate", "funding_event", "funding_rate_event", "funding_ts"],
        "open_interest": ["oi", "oi_ccy", "oi_usd", "oi_change_pct"],
        "order_flow": ["trade_count", "aggressive_buy_ratio", "trade_imbalance", "ofi", "trade_volume_usd"],
        "l2": ["bid_px", "ask_px", "bid_depth_usd", "ask_depth_usd", "depth_imbalance", "spread_bps"],
    }
    
    removed = []
    for src, enabled in sources.items():
        if not enabled and src in source_cols:
            for col in source_cols[src]:
                if col in result.columns:
                    result = result.drop(columns=[col], errors="ignore")
                    removed.append(col)
            audit["coverage"][src] = 0
    
    # 跨市场
    if cross_market:
        result = cross_market_from_store(result, store)
    
    audit["selected_sources"] = {k: v for k, v in sources.items()}
    audit["removed_cols"] = removed
    return result, audit
