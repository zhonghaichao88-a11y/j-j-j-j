#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校准研究台 lab_v5_small 与生产 fast_backtest 的一致性：
用 v4 精确参数在 6 个主流币上跑 lab 的 MR-only，对照生产 v4_partial（0.06% maker 应≈66%/PF1.11）。"""
import copy, os
import lab_v5_small as V

V.DATA = os.path.join(V.BT, "data")     # 切到主流币数据
V._CACHE.clear()
MAJ = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT"]

p = copy.deepcopy(V.DEFAULT)
p.update(dict(
    w_tr=False, w_mr=True,
    trend_gap=0.30, trend_er=0.30, htf_gap=0.50,
    chop_er=0.25, chop_er_1h=0.32, range_gap4=1.20,
    band_touch=0.0005, rsi_ovs=20.0, rsi_obv=80.0, rsi_ext=12.0,
    mr_sweep=True, mr_climax=True, mr_body=False,
    mr_klo=1.9, mr_khi=2.6, mr_rr=1.3, mr_floor=0.004,
    mr_max_ext1h=99, mr_max_4gap=99, mr_bbw_x=99, mr_target="vwap",
    use_partial=True, stag_bars=18, stag_r=0.3, max_hold=48, cooldown=3,
))
for cost in (0.0012, 0.0008, 0.0006):
    print(f"\n#### lab MR-only 主流币 cost={cost*100:.2f}% (对照生产 v4_partial) ####")
    t = V.run(p, cost, universe=MAJ, verbose=True)
