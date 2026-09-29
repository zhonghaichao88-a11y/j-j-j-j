"""ALPHA-X 市场状态自适应模块。只做环境识别与仓位调节，不负责硬拦截。"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd


def _num(x, default=0.0):
    try:
        v=float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def classify_regime(candles: pd.DataFrame) -> dict:
    """根据已完成K线识别市场状态。

    状态：趋势上涨/趋势下跌/震荡/高波动/极端波动/过渡。
    返回 size_multiplier 只用于仓位调节：好趋势可适度加仓，风险环境降仓；不作为开仓硬门槛。
    """
    d=candles.copy()
    if d.empty or len(d)<80:
        return {"regime":"UNKNOWN","label":"数据不足","score":0.0,"stress":0.5,"size_multiplier":1.0,"reasons":["样本不足"]}
    c=pd.to_numeric(d["close"],errors="coerce").dropna()
    h=pd.to_numeric(d["high"],errors="coerce").reindex(c.index)
    l=pd.to_numeric(d["low"],errors="coerce").reindex(c.index)
    ret=c.pct_change().replace([np.inf,-np.inf],np.nan).dropna()
    if len(ret)<60:
        return {"regime":"UNKNOWN","label":"数据不足","score":0.0,"stress":0.5,"size_multiplier":1.0,"reasons":["收益序列不足"]}
    ema_fast=c.ewm(span=20,adjust=False).mean(); ema_slow=c.ewm(span=50,adjust=False).mean()
    trend=(_num(ema_fast.iloc[-1]/max(ema_slow.iloc[-1],1e-12)-1)*100)
    recent_ret=_num(c.iloc[-1]/max(c.iloc[-21],1e-12)-1)
    atr=(h-l).rolling(14).mean()/c
    atr_now=_num(atr.iloc[-1]); atr_med=_num(atr.iloc[-60:].median(),atr_now)
    vol_ratio=atr_now/max(atr_med,1e-9)
    # 趋势效率：净位移 / 路径长度，越接近1越单边。
    path=float(ret.abs().tail(40).sum())
    displacement=abs(_num(c.iloc[-1]/max(c.iloc[-41],1e-12)-1))
    efficiency=displacement/max(path,1e-9)
    stress=max(0.0,min(1.0,0.35*min(vol_ratio/2.5,1.0)+0.65*(1-efficiency)))
    reasons=[]
    if vol_ratio>=2.2:
        regime="EXTREME_VOL"; label="极端波动"; mult=0.55; reasons.append("波动率显著高于近期常态")
    elif vol_ratio>=1.55:
        regime="HIGH_VOL"; label="高波动"; mult=0.70; reasons.append("波动率高于近期常态")
    elif trend>=0.35 and efficiency>=0.25 and recent_ret>0:
        regime="TREND_UP"; label="趋势上涨"; mult=1.05; reasons.append("短中期方向向上且趋势效率较高")
    elif trend<=-0.35 and efficiency>=0.25 and recent_ret<0:
        regime="TREND_DOWN"; label="趋势下跌"; mult=1.05; reasons.append("短中期方向向下且趋势效率较高")
    elif efficiency<0.18:
        regime="RANGE"; label="震荡"; mult=0.80; reasons.append("价格来回波动，趋势效率偏低")
    else:
        regime="TRANSITION"; label="市场过渡"; mult=0.85; reasons.append("趋势与波动特征正在切换")
    return {"regime":regime,"label":label,"score":float(max(-1,min(1,trend/2))),"stress":float(stress),
            "size_multiplier":float(mult),"trend_pct":float(trend),"recent_return_pct":float(recent_ret*100),
            "vol_ratio":float(vol_ratio),"trend_efficiency":float(efficiency),"reasons":reasons}
