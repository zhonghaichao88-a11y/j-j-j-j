"""方案三（ADXMomentum 多空 + BTC 大盘过滤）止盈 1%/2%/3%/4%/5% 对比；其他规则不变，成本单边0.15%，固定每笔100U、最多10仓。"""
import json, os, types
import numpy as np, pandas as pd
import adx_bt as A
D = '/home/user/okx_data'
M = types.ModuleType('M'); _src = open('adx_more.py', encoding='utf-8').read(); exec(_src[:_src.index('out = []')], M.__dict__)
def ms(x): return int(pd.Timestamp(x, tz='UTC').timestamp() * 1000)
def st(T, wallet=990):
    r = T.r.values; pnl = r * 100; o = np.argsort(T.exit_t.values); eq = np.cumsum(pnl[o])
    dd = (np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max()
    day = pd.Series(pnl, index=pd.to_datetime(T.exit_t.values, unit='ms')).resample('D').sum() / wallet * 100
    return dict(笔数=len(r), 胜率=f'{(r > 0).mean() * 100:.0f}%', 平均赚=f'{r[r > 0].mean() * 100:+.2f}%', 平均亏=f'{r[r <= 0].mean() * 100:+.2f}%',
                每笔=f'{r.mean() * 100:+.3f}%', 收益=f'{pnl.sum() / wallet * 100:+.0f}%', 最大回撤=f'{dd / wallet * 100:.0f}%', 最差一天=f'{day.min():+.0f}%',
                平均持仓=f'{((T.exit_t - T.t) / 3600000).mean():.0f}小时')
rows = []
def go(name, frames, btc, periods):
    for lab, a, b in periods:
        for roi in (0.01, 0.02, 0.03, 0.04, 0.05):
            T = A.run(frames, btc, fee=0.0015, roi=roi, t0=ms(a), t1=ms(b), wallet=990, short=True)
            rows.append(dict(数据=name, 段=lab, 止盈=f'{roi * 100:.0f}%', **st(T))); print(rows[-1], flush=True)
syms = json.load(open(f'{D}/universe_bnx.json'))
go('币安独有币', {s: M.npz(f'{D}/{s}_bn1h.npz') for s in syms if os.path.exists(f'{D}/{s}_bn1h.npz')}, M.btc_bnf(),
   (('第一年', '2024-09-29', '2025-09-29'), ('第二年', '2025-09-29', '2026-09-01')))
syms = json.load(open(f'{D}/universe_bnold.json'))
fr = {s: M.npz(f'{D}/{s}_bnold1h.npz') for s in syms if os.path.exists(f'{D}/{s}_bnold1h.npz')}
go('更早年份', fr, M.daily(fr['BTCUSDT']), (('2022年', '2022-01-01', '2023-01-01'), ('2023年', '2023-01-01', '2024-01-01'), ('2024前三季', '2024-01-01', '2024-10-01')))
R = pd.DataFrame(rows); R.to_csv('adx_tp.csv', index=False)
pd.set_option('display.width', 300); pd.set_option('display.max_columns', 30); print(R.to_string(index=False)); print('DONE')
