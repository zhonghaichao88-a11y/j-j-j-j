"""合成做多打法：NFI 头部币原版（5 分钟 141~145）+ 15 分钟急跌抄底（头部币）+ 牛市按波动算的急跌抄底（ATR 出场），
任一触发就开，同币不重叠，组合 10% 仓位、最多 10 单。各自用自己过关时的出场。"""
import os, sys, glob, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from itemsets import evaluate
N = '/home/user/ext/nfisig'; SEGS = ['2022-23', '2024-25', '2025-26']; DAYS = {'2022-23': 756, '2024-25': 359, '2025-26': 359}
DIRS = {'L': ['L_old', 'L_mid', 'L_new'], 'L2': ['L2_old', 'L2_mid', 'L2_new'], 'L3': ['L3_old', 'L3_mid', 'L3_new']}
TOP = set(open(f'{N}/top.txt').read().split())
R = pd.read_parquet(f'{N}/btc_regime.parquet'); R['day'] = R.day.values.astype('datetime64[ms]').astype(np.int64)
# (来源, 规则, 出场, 只头部币, 只牛市)
PARTS = {
    'NFI原版': [('L', r, 1, True, False) for r in ['L_A141_0.04|own', 'L_A142_0.04|own', 'L_B143_0.02|own', 'L_C144_0.1|own', 'L_D145_0.02|own']],
    '15分钟急跌': [('L2', r, 1, True, False) for r in ['L_15mA142_0.05|any', 'L_15mA141_0.05|any', 'L_15mA142_0.04|own', 'L_15mA141_0.06|any', 'L_15mC144_0.15|own']],
    '波动急跌头部币': [('L3', r, 4, True, True) for r in ['L_VN4_4.0', 'L_VN4_5.0', 'L_VN1_5.0']],
    '波动急跌全部币': [('L3', r, 4, False, True) for r in ['L_VN4_5.0', 'L_VN1_5.0']],
}
cache = {}
def rows(src, rule, k, top, bull):
    out = []
    for seg, d in zip(SEGS, DIRS[src]):
        for f in glob.glob(f'{N}/{d}/*.parquet'):
            c = os.path.basename(f)[:-8]
            if top and c not in TOP: continue
            key = (d, c)
            if key not in cache: cache[key] = pd.read_parquet(f)
            x = cache[key]; x = x[x.rule.astype(str) == rule]
            if not len(x): continue
            out.append(pd.DataFrame({'seg': seg, 'coin': c, 't': x.t.values, 'y': x[f'y{k}'].values.astype(float), 'd': x[f'd{k}'].values.astype(np.int64), 'part': ''}))
    X = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    if bull and len(X):
        X['day'] = (X.t // 86_400_000 * 86_400_000).astype(np.int64); X = X.merge(R, on='day', how='left'); X = X[X.bull == True]
    return X
P = {nm: pd.concat([rows(*a) for a in lst], ignore_index=True).assign(part=nm) for nm, lst in PARTS.items()}
def show(name, X):
    X = X.sort_values('t').drop_duplicates(['coin', 't'])
    cid = X.coin.astype('category').cat.codes.values.astype(np.int64)
    s = []
    for seg in SEGS:
        m = (X.seg == seg).values
        n, win, gp, gl, p1, l1, p2, l2, eq, dd = evaluate(X.t.values[m], cid[m], X.y.values[m], X.d.values[m], int(cid.max()) + 1)
        s.append(f'{seg}: 每天{n / DAYS[seg]:.2f}单 胜{win / max(n, 1):.0%} PF{gp / gl if gl else 9.99:.2f} 100U→{eq:.0f} 撤{dd * 100:.0f}%')
    print(f'{name}\n   ' + '\n   '.join(s))
for nm, X in P.items(): show(nm, X)
show('合成 = NFI原版 + 15分钟急跌 + 波动急跌头部币', pd.concat([P['NFI原版'], P['15分钟急跌'], P['波动急跌头部币']]))
show('合成 = 全部四组', pd.concat(list(P.values())))
