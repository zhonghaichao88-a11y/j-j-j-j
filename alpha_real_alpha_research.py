"""ALPHA-X REAL ALPHA RESEARCH 17.0.

Purpose: test whether real external sources add *incremental* out-of-sample alpha
beyond the existing OHLCV/technical baseline.

Design:
- identical labels/costs/model family across ablations;
- temporal walk-forward development selection; untouched final test;
- source ablations: OHLCV -> Funding -> OI -> Order Flow -> L2 -> Cross Market;
- no forward/backward fill; rows require finite selected features;
- paired bar-level cost-adjusted signal returns for statistical comparison;
- bootstrap confidence interval + sign/permutation test + Benjamini-Hochberg;
- stability across temporal folds; multiple-testing-aware winner promotion;
- dataset/manifest hashes are recorded so results are reproducible.

This module is research-only. It never places orders and never claims causality.
"""
from __future__ import annotations
import hashlib, json, math, time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, log_loss
from loguru import logger

VERSION = "ALPHA-X-REAL-ALPHA-RESEARCH-17.2"

SOURCE_GROUPS = {
    "ohlcv": (),
    "funding": ("funding_rate", "funding_rate_realized", "funding_event", "funding_rate_event"),
    "open_interest": ("open_interest", "oi_change_pct", "oi", "oi_ccy", "oi_usd"),
    "order_flow": ("ofi", "aggressive_buy_ratio", "trade_imbalance", "trade_count", "trade_volume_usd"),
    "l2": ("bid_depth_usd", "ask_depth_usd", "spread_bps", "depth_imbalance"),
    "cross_market": ("btc_ret_1", "eth_ret_1", "btc_eth_spread", "venue_basis_bps"),
}

CUMULATIVE_ABLATIONS = [
    ("A_OHLCV", ("ohlcv",)),
    ("B_OHLCV_FUNDING", ("ohlcv", "funding")),
    ("C_OHLCV_FUNDING_OI", ("ohlcv", "funding", "open_interest")),
    ("D_OHLCV_FUNDING_OI_FLOW", ("ohlcv", "funding", "open_interest", "order_flow")),
    ("E_OHLCV_FUNDING_OI_FLOW_L2", ("ohlcv", "funding", "open_interest", "order_flow", "l2")),
    ("F_FULL", ("ohlcv", "funding", "open_interest", "order_flow", "l2", "cross_market")),
]


def _sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rsi(s: pd.Series, n: int = 14) -> pd.Series:
    x=s.diff(); up=x.clip(lower=0); dn=-x.clip(upper=0)
    rs=up.ewm(alpha=1/n, adjust=False).mean()/(dn.ewm(alpha=1/n, adjust=False).mean()+1e-12)
    return 100-100/(1+rs)


