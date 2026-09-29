"""Simple conservative market-impact/capacity model; never optimistic by construction."""
from __future__ import annotations
import math

def estimate(notional:float, adv_notional:float, spread_bps:float, volatility:float, impact_coeff:float=12.0)->dict:
    n=max(float(notional),0); adv=max(float(adv_notional),1e-9); share=n/adv
    spread=max(float(spread_bps),0); vol=max(float(volatility),0)
    impact=impact_coeff*math.sqrt(max(share,0))*max(vol/0.01,0.5)
    total=spread/2+impact
    return {'notional':n,'adv_notional':adv,'participation':share,'spread_half_bps':spread/2,'impact_bps':impact,'all_in_cost_bps':total}

def capacity(notional_grid, adv_notional, spread_bps, volatility, expected_edge_bps):
    out=[]
    for n in notional_grid:
        x=estimate(n,adv_notional,spread_bps,volatility); out.append({**x,'net_edge_bps':float(expected_edge_bps)-x['all_in_cost_bps'],'viable':float(expected_edge_bps)>x['all_in_cost_bps']})
    return out
