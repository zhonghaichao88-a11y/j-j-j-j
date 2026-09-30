"""ADXMomentum + BTC 大盘过滤（只做多，固定每笔100U、最多10仓）在 freqtrade 测不了的数据上：
  A 币安独有币（OKX 没有，416 个，2024-09~2026-08）
  B 更早年份（币安 2021-10~2024-09，2022-01 已上市的币；按年分段：2022、2023、2024前三季）
成本：单边 0.10% / 0.15% / 0.20%。参数小改：止盈 0.8%/1.5%/2%，ADX 门槛 20/30（成本 0.15%）。"""
import json, os, sys
import numpy as np, pandas as pd
import adx_bt as A
D = '/home/user/okx_data'
H = 3600000


def npz(p):
    z = np.load(p); return {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close')}


def daily(f):
    s = pd.DataFrame(f); s['d'] = s.ts // 86400000 * 86400000
    g = s.groupby('d').agg(close=('close', 'last'), n=('close', 'size')); g = g[g.n >= 20]
    return dict(ts=g.index.values.astype(np.int64), close=g.close.values)


def btc_bnf():
    z = np.load(f'{D}/bnf_BTC-USDT-SWAP.npz'); return daily({'ts': z['ts'], 'close': z['close']})


def ms(x): return int(pd.Timestamp(x, tz='UTC').timestamp() * 1000)


out = []


def test(name, frames, btc, periods):
    for label, a, b in periods:
        for fee in (0.001, 0.0015, 0.002):
            T = A.run(frames, btc, fee=fee, t0=ms(a), t1=ms(b), wallet=990)
            out.append(dict(数据=name, 段=label, 手续费=fee, 参数='原版', **A.summary(T, wallet=990))); print(out[-1], flush=True)
        for kw, lbl in ((dict(roi=0.008), '止盈0.8%'), (dict(roi=0.015), '止盈1.5%'), (dict(roi=0.02), '止盈2%'), (dict(adx_th=20), 'ADX>20'), (dict(adx_th=30), 'ADX>30')):
            T = A.run(frames, btc, fee=0.0015, t0=ms(a), t1=ms(b), wallet=990, **kw)
            out.append(dict(数据=name, 段=label, 手续费=0.0015, 参数=lbl, **A.summary(T, wallet=990))); print(out[-1], flush=True)


which = sys.argv[1] if len(sys.argv) > 1 else 'all'
if which in ('all', 'bnx'):
    syms = json.load(open(f'{D}/universe_bnx.json'))
    frames = {s: npz(f'{D}/{s}_bn1h.npz') for s in syms if os.path.exists(f'{D}/{s}_bn1h.npz')}
    test('币安独有币', frames, btc_bnf(), (('第一年', '2024-09-29', '2025-09-29'), ('第二年', '2025-09-29', '2026-09-01')))
if which in ('all', 'old'):
    syms = json.load(open(f'{D}/universe_bnold.json'))
    frames = {s: npz(f'{D}/{s}_bnold1h.npz') for s in syms if os.path.exists(f'{D}/{s}_bnold1h.npz')}
    btc = daily(frames['BTCUSDT'])
    test('更早年份(币安)', frames, btc, (('2022年', '2022-01-01', '2023-01-01'), ('2023年', '2023-01-01', '2024-01-01'), ('2024年前三季', '2024-01-01', '2024-10-01')))
R = pd.DataFrame(out); R.to_csv(f'adx_more_{which}.csv', index=False)
pd.set_option('display.width', 250); print(R.to_string(index=False))
