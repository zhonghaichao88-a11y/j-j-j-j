"""ALPHA-X 多时间周期共振模块：4H/1H/15M/5M。"""
from __future__ import annotations
import math
import numpy as np

FRAMES=("4h","1h","15m","5m")

def _completed(rows):
    if not rows: return []
    # OKX返回的最后一根可能未收盘；时间戳+周期判断比简单丢最后一根更稳。
    import time as _time
    out=[]
    ms_map={"5m":300000,"15m":900000,"1h":3600000,"4h":14400000}
    for r in rows:
        try:
            if isinstance(r,dict): ts=float(r.get("ts",r.get("timestamp",0)))
            else: ts=float(r[0])
            tf=ms_map.get(_CURRENT_FRAME,900000)
            now_ms=_time.time()*1000
            if ts+tf<=now_ms+1000: out.append(r)
        except Exception: pass
    return out

_CURRENT_FRAME="15m"

def _direction(rows):
    if not rows or len(rows)<30: return 0.0,"数据不足"
    vals=[]
    for r in rows[-40:]:
        try:
            o=float(r[1] if not isinstance(r,dict) else r.get("open")); c=float(r[4] if not isinstance(r,dict) else r.get("close"))
            if o>0: vals.append(c/o-1)
        except Exception: pass
    if len(vals)<20: return 0.0,"数据不足"
    x=np.asarray(vals[-20:]); mean=float(x.mean()); total=float(np.prod(1+x)-1)
    # 方向分数：近期累计方向为主，单根噪声不会决定结果。
    score=max(-1.0,min(1.0,total/0.04))
    return score, ("偏多" if score>0.20 else "偏空" if score<-0.20 else "中性")


def analyze_multi_timeframe(symbol, client, base_timeframe="15m"):
    global _CURRENT_FRAME
    rows={}; details={}
    for tf in FRAMES:
        try:
            _CURRENT_FRAME=tf
            raw=client.get_ohlcv(symbol=symbol,timeframe=tf,limit=100)
            rr=_completed(raw)
            rows[tf]=rr
            sc,label=_direction(rr)
            details[tf]={"score":float(sc),"label":label,"bars":len(rr)}
        except Exception as exc:
            rows[tf]=[]; details[tf]={"score":0.0,"label":"获取失败","bars":0,"error":str(exc)}
    scores=[v["score"] for v in details.values() if v.get("label")!="获取失败" and v.get("label")!="数据不足"]
    if not scores:
        return {"direction":"NEUTRAL","label":"多周期数据不足","agreement":0.0,"score":0.0,"size_multiplier":1.0,"timeframes":details}
    # 4H/1H权重略高，5M只负责时机，不单独推翻大周期。
    weights={"4h":0.30,"1h":0.30,"15m":0.25,"5m":0.15}
    weighted=sum(details[k]["score"]*weights[k] for k in FRAMES if k in details)
    signs=[1 if x>0.15 else -1 if x<-0.15 else 0 for x in scores]
    directional=[x for x in signs if x]
    agreement=(abs(sum(directional))/len(directional)) if directional else 0.0
    direction="LONG" if weighted>0.18 else "SHORT" if weighted<-0.18 else "NEUTRAL"
    if agreement>=0.75 and direction!="NEUTRAL": mult=1.05; label=f"{direction}强共振"
    elif agreement>=0.50 and direction!="NEUTRAL": mult=0.80; label=f"{direction}中等共振"
    elif direction!="NEUTRAL": mult=0.60; label=f"{direction}周期分歧"
    else: mult=0.70; label="多周期未形成明确共振"
    return {"direction":direction,"label":label,"agreement":float(agreement),"score":float(weighted),
            "size_multiplier":float(mult),"timeframes":details}
