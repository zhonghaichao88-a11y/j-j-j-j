"""用策略机的回测引擎（和 freqtrade 逐单对过账）在欧易 98 个币上按季度跑指定年份，快速看一个策略某几年行不行。
条件和全币复测一样：最多 10 单、平分资金、每边手续费 0.07%、每季度 1000U 重来、按欧易现在的最小下单量检查。
用法：python engine_years.py 策略文件 策略类名 年份,年份...   输出 /home/user/ext/ftrun/eng/<类名>/<年>.json
策略机加载不了（用到不支持的功能）就输出 {"unsupported": [...]}"""
import os, sys, json, glob
import pandas as pd
from numpy import format_float_positional
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..', 'stratbot'))
import sb_loader, sb_engine  # noqa: E402
import inf_data  # noqa: E402

path, cls, years = sys.argv[1], sys.argv[2], [int(y) for y in sys.argv[3].split(',')]
OUT = f'/home/user/ext/ftrun/eng/{cls}'
os.makedirs(OUT, exist_ok=True)
try:
    st = sb_loader.Strategy(cls, path, runmode='backtest')
    err = st.unsupported
except Exception as e:  # noqa: BLE001
    err = [f'加载失败：{e}'[:300]]
if err:
    json.dump({'unsupported': err}, open(f'{OUT}/unsupported.json', 'w'), ensure_ascii=False)
    print(cls, '策略机跑不了：', err); sys.exit(0)
pairs = json.load(open('/home/user/ext/ft_whitelist_spot.json'))
inst = json.load(open('/home/user/ext/okx_spot_inst.json'))
mk = json.load(open('/home/user/ext/okx_ccxt_markets.json'))
uses_dp = st.uses_callbacks


def tick_over_time(c):
    c = c.copy()
    for col in ['open', 'high', 'low', 'close']:
        c[f'{col}_c'] = (c[col].apply(format_float_positional, precision=14, unique=False, fractional=False, trim='-')
                         .str.extract(r'\.(\d*[1-9])')[0].str.len())
    return 1 / 10 ** c.set_index('date')[['open_c', 'high_c', 'low_c', 'close_c']].max(axis=1).resample('MS').max()


raw = {p: pd.read_feather(f"/home/user/ext/ft/data_spot/okx/{p.replace('/', '_')}-{st.timeframe}.feather") for p in pairs
       if os.path.exists(f"/home/user/ext/ft/data_spot/okx/{p.replace('/', '_')}-{st.timeframe}.feather")}
for y in years:
    if os.path.exists(f'{OUT}/{y}.json'):
        continue
    Ts = []
    for q in (1, 2, 3, 4):
        if y == 2026 and q == 4:
            continue
        t0 = pd.Timestamp(year=y, month=(q - 1) * 3 + 1, day=1, tz='UTC')
        t1 = t0 + pd.DateOffset(months=3) if not (y == 2026 and q == 3) else pd.Timestamp('2026-09-30', tz='UTC')
        w0 = t0 - pd.Timedelta(minutes=st.tf_min * st.startup)
        data, full, ticks = {}, {}, {}
        src = inf_data.okx_source(t0, t1, st.startup, 'spot')     # 策略要别的币/别的周期K线时从这里拿（和 freqtrade 同一份数据）
        st.s.dp.whitelist = list(raw)
        for p, d0 in raw.items():
            d = d0[(d0.date >= w0) & (d0.date <= t1)]
            if len(d) <= st.startup + 1:
                continue
            d = sb_loader.fill_missing(d.reset_index(drop=True), st.tf_min)
            ticks[p] = (tick_over_time(d), inst.get(p.replace('/', '-'), {}).get('tick'))
            a, fa = st.analyze(d, p, keep_full=True, inf=src)
            a = a.iloc[st.startup:].reset_index(drop=True)
            if len(a) > 1:
                data[p] = a
                if uses_dp:
                    full[p] = fa.reset_index(drop=True)
        bt = sb_engine.Backtest(st, max_open=10, fee=0.0007, ticks=ticks, wallet=1000, tradable_ratio=0.99,
                                markets={p: mk[p] for p in data if p in mk})
        T = pd.DataFrame(bt.run(data, sorted(data), full=full or None, startup=st.startup, consume=True))
        if len(T):
            Ts.append(T)
    T = pd.concat(Ts) if Ts else pd.DataFrame(columns=['profit_abs', 'profit_ratio', 'pair'])
    gp, gl = T.profit_abs[T.profit_abs > 0].sum(), -T.profit_abs[T.profit_abs < 0].sum()
    r = {'n': len(T), 'win': round(float((T.profit_abs > 0).mean()), 3) if len(T) else 0,
         'pf': round(float(gp / gl), 2) if gl > 0 else (99 if gp > 0 else 0),
         'avg': round(float(T.profit_ratio.mean()) * 100, 3) if len(T) else 0, 'coins': int(T.pair.nunique()) if len(T) else 0}
    json.dump(r, open(f'{OUT}/{y}.json', 'w'))
    print(cls, y, r, flush=True)
