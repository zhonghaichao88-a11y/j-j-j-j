from pathlib import Path
import tempfile
import pandas as pd
from alpha_real_data import RealDataStore
from alpha_data_pipeline import build_dataset, _gap_stats, production_config

with tempfile.TemporaryDirectory() as td:
    db=Path(td)/"d.sqlite3"; out=Path(td)/"ds"
    s=RealDataStore(db); rows=[]
    for i in range(2500):
        ts=1700000000000+i*900000
        px=100+i*0.01
        rows.append({"instId":"BTC-USDT-SWAP","ts":ts,"open":px,"high":px+1,"low":px-1,"close":px+0.2,"volume":10,"confirm":1})
    s.insert_bars(rows)
    # Core source rows: enough coverage for gate.
    funding=[{"instId":"BTC-USDT-SWAP","fundingTime":1700000000000+i*900000,"realizedRate":0.0001,"fundingRate":0.0001} for i in range(2500)]
    s.insert_funding(funding)
    oi=[{"instId":"BTC-USDT-SWAP","ts":1700000000000+i*900000,"oi":100+i,"oiCcy":1,"oiUsd":100} for i in range(2500)]
    s.insert_oi(oi)
    # Cross-market source: completed BTC/ETH bars over the same time range.
    eth_rows=[]
    for i in range(2500):
        ts=1700000000000+i*900000; px=80+i*0.02
        eth_rows.append({"instId":"ETH-USDT-SWAP","ts":ts,"open":px,"high":px+1,"low":px-1,"close":px+0.1,"volume":10,"confirm":1})
    s.insert_bars(eth_rows); s.close()
    r=build_dataset("BTC-USDT-SWAP",db,"15m",output_dir=out,min_core_coverage=.8,cross_market=True)
    assert r["ready"] and Path(r["dataset"]).exists() and Path(r["manifest"]).exists()
    assert r["audit"]["no_bfill"] and r["audit"]["completed_bars_only"]
    assert r["audit"]["cross_market"]["enabled"] and r["audit"]["coverage_ratios"]["cross_market"] >= .8
    import json, hashlib
    m=json.loads(Path(r["manifest"]).read_text(encoding="utf-8")); canon=dict(m); got=canon.pop("manifest_sha256")
    raw=json.dumps(canon,ensure_ascii=False,sort_keys=True,separators=(",", ":")).encode()
    assert got==hashlib.sha256(raw).hexdigest()
    assert _gap_stats([1, 2, 4],1)["missing_bars_est"]==1
    assert production_config()["autostart"] is False
print("ALPHA-X DATA PIPELINE 16.0 SELF-TEST: PASS")
