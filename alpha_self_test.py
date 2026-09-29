import os,sys,types,tempfile
os.environ.setdefault("ALPHA_LIVE_ALLOWED","0")
# Keep the self-test network-free even when ccxt is not installed in the build environment.
if "ccxt" not in sys.modules:
    m=types.ModuleType("ccxt"); m.okx=type("okx",(),{}) ; sys.modules["ccxt"]=m
import numpy as np, pandas as pd
from alpha_ultra_stack import portfolio_weights, drift_score, execution_plan, regime_from_features
from alpha_attribution import trade_metrics, summary
from alpha_governance import compare
from alpha_recovery import reconcile
from alpha_ops import client_id

def main():
    n=900; rng=np.random.default_rng(7); close=100*np.exp(np.cumsum(rng.normal(.0002,.004,n)))
    op=np.r_[close[0],close[:-1]]; hi=np.maximum(op,close)*(1+rng.uniform(0,.003,n)); lo=np.minimum(op,close)*(1-rng.uniform(0,.003,n))
    d=pd.DataFrame({"ts":np.arange(n)*900000,"open":op,"high":hi,"low":lo,"close":close,"volume":rng.lognormal(8,.4,n)})
    assert np.isfinite(d.close).all()
    assert sum(portfolio_weights({"a":.2,"b":.1},{"a":.3,"b":.2}).values())>0
    assert drift_score(np.arange(100),np.arange(100))<.1
    assert execution_plan(1000,.001,.02,1)["style"]=="MARKET"
    assert regime_from_features(.01,.5,.02)
    t=trade_metrics("long",100,[101,99,103],102,1,entry_fee=.1,exit_fee=.1)
    assert t["mfe_pct"]>0 and t["mae_pct"]<0
    assert summary([t])["trades"]==1
    c={"shadow":{"trades":50,"wins":20,"pnl":10}}; h={"shadow":{"trades":50,"wins":40,"pnl":20}}
    assert compare(c,h)["challenger_trades"]==50
    r=reconcile([{"clOrdId":"AX123","state":"live"},{"clOrdId":"MANUAL","state":"live"}],[],{"AX123"})
    assert len(r["owned"])==1 and len(r["unknown"])==1
    assert client_id("BTC/USDT:USDT","long",intent_key="abc")==client_id("BTC/USDT:USDT","long",intent_key="abc")
    print("ALPHA-X ULTRA MAX 10.0 SELF-TEST PASS")
if __name__=="__main__": main()

# 8.0 autonomous control-plane checks (network-free)
from alpha_autonomy import regime_bucket, regime_attribution, execution_summary, autonomous_decision
assert regime_bucket({"regime":"TREND","stress":0.1}) == "TREND"
assert regime_bucket({"regime":"RANGE","stress":0.8}) == "STRESS"
ra = regime_attribution([{"market_context":{"regime":"TREND"},"net_pnl":2.0},{"market_context":{"regime":"RANGE"},"net_pnl":-1.0}])
assert ra["total_trades"] == 2 and ra["buckets"]["TREND"]["net_pnl"] == 2.0
ex = execution_summary([{"implementation_shortfall_bps":3,"fill_latency_ms_mean":100}])
assert ex["samples"] == 1 and ex["mean_shortfall_bps"] == 3
assert autonomous_decision(health={"healthy":False,"reasons":["ws_stale"]})["action"] == "DEGRADED"
