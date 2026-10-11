"""网页上每个打法旁边显示的成绩，换成程序回放的成绩（of_backtest_result.json，三个周期块都改）。"""
import os, sys, json, glob, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.argv = ['x']
import compare as C
from itemsets import evaluate
F = '/home/user/j-j-j-j/orderflow/of_backtest_result.json'
MAP = {   # 清洗接盘的数来自 age_test.py（成交额前 150 + 上线满 1 年），不在这里算
       'squeeze_long': ('squeeze', '程序回放 币安148币 2021-12~2026-09'),
       'momo_long': ('momo', '程序回放 币安148币 2021-12~2026-09，2022~2023 亏，回撤 62%'),
       'trap_short': ('trapC', '程序回放 币安148币 2021-12~2026-09（C 做法；A 1.03 / B 1.00 / 原版 1.10，都只是保本）'),
       'nfi_5m': ('nfi5', '程序回放 54 个头部币 2020~2026，7 年每年都赚'),
       'nfi_15m': ('nfi15', '程序回放 54 个头部币 2020~2026，2024 年亏，回撤 53%'),
       'vn_dip': ('vn', '程序回放 54 个头部币 2020~2026，2024 年以后基本不赚'),


def stat(X, y):
    X = X.sort_values('t_in'); cid = X.coin.astype('category').cat.codes.values.astype(np.int64)
    d = ((X.t_out - X.t_in) // 300_000 + 1).values.astype(np.int64)
    n, win, gp, gl, p1, l1, p2, l2, eq, dd = evaluate(X.t_in.values.astype(np.int64), cid, X[y].values.astype(float), d, int(cid.max()) + 1)
    return {'n': int(n), 'win': round(win / n, 3), 'pf': round(gp / gl, 2), 'pf_a': round(p1 / l1, 2), 'pf_b': round(p2 / l2, 2), 'avg': round((gp - gl) / n, 4)}


js = json.load(open(F, encoding='utf-8'))
for kind, (k, note) in MAP.items():
    if k == 'sweep':
        P = []
        for f in glob.glob('/home/user/ext/replay/sweep/*.parquet'):
            if f.endswith('.sig.parquet'): continue
            x = pd.read_parquet(f)
            if not len(x): continue
            x['coin'] = os.path.basename(f)[:-8]
            closed = x[['t_close', 'pnl']].sort_values('t_close'); cs = closed.pnl.cumsum().values; ct = closed.t_close.values
            x['eq_open'] = [1000 + (cs[np.searchsorted(ct, t, 'right') - 1] if np.searchsorted(ct, t, 'right') > 0 else 0) for t in x.t_open]
            x['ret'] = x.pnl / (0.10 * x.eq_open); P.append(x)
        P = pd.concat(P); P['t_in'] = P.t_open // 300_000 * 300_000; P['t_out'] = P.t_close // 300_000 * 300_000
    else:
        P, _ = C.program(k)
        if k not in ('nfi5', 'nfi15', 'vn'): P = P[P.t_in >= C.ms('2021-12-01')]
    r = stat(P, 'ret'); r['note'] = note
    for tf in js: js[tf][kind] = r
    print(kind, r)
# 过关 / 不过关按回放后的结论定（利润全靠一次行情、最近几年不赚的，PF 再高也不过关）
OK = {'flush_spot': True, 'nfi_5m': True, 'nfi_15m': True, 'squeeze_long': False, 'vn_dip': False, 'trap_short': False, 'momo_long': False}
NOTE = {'nfi_15m': '程序回放 54 个头部币 2020~2026，2024 年亏，回撤 53%，仓位要小',
        'squeeze_long': '程序回放 币安148币 2021-12~2026-09 PF 1.48，但最近一段的利润全靠 2025-10 一次暴跌反弹（61 笔），去掉后 PF 0.49；不建议',
        'vn_dip': '程序回放 54 个头部币 2020~2026 PF 1.65，但 2024 年亏、2026 年保本；先不开'}
for tf in js:
    for k, ok in OK.items():
        js[tf][k]['ok'] = ok
        if k in NOTE: js[tf][k]['note'] = NOTE[k]
json.dump(js, open(F, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
