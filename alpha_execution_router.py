"""Deterministic smart-execution policy selector for research and paper execution."""
from __future__ import annotations

def choose(notional, adv_notional, spread_bps, volatility, urgency=0.5, max_participation=0.10):
    n=max(float(notional),0.0); adv=max(float(adv_notional),1e-12); sp=max(float(spread_bps),0.0); vol=max(float(volatility),0.0)
    u=min(max(float(urgency),0.0),1.0); cap=max(float(max_participation),0.0)
    part=n/adv
    if part <= min(cap,0.03) and sp <= 4 and vol <= .01:
        style='PASSIVE_LIMIT'
    elif part <= cap and u < .75:
        style='POV'
    elif part <= cap*2 and u < .9:
        style='TWAP'
    else:
        style='URGENT'
    return {'style':style,'participation':part,'capped':part>cap,'urgency':u,
            'reason':('liquidity_ok' if style=='PASSIVE_LIMIT' else 'balanced' if style in ('POV','TWAP') else 'urgency_or_capacity')}