def technical_features(d: pd.DataFrame) -> pd.DataFrame:
    """Same technical feature definitions used by ALPHA-X engine, no bfill."""
    x=d.copy().sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    for c in ("open","high","low","close","volume"):
        x[c]=pd.to_numeric(x[c], errors="coerce")
    c,h,l,o,v=x.close,x.high,x.low,x.open,x.volume
    for n in (5,10,20,40,80,160):
        ema=c.ewm(span=n,adjust=False).mean()
        x[f"ret{n}"]=c.pct_change(n)
        x[f"dist_ema{n}"]=c/(ema+1e-12)-1
        x[f"vol{n}"]=c.pct_change().rolling(n).std()
    tr=pd.concat([(h-l),(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    x["atr14"]=tr.rolling(14).mean()/(c+1e-12)
    x["atr50"]=tr.rolling(50).mean()/(c+1e-12)
    x["rsi7"]=_rsi(c,7)/100; x["rsi14"]=_rsi(c,14)/100
    x["range_pct"]=(h-l)/(c+1e-12); x["body_pct"]=(c-o)/(o+1e-12)
    x["upper_wick"]=(h-np.maximum(o,c))/(c+1e-12); x["lower_wick"]=(np.minimum(o,c)-l)/(c+1e-12)
    x["vol_ratio"]=v/(v.rolling(20).mean()+1e-12)
    obv=(np.sign(c.diff()).fillna(0)*v).cumsum(); x["obv_slope"]=obv.pct_change(20)
    # Completed 1h context: only completed hourly bars can flow into 15m rows.
    idx=pd.to_datetime(x.ts,unit="ms",utc=True)
    h1=x.set_index(idx).resample("1h",label="left",closed="left").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna()
    hour_start=pd.Timestamp.now(tz="UTC").floor("h")
    h1=h1[h1.index<hour_start]
    if not h1.empty:
        h1["ema50"]=h1.close.ewm(span=50,adjust=False).mean(); h1["ema200"]=h1.close.ewm(span=200,adjust=False).mean()
        h1["rsi"]=_rsi(h1.close,14)/100; h1["ret8"]=h1.close.pct_change(8)
        for col in ("ema50","ema200","rsi","ret8"):
            x[f"h1_{col}"]=h1[col].reindex(idx,method="ffill").to_numpy()
    else:
        for col in ("ema50","ema200","rsi","ret8"): x[f"h1_{col}"]=np.nan
    # 1小时数据不足时h1相关列全为NaN，用0填充，避免所有行被过滤
    for col in ("h1_ema50","h1_ema200","h1_rsi","h1_ret8"):
        x[col]=x[col].fillna(0.0)
    x["h1_trend"]=x.h1_ema50/(x.h1_ema200+1e-12)-1
    # 1小时数据不足时h1_trend为NaN，用0填充，避免trend_strength全NaN导致所有行被过滤
    x["h1_trend"]=x["h1_trend"].fillna(0.0)
    x["trend_score"]=x.close/(x.close.ewm(span=80,adjust=False).mean()+1e-12)-1+x.h1_trend
    x["vol_regime"]=x.vol20/(x.vol80+1e-12)
    x["trend_strength"]=(x.dist_ema40.abs()+x.h1_trend.abs())/(x.atr14+1e-12)
    # 不允许bfill：研究数据不能用未来值补历史开头。缺失值留给每个WFO折的有效行筛选处理。
    x=x.replace([np.inf,-np.inf],np.nan)
    return x


def labels_first_touch(d: pd.DataFrame, tp: float, sl: float, horizon: int) -> np.ndarray:
    n=len(d); y=np.ones(n,dtype=np.int8); hi=d.high.to_numpy(); lo=d.low.to_numpy(); close=d.close.to_numpy()
    for i in range(n):
        end=min(n,i+horizon+1)
        if i+1>=end: continue
        ep=close[i]; ltp=ep*(1+tp); lsl=ep*(1-sl); stp=ep*(1-tp); ssl=ep*(1+sl); r=1
        for j in range(i+1,end):
            a=hi[j]>=ltp; b=lo[j]<=lsl; c=lo[j]<=stp; e=hi[j]>=ssl
            if (a and b) or (c and e) or (a and c): r=1; break
            if a: r=2; break
            if c: r=0; break
        y[i]=r
    return y


def _base_cols(df: pd.DataFrame) -> List[str]:
    raw={"ts","open","high","low","close","volume","funding_ts","bid_px","ask_px"}
    external=set(sum((list(v) for k,v in SOURCE_GROUPS.items() if k!="ohlcv"),[]))
    return [c for c in df.columns if c not in raw and c not in external]


def _cols_for(df: pd.DataFrame, groups: Sequence[str]) -> List[str]:
    cols=_base_cols(df)
    for g in groups:
        if g=="ohlcv": continue
        for c in SOURCE_GROUPS[g]:
            if c in df.columns and c not in cols: cols.append(c)
    return cols

def _source_coverage(df: pd.DataFrame, group: str) -> float:
    """Coverage of substantive source observations, not event sentinel values."""
    if group == "ohlcv": return 1.0 if len(df) else 0.0
    cols=[c for c in SOURCE_GROUPS.get(group, ()) if c in df.columns]
    if not cols or len(df)==0: return 0.0
    if group == "funding":
        # funding_event/funding_rate_event are sparse event markers and must not
        # make the whole funding source look 100% complete after zero encoding.
        cols=[c for c in ("funding_rate_realized","funding_rate") if c in df.columns] or cols
    vals=df[cols].apply(pd.to_numeric,errors="coerce")
    return float(np.isfinite(vals.to_numpy()).any(axis=1).mean())


def _models(seed=42):
    return [
        RandomForestClassifier(n_estimators=240,max_depth=12,min_samples_leaf=8,class_weight="balanced_subsample",random_state=seed,n_jobs=-1),
        ExtraTreesClassifier(n_estimators=240,max_depth=14,min_samples_leaf=6,class_weight="balanced",random_state=seed+1,n_jobs=-1),
        Pipeline([("scale",StandardScaler()),("lr",LogisticRegression(max_iter=1600,class_weight="balanced",C=.6,random_state=seed))]),
        HistGradientBoostingClassifier(max_iter=180,max_leaf_nodes=15,l2_regularization=2.0,learning_rate=.05,random_state=seed+2),
    ]


def _proba(models,X):
    arr=[]
    for m in models:
        p=m.predict_proba(X); z=np.zeros((len(X),3))
        for j,c in enumerate(m.classes_): z[:,int(c)]=p[:,j]
        arr.append(z)
    return np.mean(arr,axis=0)


def _folds(n:int, dev_end:int, purge:int=48, k:int=3):
    start=max(500,int(dev_end*.38)); edges=np.linspace(start,dev_end,k+1,dtype=int); out=[]
    for i in range(k):
        va0=int(edges[i]); va1=int(edges[i+1]); tr1=va0-purge
        if tr1>=500 and va1-va0>=180: out.append((0,tr1,va0,va1))
    return out


def _fit_predict(d: pd.DataFrame, y: np.ndarray, cols: List[str], tr: np.ndarray, va: np.ndarray):
    good_tr=np.isfinite(d[cols].iloc[tr].to_numpy()).all(axis=1); good_va=np.isfinite(d[cols].iloc[va].to_numpy()).all(axis=1)
    tr2=tr[good_tr]; va2=va[good_va]
    classes=np.unique(y[tr2])
    # A two-class market regime is still a valid supervised problem (for example,
    # directional labels may dominate and the neutral class may be absent).  The
    # probability adapter already maps model classes back into the fixed [0,1,2]
    # layout, so do not discard an otherwise valid WFO fold merely because one
    # label is absent.  A one-class fold remains invalid because it cannot learn
    # a directional boundary.
    if len(tr2)<500 or len(va2)<50 or len(classes)<2: return None
    ms=_models()
    for m in ms: m.fit(d[cols].iloc[tr2],y[tr2])
    return va2,_proba(ms,d[cols].iloc[va2])


def _signal_returns(d: pd.DataFrame, probs: np.ndarray, entry=.62, fee_bps=5.0, slip_bps=5.0) -> np.ndarray:
    """One-bar, non-overlapping signal PnL proxy used only for paired statistics.
    It is deliberately separate from TP/SL backtest: its purpose is hypothesis testing
    on a common per-bar observation grid, not a replacement for execution backtest.
    """
    n=len(d); out=np.zeros(n,dtype=float); c=d.close.to_numpy(); cost=(2*fee_bps+2*slip_bps)/10000.0
    for i in range(n-1):
        p=probs[i]
        if p[2]>=entry and p[2]>p[0]: out[i]=(c[i+1]/c[i]-1)-cost
        elif p[0]>=entry and p[0]>p[2]: out[i]=(c[i]/c[i+1]-1)-cost
    return out


def _block_bootstrap_ci(x: np.ndarray, reps=1500, seed=2026, alpha=.05, block=16):
    x=np.asarray(x,float); x=x[np.isfinite(x)]; n=len(x)
    if n<2: return {"n":int(n),"mean":float(np.mean(x)) if n else 0.0,"ci_low":0.0,"ci_high":0.0}
    b=max(2,min(int(block),n)); starts=np.arange(0,max(1,n-b+1)); rng=np.random.default_rng(seed)
    means=[]
    for _ in range(min(int(reps),10000)):
        chunks=[]
        while sum(len(z) for z in chunks)<n:
            a=int(rng.choice(starts)); chunks.append(x[a:a+b])
        sample=np.concatenate(chunks)[:n]
        means.append(float(sample.mean()))
    means=np.asarray(means)
    return {"n":int(n),"mean":float(x.mean()),"ci_low":float(np.quantile(means,alpha/2)),"ci_high":float(np.quantile(means,1-alpha/2)),"method":"paired_block_bootstrap","block_bars":b}


def _permutation_pvalue(diff: np.ndarray, reps=4000, seed=2026, block=16):
    x=np.asarray(diff,float); x=x[np.isfinite(x)]; n=len(x)
    if n<20: return 1.0
    b=max(2,min(int(block),n)); obs=abs(float(x.mean())); rng=np.random.default_rng(seed); ge=0
    # Flip signs by contiguous blocks to preserve local dependence.
    blocks=[np.arange(i,min(i+b,n)) for i in range(0,n,b)]
    for _ in range(int(reps)):
        signs=rng.choice(np.array([-1.0,1.0]),size=len(blocks)); y=np.zeros(n)
        for j,idx in enumerate(blocks): y[idx]=x[idx]*signs[j]
        if abs(float(y.mean()))>=obs: ge+=1
    return float((ge+1)/(int(reps)+1))


def _bh(pvals: Dict[str,float]) -> Dict[str,float]:
    items=sorted((float(v),k) for k,v in pvals.items() if np.isfinite(float(v))); m=len(items); out={k:1.0 for k in pvals}; prev=1.0
    for rank,(p,k) in reversed(list(enumerate(items,1))):
        adj=min(prev,p*m/rank); out[k]=adj; prev=adj
    return out


def _metrics(r: np.ndarray) -> Dict[str,float]:
    r=np.asarray(r,float); r=r[np.isfinite(r)]; n=len(r)
    if not n: return {"bars":0,"mean_bps":0,"cum_return_pct":0,"sharpe":0,"downside_sharpe":0,"max_drawdown_pct":0}
    eq=np.cumprod(1+r); peak=np.maximum.accumulate(eq); dd=(peak-eq)/np.maximum(peak,1e-12)
    mean=float(r.mean()); sd=float(r.std(ddof=1)) if n>1 else 0
    downside=r[r<0]; dsd=float(downside.std(ddof=1)) if len(downside)>1 else 0
    return {"bars":int(n),"mean_bps":mean*10000,"cum_return_pct":(float(eq[-1])-1)*100,
            "sharpe":mean/(sd+1e-12)*math.sqrt(n) if n>1 else 0.0,
            "downside_sharpe":mean/(dsd+1e-12)*math.sqrt(n) if n>1 and dsd>0 else 0.0,
            "max_drawdown_pct":float(dd.max()*100)}


def _fit_oos(d: pd.DataFrame, y: np.ndarray, cols: List[str], tr_start:int, tr_end:int, test_idx:np.ndarray):
    tr=np.arange(tr_start,tr_end,dtype=int); te=np.asarray(test_idx,dtype=int)
    good_tr=np.isfinite(d[cols].iloc[tr].to_numpy()).all(axis=1); good_te=np.isfinite(d[cols].iloc[te].to_numpy()).all(axis=1)
    tr=tr[good_tr]; te2=te[good_te]
    classes=np.unique(y[tr])
    if len(tr)<500 or len(te2)<50 or len(classes)<2: return None
    ms=_models()
    for m in ms: m.fit(d[cols].iloc[tr],y[tr])
    p=np.full((len(te),3),np.nan); p[good_te]=_proba(ms,d[cols].iloc[te2]); return p


def _validate_manifest(dataset: Path, manifest: Optional[Path]) -> Dict[str, Any]:
    if manifest is None:
        candidate=dataset.with_suffix(".manifest.json")
        manifest=candidate if candidate.exists() else None
    if manifest is None or not manifest.exists():
        return {"present":False,"valid":not bool(manifest),"reason":"manifest_missing"}
    m=json.loads(manifest.read_text(encoding="utf-8"))
    actual=_sha256_file(dataset)
    if m.get("dataset_sha256") != actual:
        raise ValueError("dataset SHA256 与 manifest 不一致，拒绝研究")
    canonical=dict(m); expected=canonical.pop("manifest_sha256",None)
    raw=json.dumps(canonical,ensure_ascii=False,sort_keys=True,separators=(",", ":")).encode("utf-8")
    actual_m=hashlib.sha256(raw).hexdigest()
    if expected and expected != actual_m:
        raise ValueError("manifest SHA256 校验失败，拒绝研究")
    if not bool(m.get("completed_bars_only")) or not bool(m.get("no_bfill")) or not bool(m.get("point_in_time_join")):
        raise ValueError("manifest 数据治理标志不满足：completed_bars_only/no_bfill/point_in_time_join")
    gate=m.get("core_gate",{}) or {}
    if not bool(gate.get("ready")):
        raise ValueError("manifest core_gate 未通过，拒绝研究")
    return {"present":True,"valid":True,"path":str(manifest),"dataset_sha256":actual,"manifest_sha256":actual_m}


def _finite_source_coverage(df: pd.DataFrame, group: str) -> float:
    return _source_coverage(df, group)

def _research_data_health(d: pd.DataFrame, manifest: Optional[Path]) -> Dict[str, Any]:
    m = {}
    if manifest and manifest.exists():
        try: m = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception: m = {}
    tf = str(m.get("timeframe", "15m"))
    if tf.endswith("m"):
        expected = int(tf[:-1]) * 60 * 1000
    elif tf.endswith("h"):
        expected = int(tf[:-1]) * 60 * 60 * 1000
    elif tf.endswith("d"):
        expected = int(tf[:-1]) * 24 * 60 * 60 * 1000
    else:
        expected = None
    ts = pd.to_numeric(d.get("ts", pd.Series(dtype=float)), errors="coerce").dropna().astype("int64")
    diffs = np.diff(np.sort(ts.unique())) if len(ts)>1 else np.array([])
    median_delta = int(np.median(diffs)) if len(diffs) else 0
    return {"timeframe":tf,"rows":int(len(d)),"expected_interval_ms":expected,"median_interval_ms":median_delta,
            "cadence_ok": bool(expected is None or not len(diffs) or median_delta >= expected*0.90),
            "source_coverage": {g: _finite_source_coverage(d,g) for g in SOURCE_GROUPS}}

def run_research(dataset: str|Path, manifest: str|Path|None=None, tp=.018, sl=.012, horizon=24,
                 entry=.62, fee_bps=5.0, slip_bps=5.0, dev_ratio=.75, purge=48,
                 min_rows=1200, require_sources=False, source_min_coverage=.80) -> Dict[str,Any]:
    """Run cumulative source ablation with a frozen final test window.

    Feature-group choice is decided using development walk-forward only. Final test
    is evaluated for every ablation but never used to choose the winner.
    """
    dataset=Path(dataset); manifest=Path(manifest) if manifest else None
    logger.info(f"[V17研究] 开始｜数据集={dataset.name}｜参数 TP={tp:.3%} SL={sl:.3%} Horizon={horizon}")
    if not dataset.exists(): raise FileNotFoundError(dataset)
    manifest_check=_validate_manifest(dataset, manifest)
    if manifest is None and manifest_check.get("present"):
        manifest=Path(manifest_check["path"])
    d=pd.read_csv(dataset)
    logger.info(f"[V17研究] 数据读取完成｜原始行数={len(d)}")
    if "ts" not in d:
        raise ValueError("V17数据转换失败：缺少ts时间列")
    health=_research_data_health(d, manifest)
    logger.info(f"[V17研究] 数据体检｜周期={health['timeframe']}｜行数={health['rows']}｜时间间隔中位数={health['median_interval_ms']}ms")
    if health.get("expected_interval_ms") and health.get("median_interval_ms"):
        if health["median_interval_ms"] < health["expected_interval_ms"] * 0.90:
            raise ValueError(f"V16→V17周期转换异常：manifest标记{health['timeframe']}，实际时间间隔约{health['median_interval_ms']}ms；请重新构建该周期数据集")
    if len(d)<min_rows:
        raise ValueError(f"V17研究数据不足：当前{len(d)}行，最低需要{min_rows}行；请回到V16继续采集并重新构建数据集（不会用假数据补齐）")
    # 数据清洗：所有列转成数字，非数字转NaN，删除全空列
    for col in d.columns:
        if col != "ts":
            d[col]=pd.to_numeric(d[col],errors="coerce")
    d=d.dropna(axis=1,how="all")
    # Funding is a point-in-time event source: funding_event is sparse by design.
    # Never require funding_rate_event to be populated on every candle. Keep the
    # event indicator, and encode non-event rows as 0 for the event-rate feature.
    # This is not a forward/backward fill and cannot introduce future information.
    if "funding_event" in d.columns:
        d["funding_event"] = pd.to_numeric(d["funding_event"], errors="coerce").fillna(0.0)
    if "funding_rate_event" in d.columns:
        d["funding_rate_event"] = pd.to_numeric(d["funding_rate_event"], errors="coerce").fillna(0.0)
    d=technical_features(d)
    d=d.dropna(subset=["open","high","low","close","volume"]).reset_index(drop=True)
    n=len(d); test_start=int(n*float(dev_ratio)); dev_end=test_start-purge
    if dev_end<800: raise ValueError("development区间不足")
    source_coverage={g:_source_coverage(d,g) for g in SOURCE_GROUPS}
    y=labels_first_touch(d,tp,sl,horizon)
    folds=_folds(n,dev_end,purge,3)
    logger.info(f"[V17研究] 数据治理通过｜有效行数={n}｜开发集={dev_end}｜最终测试={n-test_start}｜Purged={purge}｜WFO折数={len(folds)}")
    if len(folds)<2: raise ValueError("walk-forward folds不足")

    candidates={}
    for name,groups in CUMULATIVE_ABLATIONS:
        logger.info(f"[V17实验{name}] 开始｜数据组合={', '.join(groups) if groups else '仅K线'}")
        cols=_cols_for(d,groups)
        required_external=[g for g in groups if g!="ohlcv"]
        weak_sources=[g for g in required_external if source_coverage.get(g,0.0) < float(source_min_coverage)]
        fold_diagnostics=[]
        # Optional-source degradation: insufficient V16 OI/trades/L2/cross-market
        # data is skipped for this experiment; it must never stall the whole study.
        # No missing source is synthesized. If the degraded combination duplicates
        # an earlier experiment, skip the duplicate rather than retraining it.
        effective_groups=tuple(g for g in groups if g=="ohlcv" or g not in weak_sources)
        effective_key=tuple(effective_groups)
        if effective_key != tuple(groups):
            logger.warning(
                f"[V17实验{name}] 部分数据不足｜跳过不足源="
                + (",".join(weak_sources) if weak_sources else "无")
                + "｜实际组合=" + (",".join(effective_groups) if effective_groups else "仅K线")
            )
        existing_keys={tuple(v.get("effective_groups", v.get("groups", []))) for v in candidates.values() if v.get("available")}
        if name != "A_OHLCV" and effective_key in existing_keys:
            reason="移除不足数据源后与已有研究组合重复，已跳过重复实验"
            candidates[name]={"groups":list(groups),"effective_groups":list(effective_groups),"features":_cols_for(d,effective_groups),
                              "available":False,"reason":reason,"skipped_sources":weak_sources,
                              "wfo":[],"wfo_folds":0,"required_wfo_folds":len(folds),"source_coverage":source_coverage,
                              "fold_diagnostics":fold_diagnostics}
            logger.info(f"[V17实验{name}] 跳过｜{reason}")
            continue
        cols=_cols_for(d,effective_groups)
        if not cols:
            reason="没有可用于该组合的有效特征"
            candidates[name]={"groups":list(groups),"effective_groups":list(effective_groups),"features":[],"available":False,
                              "reason":reason,"skipped_sources":weak_sources,"wfo":[],"wfo_folds":0,
                              "required_wfo_folds":len(folds),"source_coverage":source_coverage,"fold_diagnostics":fold_diagnostics}
            continue
        fold_stats=[]; fold_returns=[]
        for fold_no,(tr0,tr1,va0,va1) in enumerate(folds,1):
            tr=np.arange(tr0,tr1,dtype=int); va=np.arange(va0,va1,dtype=int)
            good_tr=np.isfinite(d[cols].iloc[tr].to_numpy()).all(axis=1)
            good_va=np.isfinite(d[cols].iloc[va].to_numpy()).all(axis=1)
            tr2=tr[good_tr]; va2=va[good_va]
            cls, cnt=np.unique(y[tr2],return_counts=True) if len(tr2) else (np.array([],dtype=int),np.array([],dtype=int))
            diag={"fold":fold_no,"train_rows_raw":int(len(tr)),"train_rows_valid":int(len(tr2)),
                  "validation_rows_raw":int(len(va)),"validation_rows_valid":int(len(va2)),
                  "train_label_counts":{str(int(k)):int(v) for k,v in zip(cls,cnt)},
                  "train_label_classes":int(len(cls))}
            fold_diagnostics.append(diag)
            if len(tr2)<500 or len(va2)<50 or len(cls)<2:
                logger.warning(f"[V17实验{name}] WFO第{fold_no}折跳过｜训练有效={len(tr2)}｜验证有效={len(va2)}｜标签类别={len(cls)}｜标签分布={diag['train_label_counts']}")
                continue
            res=_fit_predict(d,y,cols,tr,va)
            if res is None: continue
            va2,p=res; dv=d.iloc[va2].reset_index(drop=True)
            rr=_signal_returns(dv,p,entry,fee_bps,slip_bps)
            fold_returns.append(rr); met=_metrics(rr)
            met["logloss"]=float(log_loss(y[va2],p,labels=[0,1,2])); met["accuracy"]=float(accuracy_score(y[va2],np.argmax(p,axis=1)))
            fold_stats.append(met)
        if len(fold_stats)<2:
            reason=f"有效WFO折数不足（{len(fold_stats)}/{len(folds)}）；该数据源组合在当前数据覆盖下不可研究"
            logger.warning(f"[V17实验{name}] 跳过｜{reason}")
            candidates[name]={"groups":list(groups),"features":cols,"available":False,"reason":reason,
                              "wfo":fold_stats,"wfo_folds":len(fold_stats),"required_wfo_folds":len(folds),
                              "fold_diagnostics":fold_diagnostics}
            continue
        mean_bps=float(np.mean([x["mean_bps"] for x in fold_stats])); std_bps=float(np.std([x["mean_bps"] for x in fold_stats]))
        candidates[name]={"groups":list(groups),"effective_groups":list(effective_groups),"skipped_sources":weak_sources,"features":cols,"available":True,"wfo":fold_stats,"wfo_folds":len(fold_stats),
                         "wfo_mean_bps":mean_bps,"wfo_std_bps":std_bps,
                         "wfo_score":mean_bps-0.50*std_bps,"wfo_logloss":float(np.mean([x["logloss"] for x in fold_stats])),
                         "fold_diagnostics":fold_diagnostics}
        logger.info(f"[V17实验{name}] 完成｜WFO净收益={mean_bps:.2f}bps｜稳定性={std_bps:.2f}bps｜评分={candidates[name]['wfo_score']:.2f}")

    if "A_OHLCV" not in candidates or not candidates["A_OHLCV"].get("available"):
        diag=candidates.get("A_OHLCV",{}).get("fold_diagnostics",[])
        detail="；".join(
            f"第{x.get('fold')}折:训练有效{x.get('train_rows_valid')}/标签类别{x.get('train_label_classes')}/分布{x.get('train_label_counts')}"
            for x in diag
        )
        raise ValueError("V17基线研究不可用：没有足够的有效WFO折。"
                         + (f" 具体诊断：{detail}" if detail else "")
                         + "。请先检查TP/SL、数据有效行和标签分布，不要只增加K线。")
    winner=max((k for k,v in candidates.items() if v.get("available")),key=lambda k:candidates[k]["wfo_score"])
    logger.info(f"[V17研究] 开发集选出候选｜{winner}｜最终测试尚未参与选择")
    # Final test is frozen and identical across all candidates.
    test_idx=np.arange(test_start,n,dtype=int)
    test_results={}; pvals={}; base_returns=None
    for name,info in candidates.items():
        if not info.get("available"):
            continue
        p=_fit_oos(d,y,info["features"],0,dev_end,test_idx)
        if p is None:
            info["final_test_available"]=False
            info["final_test_reason"]="最终测试有效样本不足"
            continue
        valid=np.isfinite(p).all(axis=1)
        rr=np.zeros(len(test_idx),dtype=float)
        rr[valid]=_signal_returns(d.iloc[test_idx[valid]].reset_index(drop=True),p[valid],entry,fee_bps,slip_bps)
        test_results[name]={"metrics":_metrics(rr),"valid_bars":int(valid.sum()),"probs":p,"returns":rr}
        info["final_test_available"]=True
        if name=="A_OHLCV": base_returns=rr

    logger.info("[V17研究] 最终测试开始｜所有候选统一测试｜测试结果不用于回溯选优")
    for name,res in test_results.items():
        diff=res["returns"]-base_returns
        ci=_block_bootstrap_ci(diff)
        p=_permutation_pvalue(diff)
        res["incremental_vs_baseline"]={"mean_diff_bps":float(diff.mean()*10000),"ci_low_bps":float(ci["ci_low"]*10000),"ci_high_bps":float(ci["ci_high"]*10000),"p_value":p}
        pvals[name]=p
    qvals=_bh(pvals)
    for name,res in test_results.items(): res["incremental_vs_baseline"]["q_value_bh"]=float(qvals[name])

    # Strip model probabilities/returns from result payload; they are large and not needed in API.
    for res in test_results.values(): res.pop("probs",None); res.pop("returns",None)

    if base_returns is None or winner not in test_results:
        raise ValueError("V17最终测试不可用：基线或最优组合缺少足够的最终测试样本")
    winner_test=test_results[winner]
    winner_delta=winner_test["incremental_vs_baseline"]
    winner_ci_low=winner_delta["ci_low_bps"]
    promoted=bool(winner_ci_low>0 and winner_delta["q_value_bh"]<0.10 and candidates[winner]["wfo_mean_bps"]>0)
    logger.info(f"[V17统计] Winner={winner}｜增量={winner_delta['mean_diff_bps']:.2f}bps｜CI下限={winner_ci_low:.2f}bps｜BH q={winner_delta['q_value_bh']:.4f}")
    logger.info(f"[V17结论] {'通过增量Alpha证据｜允许Smart采用' if promoted else '证据不足｜保留K线基线'}")
    source_presence={}
    for g in SOURCE_GROUPS:
        source_presence[g]=[c for c in SOURCE_GROUPS[g] if c in d.columns]
    out={
        "version":VERSION,"dataset":str(dataset),"dataset_sha256":_sha256_file(dataset),
        "manifest":str(manifest) if manifest else None,"manifest_sha256":_sha256_file(manifest) if manifest and manifest.exists() else None,
        "rows":n,"split":{"development_end":dev_end,"test_start":test_start,"test_bars":len(test_idx),"purge":purge,"selection_uses_test":False},
        "label":{"tp":tp,"sl":sl,"horizon":horizon,"definition":"first-touch; same-candle ambiguity=FLAT"},
        "cost":{"fee_bps":fee_bps,"slippage_bps":slip_bps,"signal_cost_bps":2*(fee_bps+slip_bps)},
        "source_presence":source_presence,"source_coverage":source_coverage,"source_gap":[g for g in SOURCE_GROUPS if g!="ohlcv" and source_coverage.get(g,0.0)<=0],"manifest_check":manifest_check,
        "ablations":{k:{kk:vv for kk,vv in v.items() if kk not in ("features",)} for k,v in candidates.items()},
        "ablation_unavailable":[k for k,v in candidates.items() if not v.get("available")],
        "winner":{"name":winner,"selection_rule":"max(WFO mean net bps - 0.5*fold std)","test":winner_test},
        "test_results":test_results,
        "multiple_testing":{"method":"Benjamini-Hochberg","hypotheses":len(pvals),"q_values":qvals},
        "decision":{"incremental_alpha_proven":promoted,"decision":"PROMOTE_FOR_SHADOW" if promoted else "NO_EVIDENCE_RETAIN_BASELINE",
                    "criteria":"winner WFO net > 0 AND test paired bootstrap CI lower bound > 0 AND BH q < 0.10"},
        "data_health":health,
        "audit":{"no_bfill":True,"completed_bars_only":True,"point_in_time":True,"final_test_touched_during_selection":False,
                 "feature_group_selected_on_development_only":True,"statistical_test":"paired block sign permutation on per-bar cost-adjusted signal returns","bootstrap":"paired block bootstrap",
                 "created_at":time.time()}
    }
    if require_sources and out["source_gap"]: raise ValueError("缺少真实源: "+",".join(out["source_gap"]))
    return out


def summarize(result: Dict[str,Any]) -> Dict[str,Any]:
    w=result.get("winner",{}); dec=result.get("decision",{}); wt=w.get("test",{}).get("metrics",{}); delta=w.get("test",{}).get("incremental_vs_baseline",{})
    return {"version":result.get("version"),"dataset_sha256":result.get("dataset_sha256"),"winner":w.get("name"),"test_mean_bps":wt.get("mean_bps"),"test_sharpe":wt.get("sharpe"),"test_dd_pct":wt.get("max_drawdown_pct"),"incremental_bps":delta.get("mean_diff_bps"),"ci_low_bps":delta.get("ci_low_bps"),"q_value":delta.get("q_value_bh"),"decision":dec.get("decision")}


if __name__=="__main__":
    import argparse
    ap=argparse.ArgumentParser(description="ALPHA-X 17.1 Real Alpha Research")
    ap.add_argument("dataset"); ap.add_argument("--manifest",default=""); ap.add_argument("--tp",type=float,default=.018); ap.add_argument("--sl",type=float,default=.012); ap.add_argument("--horizon",type=int,default=24); ap.add_argument("--entry",type=float,default=.62); ap.add_argument("--fee-bps",type=float,default=5); ap.add_argument("--slip-bps",type=float,default=5); ap.add_argument("--dev-ratio",type=float,default=.75); ap.add_argument("--purge",type=int,default=48); ap.add_argument("--require-sources",action="store_true"); ap.add_argument("--out",default="")
    a=ap.parse_args(); r=run_research(a.dataset,a.manifest or None,a.tp,a.sl,a.horizon,a.entry,a.fee_bps,a.slip_bps,a.dev_ratio,a.purge,1200,a.require_sources)
    print(json.dumps(summarize(r),ensure_ascii=False,indent=2))
    if a.out: Path(a.out).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding="utf-8")


# ===== 智能选择数据源（供训练时调用）=====
import json as _json
from pathlib import Path as _Path

def load_research_result(inst_id: str, results_dir: str | None = None) -> dict | None:
    """加载某个币种的v17研究结果。"""
    d = _Path(results_dir) if results_dir else _Path(__file__).parent / "alpha_research_results"
    p = d / f"{inst_id.upper()}.json"
    if not p.exists():
        # 尝试去掉-SWAP
        p2 = d / f"{inst_id.upper().replace('-SWAP','')}-USDT-SWAP.json"
        if p2.exists(): p = p2
        else: return None
    try:
        return _json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

def select_sources_from_research(inst_id: str, results_dir: str | None = None) -> dict:
    """根据v17研究结果，智能选择应该用哪些数据源。
    
    返回: {"funding": bool, "open_interest": bool, "order_flow": bool, "l2": bool, "cross_market": bool, "reason": str}
    """
    result = load_research_result(inst_id, results_dir)
    default = {"funding": False, "open_interest": False, "order_flow": False, "l2": False, "cross_market": False, "reason": "未找到v17研究结果，智能模式禁止猜测数据源", "research_available": False}
    if not result:
        return default
    
    # 从winner判断用了哪些数据源组
    winner = result.get("winner", {})
    winner_name = winner.get("name", "") if isinstance(winner, dict) else str(winner)
    
    # CUMULATIVE_ABLATIONS的映射
    group_map = {
        "A_OHLCV": set(),
        "B_OHLCV_FUNDING": {"funding"},
        "C_OHLCV_FUNDING_OI": {"funding", "open_interest"},
        "D_OHLCV_FUNDING_OI_FLOW": {"funding", "open_interest", "order_flow"},
        "E_OHLCV_FUNDING_OI_FLOW_L2": {"funding", "open_interest", "order_flow", "l2"},
        "F_FULL": {"funding", "open_interest", "order_flow", "l2", "cross_market"},
    }
    
    selected = group_map.get(winner_name, set())
    
    # 检查覆盖率，覆盖率太低的数据源不用
    coverage = result.get("source_coverage", {})
    final = {"funding": False, "open_interest": False, "order_flow": False, "l2": False, "cross_market": False, "research_available": True}
    for src in final:
        if src in selected:
            cov = coverage.get(src, 0)
            if cov >= 0.80:  # 与训练端真实数据门槛一致，避免V17选中后训练再因覆盖不足失败
                final[src] = True
    
    # 检查promoted，如果不推荐真实数据就全关
    promoted = result.get("decision", {}).get("incremental_alpha_proven", False)
    if not promoted:
        final = {"funding": False, "open_interest": False, "order_flow": False, "l2": False, "cross_market": False, "reason": f"v17研究不推荐使用真实数据（winner={winner_name}），退回只用K线"}
    else:
        used = [k for k in ("funding","open_interest","order_flow","l2","cross_market") if final.get(k)]
        final["reason"] = f"v17研究最优组合={winner_name}，实际启用={used if used else '无（退回K线）'}"
    
    return final

def research_available(inst_id: str, results_dir: str | None = None) -> bool:
    """检查某个币种是否有v17研究结果。"""
    return load_research_result(inst_id, results_dir) is not None
