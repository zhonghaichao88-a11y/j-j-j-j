"""用策略机的回测引擎（和 freqtrade 逐单对过账）在币安合约全部约 250 个币上跑一个策略，2021~2026 每季度分开跑。
数据：/home/user/ext/bnk/*.parquet（币安 U 本位合约 5 分钟K线，含后来下架的几个币）。
条件：最多同时 10 单、每单 = 可用资金 ÷ 剩余空位，1000U 起每季度重来，每边手续费 0.07%，不检查最小下单量。
还会算：移动止盈每单少 0.3%（更接近实盘的悲观估计）时的结果。
用法：python allcoins_stratbot.py 策略类名 [输出目录]"""
import os, sys, json, glob, time
import numpy as np, pandas as pd, pyarrow.parquet as pq
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..', 'stratbot'))
import sb_loader, sb_engine  # noqa: E402
from numpy import format_float_positional

cls = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else f'/home/user/ext/ftrun/allcoins_{cls}'
os.makedirs(OUT, exist_ok=True)
FILES = sorted(glob.glob('/home/user/ext/bnk/*.parquet'))
st = sb_loader.Strategy(cls)
step = st.tf_min * 60_000


def tick_over_time(c):
    c = c.copy()
    for col in ['open', 'high', 'low', 'close']:
        c[f'{col}_c'] = (c[col].apply(format_float_positional, precision=14, unique=False, fractional=False, trim='-')
                         .str.extract(r'\.(\d*[1-9])')[0].str.len())
    return 1 / 10 ** c.set_index('date')[['open_c', 'high_c', 'low_c', 'close_c']].max(axis=1).resample('MS').max()


quarters = [(y, q) for y in range(2021, 2027) for q in (1, 2, 3, 4) if not (y == 2026 and q == 4)]
for y, q in quarters:
    of = f'{OUT}/trades{y}q{q}.csv'
    if os.path.exists(of):
        continue
    mq = {1: ('01', '04'), 2: ('04', '07'), 3: ('07', '10'), 4: ('10', '01')}[q]
    t0 = pd.Timestamp(f'{y}-{mq[0]}-01', tz='UTC'); t1 = pd.Timestamp(f'{y + (q == 4)}-{mq[1]}-01', tz='UTC')
    if y == 2026 and q == 3:
        t1 = pd.Timestamp('2026-09-30', tz='UTC')
    w0 = t0 - pd.Timedelta(minutes=st.tf_min * st.startup)
    a0, a1 = int(w0.timestamp() * 1000), int(t1.timestamp() * 1000)
    data, ticks, tt = {}, {}, time.time()
    for f in FILES:
        coin = os.path.basename(f)[:-8]
        d = pq.read_table(f, columns=['ts', 'open', 'high', 'low', 'close', 'volume'],
                          filters=[('ts', '>=', a0), ('ts', '<=', a1)]).to_pandas()
        if len(d) <= st.startup + 1:
            continue
        d['date'] = pd.to_datetime(d.ts, unit='ms', utc=True)
        d = sb_loader.fill_missing(d[['date', 'open', 'high', 'low', 'close', 'volume']].reset_index(drop=True), st.tf_min)
        p = coin + '/USDT'
        ticks[p] = (tick_over_time(d), None)
        a = st.analyze(d, p).iloc[st.startup:].reset_index(drop=True)
        if len(a) > 1:
            data[p] = a
    pairs = sorted(data)
    bt = sb_engine.Backtest(st, max_open=10, fee=0.0007, ticks=ticks, wallet=1000, tradable_ratio=0.99)
    T = pd.DataFrame(bt.run(data, pairs))
    T.to_csv(of, index=False)
    print(y, q, '币', len(pairs), '单', len(T), '用时', round(time.time() - tt), flush=True)

rows = []
for y in range(2021, 2027):
    fs = sorted(glob.glob(f'{OUT}/trades{y}q*.csv'))
    T = pd.concat([pd.read_csv(f) for f in fs if os.path.getsize(f) > 10])
    T = T.sort_values('close_ts')
    days = 272 if y == 2026 else 365
    for hair in (0.0, 0.003):
        r = T.profit_ratio - (T.exit_reason == 'trailing_stop_loss') * hair
        pa = r * T.stake
        eq = 1000 + pa.cumsum(); peak = eq.cummax().clip(lower=1000)
        gp, gl = pa[pa > 0].sum(), -pa[pa < 0].sum()
        rows.append({'年': y, '移动止盈少': f'{hair * 100:.1f}%', '单数': len(T), '每天': round(len(T) / days, 1),
                     '胜率': round((r > 0).mean(), 3), '盈亏比': round(gp / gl, 2), '每单平均%': round(r.mean() * 100, 3),
                     '回撤%': round(((peak - eq) / peak).max() * 100, 1), '做过的币': T.pair.nunique()})
R = pd.DataFrame(rows)
print(R.to_string(index=False))
R.to_csv(f'{OUT}/summary.csv', index=False)
