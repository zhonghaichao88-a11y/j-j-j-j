"""ALPHA-X 10.0 statistical model governance.
No automatic promotion is allowed without out-of-sample and shadow evidence.
"""
from __future__ import annotations
import math
from typing import Any, Dict


def _wilson(wins:int,n:int,z:float=1.96):
    if n<=0:return (0.0,1.0)
    p=wins/n; den=1+z*z/n; ctr=(p+z*z/(2*n))/den; half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return max(0,ctr-half),min(1,ctr+half)


def compare(champion:Dict[str,Any], challenger:Dict[str,Any], min_shadow=40, min_delta=0.02)->Dict[str,Any]:
    c=champion or {}; h=challenger or {}
    cs=c.get("shadow") or {}; hs=h.get("shadow") or {}
    cn=int(cs.get("trades",0)); hn=int(hs.get("trades",0));
    cp=float(cs.get("pnl",0)); hp=float(hs.get("pnl",0));
    cw=int(cs.get("wins",0)); hw=int(hs.get("wins",0));
    cwr=cw/max(cn,1); hwr=hw/max(hn,1)
    lo,hi=_wilson(hw,hn)
    delta=hwr-cwr
    eligible=(hn>=min_shadow and hp>0 and delta>=min_delta and lo>cwr)
    return {"eligible":eligible,"champion_trades":cn,"challenger_trades":hn,"champion_pnl":cp,"challenger_pnl":hp,
            "win_rate_delta":delta,"challenger_win_rate_ci95":[lo,hi],
            "reason":"statistically_stronger_shadow" if eligible else "insufficient_or_not_stronger"}
