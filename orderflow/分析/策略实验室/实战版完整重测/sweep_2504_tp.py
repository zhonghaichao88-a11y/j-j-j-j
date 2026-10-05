"""第五份数据（欧易 2025-04~09）上，把均价止盈放大到 3 / 5 / 8% 再测（其他不变）。"""
import sys, os, numpy as np, pandas as pd
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweep_check as SC
from sweep_2504 import COINS
if __name__ == '__main__':
    for tp in (0.03, 0.05, 0.08):
        SC.TP = tp
        with Pool(4) as p:
            res = p.map(SC.coin, [('第五份', '/home/user/ext/of/fp_2504', c) for c in COINS], chunksize=1)
        for cfg in SC.CFGS:
            parts = [o[cfg] for _, c, o in res if o and cfg in o]
            tr = pd.concat([x[0] for x in parts]); rnd = np.concatenate([x[1] for x in parts]); r = tr.ret.values
            q = SC.port(tr.assign(src='扫止损补仓'))
            print(f'止盈 {tp:.0%} {cfg}: {len(r)}笔 胜率{(r > 0).mean():.0%} PF{SC.pf(r):.2f} 随机{SC.pf(rnd):.2f} 组合 100U→{q["final"]}U 回撤{q["dd"]}%', flush=True)
