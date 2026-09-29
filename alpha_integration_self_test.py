"""End-to-end wiring test for Institutional 15.0 integrated pre-trade path."""
import time
from alpha_production_integrated import pretrade_pipeline, market_quality, issue_execution_fence, authorize_execution


def candles(n=160):
    now=int(time.time()*1000)
    out=[]; px=100.0
    start=now-(n-1)*15*60_000
    for i in range(n):
        ts=start+i*15*60_000
        px*=1.0002
        out.append([ts,px*0.999,px*1.001,px*0.998,px,1000])
    return out

rows=candles()
q=market_quality(rows, now_ms=int(time.time()*1000))
assert q["ok"] and q["fingerprint"]

ticker={"bid":119.9,"ask":120.1,"last":120.0}
cfg={"risk_gross_limit":1.0,"risk_dd_limit":.15,"risk_cvar_limit":.05,
     "max_participation":.05,"fee_bps":5,"execution_latency_ms":2,
     "execution_duration_s":60,"execution_slices":4,"execution_urgency":.85,
     "volatility_hint":.01,"leverage":3,"min_execution_confidence":.50,
     "max_execution_stress":.85,"max_symbol_weight":1.0,"portfolio_gross_limit":1.0}
res=pretrade_pipeline(symbol="BTC/USDT:USDT",side="long",candles=rows,ticker=ticker,
                      equity=10000,free=9000,requested_notional=500,stop_pct=.01,
                      confidence=.8,stress=.1,cfg=cfg,live=False)
assert res["ready"], res
assert res["route"]["venue"]=="OKX"
assert res["planned_notional"]>0
assert res["portfolio"]["weights"]

tok=issue_execution_fence("selftest")
assert authorize_execution(tok)
print("ALPHA-X INSTITUTIONAL 15.0 INTEGRATION SELF-TEST: PASS")
