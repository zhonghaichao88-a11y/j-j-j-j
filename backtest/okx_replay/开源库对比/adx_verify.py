"""ADXMomentum 多空 + BTC 大盘过滤（规则不改）核实：
1 加真实资金费率（币安历史，按持仓期间经过的每次结算扣/加；做多付正费率，做空收正费率）
2 成本 单边 0.15%（基准）/ 0.30% / 0.50%
3 晚一根K线进场（成本 0.15%）
4 平均持仓时长、最多连亏几笔、最差一个月、最差一天
数据：币安独有币（2024-09~2026-08）、更早年份（2022、2023、2024前三季）。固定每笔100U、最多10仓。"""
import json, os
import numpy as np, pandas as pd
import adx_bt as A, types
D = '/home/user/okx_data'
M = types.ModuleType('M')                     # 只取 adx_more 里的数据读取函数，不跑它的主流程
_src = open('adx_more.py', encoding='utf-8').read(); exec(_src[:_src.index('out = []')], M.__dict__)
FUND = {s: np.array(v) for s, v in json.load(open(f'{D}/bn_funding.json')).items() if v}


def ms(x): return int(pd.Timestamp(x, tz='UTC').timestamp() * 1000)


def add_funding(T):
    fs = []
    for p, t, e, d in zip(T.pair, T.t, T.exit_t, T.d):
        f = FUND.get(p)
        if f is None: fs.append(np.nan); continue
        m = (f[:, 0] > t) & (f[:, 0] <= e); fs.append(-d * f[m, 1].sum())
    T = T.copy(); T['fund'] = fs; T['r_f'] = T.r + T.fund.fillna(0); return T


def stats(T, col='r', stake=100, wallet=990):
    r = T[col].values; pnl = r * stake; o = np.argsort(T.exit_t.values); eq = np.cumsum(pnl[o])
    dd = (np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max()
    lose = (r[o] <= 0).astype(int); run = mx = 0
    for x in lose: run = run + 1 if x else 0; mx = max(mx, run)
    mon = pd.Series(pnl, index=pd.to_datetime(T.exit_t.values, unit='ms')).resample('ME').sum() / wallet * 100
    day = pd.Series(pnl, index=pd.to_datetime(T.exit_t.values, unit='ms')).resample('D').sum() / wallet * 100
    return dict(笔数=len(r), 胜率=f'{(r > 0).mean() * 100:.0f}%', 每笔=f'{r.mean() * 100:+.3f}%', 收益=f'{pnl.sum() / wallet * 100:+.0f}%',
                最大回撤=f'{dd / wallet * 100:.0f}%', 最多连亏=mx, 最差一月=f'{mon.min():+.0f}%', 最差一天=f'{day.min():+.0f}%',
                平均持仓=f'{((T.exit_t - T.t) / 3600000).mean():.1f}小时')


rows = []


def go(name, frames, btc, periods):
    for lab, a, b in periods:
        base = add_funding(A.run(frames, btc, fee=0.0015, t0=ms(a), t1=ms(b), wallet=990, short=True))
        rows.append(dict(数据=name, 段=lab, 测试='基准(成本0.15%,不算资金费)', **stats(base)))
        rows.append(dict(数据=name, 段=lab, 测试='基准+真实资金费率', **stats(base, 'r_f'),
                         资金费每笔=f'{base.fund.mean() * 100:+.3f}%', 缺资金费数据=f'{base.fund.isna().mean() * 100:.0f}%'))
        for fee in (0.001, 0.003, 0.005):
            T = add_funding(A.run(frames, btc, fee=fee, t0=ms(a), t1=ms(b), wallet=990, short=True))
            rows.append(dict(数据=name, 段=lab, 测试=f'成本{fee * 100:.2f}%+资金费', **stats(T, 'r_f')))
        T = add_funding(A.run(frames, btc, fee=0.0015, t0=ms(a), t1=ms(b), wallet=990, short=True, delay=1))
        rows.append(dict(数据=name, 段=lab, 测试='晚一根进场+资金费', **stats(T, 'r_f')))
        for r in rows[-6:]: print(r, flush=True)


syms = json.load(open(f'{D}/universe_bnx.json'))
go('币安独有币', {s: M.npz(f'{D}/{s}_bn1h.npz') for s in syms if os.path.exists(f'{D}/{s}_bn1h.npz')}, M.btc_bnf(),
   (('第一年', '2024-09-29', '2025-09-29'), ('第二年', '2025-09-29', '2026-09-01')))
syms = json.load(open(f'{D}/universe_bnold.json'))
fr = {s: M.npz(f'{D}/{s}_bnold1h.npz') for s in syms if os.path.exists(f'{D}/{s}_bnold1h.npz')}
go('更早年份', fr, M.daily(fr['BTCUSDT']), (('2022年', '2022-01-01', '2023-01-01'), ('2023年', '2023-01-01', '2024-01-01'), ('2024前三季', '2024-01-01', '2024-10-01')))
R = pd.DataFrame(rows); R.to_csv('adx_verify.csv', index=False)
pd.set_option('display.width', 300); pd.set_option('display.max_columns', 30); print(R.to_string(index=False))
