"""第五份数据：欧易逐笔 2025-04 ~ 2025-09，43 个币（BTC/ETH/SOL + 山寨 A 20 个 + 山寨 B 20 个），和前四份完全一样的测法，参数不改。"""
import sys, os, numpy as np, pandas as pd
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweep_check as SC
COINS = ['BTC', 'ETH', 'SOL'] + SC.A20 + SC.B20
SPLIT = pd.Timestamp('2025-07-01').value // 10**6
if __name__ == '__main__':
    with Pool(4) as p:
        res = p.map(SC.coin, [('第五份', '/home/user/ext/of/fp_2504', c) for c in COINS], chunksize=1)
    for cfg in SC.CFGS:
        parts = [o[cfg] for _, c, o in res if o and cfg in o]
        tr = pd.concat([x[0] for x in parts]); rnd = np.concatenate([x[1] for x in parts]); r = tr.ret.values
        b = tr.t_in.values >= SPLIT; cp = sum(int(x[0].ret.sum() > 0) for x in parts)
        ok = len(r) >= 100 and SC.pf(r) >= 1.1 and SC.pf(r[~b]) >= 1 and SC.pf(r[b]) >= 1 and SC.pf(r) - SC.pf(rnd) >= 0.1 and cp * 2 > len(parts)
        print(f'{cfg} 第五份（2025-04~09，{len(parts)} 个币）: {len(r)}笔 胜率{(r > 0).mean():.0%} PF{SC.pf(r):.2f}（前半{SC.pf(r[~b]):.2f}/后半{SC.pf(r[b]):.2f}）'
              f'随机{SC.pf(rnd):.2f} 赚钱币{cp}/{len(parts)} {"✔" if ok else "✘"}')
        q = SC.port(tr.assign(src='扫止损补仓'))
        print(f'   组合：100U → {q["final"]}U，回撤 {q["dd"]}%，{q["n"]} 单')
