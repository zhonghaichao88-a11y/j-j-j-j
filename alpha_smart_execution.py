"""Deterministic execution planner: TWAP/POV-style slices with participation and risk limits."""
from __future__ import annotations
import math

def plan(notional, price, adv_notional, spread_bps, volatility, duration_s=300, slices=10, max_participation=.10, urgency=.5):
    n=max(float(notional),0); px=max(float(price),1e-12); adv=max(float(adv_notional),1e-12); k=max(int(slices),1)
    max_total=adv*max(float(max_participation),0)
    executable=min(n,max_total); slice_n=executable/k
    urgency=min(max(float(urgency),0),1)
    interval=max(float(duration_s),1)/k
    # Conservative limit offset: half-spread + volatility/urgency penalty.
    offset=max(float(spread_bps),0)/2 + max(float(volatility),0)*10000*(1-urgency)*0.25
    return {'requested_notional':n,'planned_notional':executable,'participation':executable/adv,'slice_notional':slice_n,
            'slices':k,'interval_s':interval,'limit_offset_bps':offset,'capped':executable<n,'urgency':urgency}

def child_orders(plan, side, start_ts=0):
    out=[]; sign=1 if str(side).lower()=='buy' else -1
    for i in range(int(plan['slices'])):
        out.append({'seq':i+1,'ts':int(start_ts+i*plan['interval_s']*1000),'side':side,'notional':plan['slice_notional'],'signed_notional':sign*plan['slice_notional']})
    return out
