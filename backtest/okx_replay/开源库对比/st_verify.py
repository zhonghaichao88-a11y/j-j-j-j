"""超级趋势（FSupertrendStrategy）多空 + BTC 大盘过滤，规则不改，按方案三同样方法验证：
数据：币安独有 416 币（2024-09~2026-08）、更早年份 137 币（2022、2023、2024前三季）、欧易新 64 币（2024-09~2026-10）。
测试：成本 0.10/0.15/0.30/0.50%（+真实资金费率，币安数据有）、晚一根进场、10仓随机开仓顺序×5、不限仓位。固定每笔100U、最多10仓、钱包990。"""
import json, os, types, random, sys
import numpy as np, pandas as pd
import st_bt as S
D = '/home/user/okx_data'
M = types.ModuleType('M'); _src = open('adx_more.py', encoding='utf-8').read(); exec(_src[:_src.index('out = []')], M.__dict__)
V = types.ModuleType('V'); _v = open('adx_verify.py', encoding='utf-8').read(); exec(_v[:_v.index('rows = []')], V.__dict__)
def ms(x): return int(pd.Timestamp(x, tz='UTC').timestamp() * 1000)
def npz(p): z = np.load(p); return {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close')}
def clean(f, run=3):
    """去掉“价格冻结”的K线（下架后的数据：最高=最低，连续 run 根以上）。"""
    h, l = np.asarray(f['high'], float), np.asarray(f['low'], float); flat = h == l
    bad = np.zeros(len(h), bool); i = 0
    while i < len(h):
        if flat[i]:
            j = i
            while j < len(h) and flat[j]: j += 1
            if j - i >= run: bad[i:j] = True
            i = j
        else: i += 1
    return {k: np.asarray(v)[~bad] for k, v in f.items()}
sets = []
s = json.load(open(f'{D}/universe_bnx.json'))
sets.append(('币安独有416', {x: M.npz(f'{D}/{x}_bn1h.npz') for x in s if os.path.exists(f'{D}/{x}_bn1h.npz')}, M.btc_bnf(),
             (('第一年', '2024-09-29', '2025-09-29'), ('第二年', '2025-09-29', '2026-09-01'))))
s = json.load(open(f'{D}/universe_bnold.json')); fr = {x: M.npz(f'{D}/{x}_bnold1h.npz') for x in s if os.path.exists(f'{D}/{x}_bnold1h.npz')}
sets.append(('更早年份137', fr, M.daily(fr['BTCUSDT']), (('2022年', '2022-01-01', '2023-01-01'), ('2023年', '2023-01-01', '2024-01-01'), ('2024前三季', '2024-01-01', '2024-10-01'))))
s = json.load(open(f'{D}/okx64/universe.json')); fr = {x: npz(f'{D}/okx64/{x}_1h.npz') for x in s if os.path.exists(f'{D}/okx64/{x}_1h.npz')}
b = npz(f'{D}/okx64/BTC_1d.npz')
sets.append(('欧易新64', {k: v for k, v in fr.items() if len(v['ts']) > 300}, dict(ts=b['ts'], close=b['close']), (('第一年', '2024-09-29', '2025-09-29'), ('第二年', '2025-09-29', '2026-10-01'))))
sets = [(n, {k: clean(v) for k, v in fr.items()}, btc, per) for n, fr, btc, per in sets]
sets = [(n, {k: v for k, v in fr.items() if len(v['ts']) > 300}, btc, per) for n, fr, btc, per in sets]
rows = []
def add(name, lab, test, T, col='r'):
    T = T[T.why != '结束']
    rows.append(dict(数据=name, 段=lab, 测试=test, **V.stats(T, col))); print(rows[-1], flush=True)
for name, fr, btc, per in sets:
    for lab, a, c in per:
        base = V.add_funding(S.run(fr, btc, fee=0.0015, t0=ms(a), t1=ms(c), wallet=990))
        add(name, lab, '基准 成本0.15%', base)
        if base.fund.notna().any(): add(name, lab, '基准+真实资金费', base, 'r_f')
        for fee in (0.001, 0.003, 0.005):
            add(name, lab, f'成本{fee*100:.2f}%', S.run(fr, btc, fee=fee, t0=ms(a), t1=ms(c), wallet=990))
        add(name, lab, '晚一根进场', S.run(fr, btc, fee=0.0015, t0=ms(a), t1=ms(c), wallet=990, delay=1))
        for seed in range(5):
            keys = list(fr); random.Random(seed).shuffle(keys)
            add(name, lab, f'随机顺序{seed+1}', S.run({f'{i:04d}_{k}': fr[k] for i, k in enumerate(keys)}, btc, fee=0.0015, t0=ms(a), t1=ms(c), wallet=990))
        T = S.run(fr, btc, fee=0.0015, t0=ms(a), t1=ms(c), wallet=10**9, max_open=10**6)
        T = T[T.why != '结束']; rows.append(dict(数据=name, 段=lab, 测试='不限仓位(全部信号)', 笔数=len(T), 胜率=f'{(T.r > 0).mean()*100:.0f}%', 每笔=f'{T.r.mean()*100:+.3f}%', 合计U=f'{T.r.sum()*100:+.0f}')); print(rows[-1], flush=True)
        pd.DataFrame(rows).to_csv('st_verify.csv', index=False)
R = pd.DataFrame(rows); R.to_csv('st_verify.csv', index=False)
pd.set_option('display.width', 300); pd.set_option('display.max_columns', 30); print(R.to_string(index=False)); print('DONE')
