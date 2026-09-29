import tempfile, json
from pathlib import Path
import numpy as np, pandas as pd
import alpha_real_alpha_research as rr
from alpha_real_alpha_research import run_research, labels_first_touch, technical_features, _bh, VERSION

# deterministic synthetic OHLCV + external columns, only for structural self-test; never packaged as research evidence.
rng=np.random.default_rng(7); n=1500; ts=np.arange(n,dtype=np.int64)*900000
ret=rng.normal(0,0.0015,n); close=100*np.exp(np.cumsum(ret)); op=np.r_[close[0],close[:-1]]
high=np.maximum(op,close)*(1+rng.uniform(0,.001,n)); low=np.minimum(op,close)*(1-rng.uniform(0,.001,n)); vol=rng.lognormal(5,0.3,n)
d=pd.DataFrame({"ts":ts,"open":op,"high":high,"low":low,"close":close,"volume":vol,
 "funding_rate":rng.normal(0,1e-4,n),"open_interest":1000+rng.normal(0,20,n).cumsum(),"oi_change_pct":rng.normal(0,.01,n),
 "ofi":rng.normal(0,10,n),"aggressive_buy_ratio":rng.uniform(.3,.7,n),"trade_imbalance":rng.normal(0,.2,n),"trade_count":rng.integers(10,100,n),
 "bid_depth_usd":rng.uniform(1e5,5e5,n),"ask_depth_usd":rng.uniform(1e5,5e5,n),"spread_bps":rng.uniform(.5,4,n),"depth_imbalance":rng.normal(0,.1,n),
 "btc_ret_1":rng.normal(0,.001,n),"eth_ret_1":rng.normal(0,.001,n),"btc_eth_spread":rng.normal(0,.002,n),"venue_basis_bps":rng.normal(0,2,n)})
with tempfile.TemporaryDirectory() as td:
    p=Path(td)/"d.csv"; d.to_csv(p,index=False)
    # Build a valid manifest so the hardened research gate is exercised.
    import hashlib, json
    sha=hashlib.sha256(p.read_bytes()).hexdigest()
    m={"dataset_sha256":sha,"completed_bars_only":True,"no_bfill":True,"point_in_time_join":True,"core_gate":{"ready":True}}
    raw=json.dumps(m,ensure_ascii=False,sort_keys=True,separators=(",", ":")).encode()
    m["manifest_sha256"]=hashlib.sha256(raw).hexdigest()
    mp=p.with_suffix(".manifest.json"); mp.write_text(json.dumps(m),encoding="utf-8")
    old_models=rr._models; old_boot=rr._block_bootstrap_ci; old_perm=rr._permutation_pvalue
    rr._models=lambda seed=42: [rr.LogisticRegression(max_iter=400,random_state=seed,class_weight="balanced"), rr.RandomForestClassifier(n_estimators=30,max_depth=5,min_samples_leaf=5,random_state=seed,n_jobs=1)]
    rr._block_bootstrap_ci=lambda x,**kw: old_boot(x,reps=80,block=8)
    rr._permutation_pvalue=lambda x,**kw: old_perm(x,reps=80,block=8)
    r=run_research(p,manifest=mp,tp=.01,sl=.01,horizon=8,entry=.5,dev_ratio=.75,purge=24,min_rows=1200)
    rr._models, rr._block_bootstrap_ci, rr._permutation_pvalue = old_models, old_boot, old_perm
    assert r["version"]==VERSION
    assert r["split"]["selection_uses_test"] is False
    assert len(r["ablations"])==6 and len(r["test_results"])==6
    assert "q_value_bh" in r["test_results"]["A_OHLCV"]["incremental_vs_baseline"]
assert labels_first_touch(d,.01,.01,8).shape[0]==len(d)
assert _bh({"a":.01,"b":.02})["a"] <= _bh({"a":.01,"b":.02})["b"]
print("ALPHA-X 17.0 self-test PASS")
