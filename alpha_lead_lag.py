"""ALPHA-X Lead-Lag 领先/跟随关系模块。
只提供实时跨资产领先关系上下文与轻微仓位调整，不负责硬拦截。
数据直接来自现有 OKX OHLCV 客户端；只使用已完成K线。
"""
from __future__ import annotations
import math
import time
import numpy as np

# 常见市场领先资产。目标币本身不会拿来和自己比较。
LEADERS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
CACHE_TTL = 60.0
_CACHE = {}


def _sym(s):
    s = str(s or "").upper()
    if s.endswith("-SWAP"):
        return s
    if s.endswith("-USDT"):
        return s + "-SWAP"
    return s


def _rows_completed(rows, tf):
    if not rows:
        return []
    import time as _time
    ms = {"5m":300000, "15m":900000, "1h":3600000, "4h":14400000}.get(tf, 900000)
    out=[]
    now=_time.time()*1000
    for r in rows:
        try:
            ts=float(r.get("ts", r.get("timestamp", 0))) if isinstance(r,dict) else float(r[0])
            if ts + ms <= now + 1000:
                out.append(r)
        except Exception:
            continue
    return out


def _closes(rows):
    vals=[]
    for r in rows:
        try:
            v=float(r.get("close")) if isinstance(r,dict) else float(r[4])
            if math.isfinite(v) and v>0: vals.append(v)
        except Exception: pass
    return np.asarray(vals, dtype=float)


def _returns(rows):
    c=_closes(rows)
    if len(c)<40: return np.asarray([], dtype=float)
    return np.diff(np.log(c))


def _best_lag(leader_r, target_r, max_lag=8):
    n=min(len(leader_r),len(target_r))
    if n<50: return None
    a=leader_r[-n:]; b=target_r[-n:]
    best=None
    # lag=1 means leader's previous bar is compared with target's current bar.
    for lag in range(1,max_lag+1):
        x=a[:-lag]; y=b[lag:]
        if len(x)<30: continue
        sx=float(np.std(x)); sy=float(np.std(y))
        if sx<1e-9 or sy<1e-9: continue
        corr=float(np.corrcoef(x,y)[0,1])
        direction=float(np.mean(np.sign(x)*np.sign(y)))
        score=0.70*corr+0.30*direction
        item=(score,corr,direction,lag)
        if best is None or item[0]>best[0]: best=item
    return best


def analyze_lead_lag(symbol, client, base_timeframe="15m"):
    target=_sym(symbol)
    key=(target,str(base_timeframe))
    now=time.time()
    cached=_CACHE.get(key)
    if cached and now-cached.get("time",0)<CACHE_TTL:
        return dict(cached["data"], cached=True)

    target_raw=client.get_ohlcv(symbol=target,timeframe=base_timeframe,limit=260)
    target_rows=_rows_completed(target_raw,base_timeframe)
    tr=_returns(target_rows)
    details=[]
    for leader in LEADERS:
        if _sym(leader)==target: continue
        try:
            raw=client.get_ohlcv(symbol=leader,timeframe=base_timeframe,limit=260)
            lr=_returns(_rows_completed(raw,base_timeframe))
            best=_best_lag(lr,tr)
            if not best: continue
            score,corr,direction,lag=best
            # 15m基准下每个lag对应15分钟；其它周期按分钟换算。
            minutes={"5m":5,"15m":15,"1h":60,"4h":240}.get(base_timeframe,15)*lag
            reliability=max(0.0,min(1.0,0.5+0.5*score))
            details.append({"leader":leader,"target":target,"lag_bars":lag,"lead_minutes":minutes,
                            "correlation":corr,"direction_consistency":direction,
                            "score":score,"reliability":reliability})
        except Exception as exc:
            details.append({"leader":leader,"target":target,"error":str(exc)})

    valid=[x for x in details if "score" in x]
    if not valid:
        data={"enabled":True,"direction":"NEUTRAL","label":"领先关系数据不足","score":0.0,
              "reliability":0.0,"size_multiplier":1.0,"relationships":details}
    else:
        # 只有关系足够稳定且当前领先方向与目标近期方向一致时才给增强；冲突只轻微降仓，不封单。
        v=max(valid,key=lambda x: x["reliability"])
        target_recent=float(np.sign(np.mean(tr[-3:]))) if len(tr)>=3 else 0.0
        leader_recent=0.0
        try:
            lr=_returns(_rows_completed(client.get_ohlcv(symbol=v["leader"],timeframe=base_timeframe,limit=20),base_timeframe))
            leader_recent=float(np.sign(np.mean(lr[-3:]))) if len(lr)>=3 else 0.0
        except Exception: pass
        raw=float(v["score"])
        direction="LONG" if raw>0.12 else "SHORT" if raw<-0.12 else "NEUTRAL"
        aligned=(direction=="LONG" and target_recent>0) or (direction=="SHORT" and target_recent<0)
        strong=abs(raw)>=0.28 and float(v["reliability"])>=0.64
        if strong and aligned:
            mult=1.05; label=f"{v['leader']}领先{v['lead_minutes']}分钟，目标币正在跟随"
        elif strong and leader_recent!=0 and target_recent!=leader_recent:
            mult=0.90; label=f"{v['leader']}领先{v['lead_minutes']}分钟，但目标币暂未跟随"
        else:
            mult=1.0; label="领先关系一般，仅作参考"
        data={"enabled":True,"direction":direction,"label":label,"score":raw,
              "reliability":float(v["reliability"]),"size_multiplier":mult,"relationships":details}
    _CACHE[key]={"time":now,"data":data}
    return dict(data, cached=False)
