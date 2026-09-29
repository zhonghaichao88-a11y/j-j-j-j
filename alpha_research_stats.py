"""Research statistics with conservative uncertainty and multiple-testing controls."""
from __future__ import annotations
import math

def bootstrap_mean(xs, alpha=.05):
    x=[float(v) for v in xs if math.isfinite(float(v))]; n=len(x)
    if not n:return {'n':0,'mean':0,'se':0,'ci_low':0,'ci_high':0}
    m=sum(x)/n; var=sum((v-m)**2 for v in x)/max(n-1,1); se=math.sqrt(var/n)
    z=1.96
    return {'n':n,'mean':m,'se':se,'ci_low':m-z*se,'ci_high':m+z*se}

def multiple_testing_adjust(p_values):
    p=sorted((float(x),i) for i,x in enumerate(p_values) if math.isfinite(float(x)))
    out=[1.0]*len(p_values); m=len(p)
    # Benjamini-Hochberg adjusted p-values, monotone from largest rank backward.
    prev=1.0
    for rank,(pv,i) in reversed(list(enumerate(p,1))):
        adj=min(prev,pv*m/rank); out[i]=adj; prev=adj
    return out

def promotion_gate(candidate, champion=None, min_n=100, min_edge=0.0, max_dd=.20):
    n=int(candidate.get('n',0)); edge=float(candidate.get('ci_low',-1e9)); dd=float(candidate.get('max_drawdown',1.0))
    if n<min_n or edge<=min_edge or dd>max_dd:return {'decision':'REJECT','reason':'insufficient_evidence_or_risk'}
    if champion:
        c=float(champion.get('mean',0)); e=float(candidate.get('mean',0))-c
        if e<=min_edge:return {'decision':'REVIEW','reason':'no_clear_edge_over_champion'}
    return {'decision':'PROMOTION_REVIEW','reason':'passes_statistical_gate'}
