"""Portfolio risk kernel: exposure, historical VaR/CVaR, drawdown and hard gates."""
from __future__ import annotations
import math, statistics

def var_cvar(returns, confidence=.99):
    r=sorted(float(x) for x in returns if math.isfinite(float(x)))
    if not r:return {'var':0.0,'cvar':0.0,'n':0}
    idx=max(0,min(len(r)-1,int(math.floor((1-confidence)*len(r))))); var=-r[idx]
    tail=[x for x in r if x<=r[idx]] or [r[idx]]; cvar=-statistics.mean(tail)
    return {'var':var,'cvar':cvar,'n':len(r)}

def drawdown(equity):
    peak=-math.inf; maxdd=0.0
    for x in equity:
        x=float(x); peak=max(peak,x); maxdd=max(maxdd,(peak-x)/peak if peak>0 else 0)
    return maxdd

def gate(equity, returns, gross_exposure, max_gross=1.0, max_dd=.15, max_cvar=.05):
    dd=drawdown(equity); vc=var_cvar(returns); reasons=[]
    if gross_exposure>max_gross: reasons.append('gross_exposure')
    if dd>max_dd: reasons.append('drawdown')
    if vc['cvar']>max_cvar: reasons.append('cvar')
    return {'ok':not reasons,'reasons':reasons,'drawdown':dd,'risk':vc,'gross_exposure':gross_exposure}
