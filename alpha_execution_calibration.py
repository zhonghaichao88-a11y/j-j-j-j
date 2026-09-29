"""Institutional 4.0 execution calibration from observed fills.
Pure research math; no live routing.
"""
from __future__ import annotations
import math
from statistics import median


def _finite(x, default=0.0):
    try:
        v=float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def calibrate(fills, bins=5):
    rows=[]
    for f in fills or []:
        slip=_finite(f.get('slippage_bps'))
        part=max(_finite(f.get('participation')),0.0)
        vol=max(_finite(f.get('volatility')),0.0)
        rows.append((part,vol,slip))
    if not rows:
        return {'n':0,'median_slippage_bps':0.0,'p90_slippage_bps':0.0,'impact_slope_bps':0.0,'ok':False}
    slips=sorted(x[2] for x in rows)
    p90=slips[min(len(slips)-1,max(0,math.ceil(.9*len(slips))-1))]
    # Robust slope through origin: median(slip / participation), capped against outliers.
    ratios=[s/p for p,_,s in rows if p>1e-9]
    slope=median(ratios) if ratios else 0.0
    return {'n':len(rows),'median_slippage_bps':median(slips),'p90_slippage_bps':p90,
            'impact_slope_bps':max(0.0,float(slope)),'ok':len(rows)>=10}


def predict(participation, volatility, spread_bps, calibration=None):
    p=max(_finite(participation),0.0); vol=max(_finite(volatility),0.0); spread=max(_finite(spread_bps),0.0)
    slope=max(_finite((calibration or {}).get('impact_slope_bps'),0.0),0.0)
    # Volatility term is intentionally conservative and small; calibrated impact dominates when available.
    return max(0.0, spread/2.0 + slope*p + vol*10000.0*0.10)
