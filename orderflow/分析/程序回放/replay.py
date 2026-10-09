"""程序回放：把历史 5 分钟K线拆成一笔笔成交，原样喂给订单流程序的引擎（of_engine.OrderFlowApp / SymbolEngine，模拟盘），
程序自己出信号、挂单、成交、撤单、平仓，全部记下来，再和回测逐笔对照。

喂法（每根 5 分钟K线 t）：
  t+1 秒   开盘价一笔（极小量）——上一根K线在这一笔收盘，程序在这里检查各打法
  t+20 秒  急跌抄底：程序是收盘后 20 秒去算，这里把回测算好的信号按程序的方式送进去（eng.dip_signal）
  t+21 秒  开盘价一笔（极小量）——"下一笔成交进场"的打法在这里成交
  t+60/120 秒  先低后高（阳线）或先高后低（阴线），主动卖量放在低点那笔、主动买量放在高点那笔
  t+240 秒 收盘价一笔（极小量）
外部读数（和程序一样的口径，按当时已知的值）：
  持仓量：每根K线收盘后加一个点到 oi_hist（1 小时变化用）；24 小时变化放到 app.oi24（程序每 10 分钟更新一次，这里每根更新，取值相同）
  散户多空比 7 天 z：放到 app.lsz；资金费率：eng.ext["funding"]；币安现货 / 合约主动买卖：假的 xx.stats() 返回 sf_60 / pf_1440
  大盘：app.btc_bull 每天按 BTC 昨收 vs 200 天均线（和程序 btc_regime_calc 一样）
  多头摊平做空 B / C 要的 4h SAR、1h ATR：和程序 trap_aux 一样，只用最近收完的 100 根 4 小时、60 根 1 小时K线算
时钟：time.time 换成回放时钟（程序里"数据太旧"的判断、每日亏损都按回放时间）。
数据：币安合约 5 分钟K线（豆包包）+ 持仓量/多空比/资金费/现货（oos/f5、oos/f6、long 三个目录拼起来）。"""
import os, sys, math, time, asyncio, json, glob, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, '/home/user/j-j-j-j/orderflow')
import of_engine as E
import of_notify
ROOTS = ['/home/user/ext/oos/f5', '/home/user/ext/oos/f6', '/home/user/ext/long']
BNK = '/home/user/ext/bnk'


def _cat(parts):
    parts = [p for p in parts if p is not None and len(p)]
    return pd.concat(parts).sort_index().loc[lambda x: ~x.index.duplicated(keep='last')] if parts else None


def load(c):
    """一个币全部数据，5 分钟一行（index = 毫秒）"""
    k = pd.read_parquet(f'{BNK}/{c}.parquet').sort_values('ts').drop_duplicates('ts').set_index('ts')
    df = pd.DataFrame({'o': k.open, 'h': k.high, 'l': k.low, 'c': k.close, 'v': k.volume, 'bv': k.taker_buy_volume,
                       'qv': k.quote_volume, 'bq': k.taker_buy_quote_volume}).astype(float)
    met, spot, fund = [], [], []
    for r in ROOTS:
        if os.path.exists(f'{r}/met/{c}.parquet'):
            m = pd.read_parquet(f'{r}/met/{c}.parquet')
            m['ts'] = (pd.to_datetime(m.create_time) - pd.Timestamp('1970-01-01')) // pd.Timedelta(milliseconds=1)
            met.append(m.set_index('ts')[[x for x in ('sum_open_interest_value', 'count_long_short_ratio') if x in m]])
        if os.path.exists(f'{r}/spot/{c}.parquet'):
            s = pd.read_parquet(f'{r}/spot/{c}.parquet').set_index('ts')
            spot.append(s[['quote_volume', 'taker_buy_quote_volume']])
        if os.path.exists(f'{r}/met/{c}_funding.parquet'):
            f = pd.read_parquet(f'{r}/met/{c}_funding.parquet').set_index('calc_time')
            fund.append(f[['last_funding_rate']])
    m = _cat(met); s = _cat(spot); f = _cat(fund)
    if m is not None:
        oi = m.sum_open_interest_value.where(m.sum_open_interest_value > 0)
        df['oi'] = oi.reindex(df.index, method='ffill', tolerance=600_000)
        df['ls'] = m.count_long_short_ratio.reindex(df.index, method='ffill', tolerance=600_000) if 'count_long_short_ratio' in m else np.nan
    else:
        df['oi'] = df['ls'] = np.nan
    if s is not None:
        s = s.reindex(df.index); df['sqv'], df['sbq'] = s.quote_volume, s.taker_buy_quote_volume
    else:
        df['sqv'] = df['sbq'] = np.nan
    df['fund'] = f.last_funding_rate.reindex(df.index, method='ffill') if f is not None else np.nan
    df.attrs['name'] = c
    return df


def features(df):
    """和回测同一口径的读数（第 i 根收盘时已知）"""
    flow = lambda b, q, n: (2 * b - q).rolling(n).sum() / q.rolling(n).sum()
    out = pd.DataFrame(index=df.index)
    out['oi24'] = df.oi / df.oi.shift(288) - 1
    lsh = df.ls.iloc[11::12]
    lz = (lsh - lsh.rolling(168, min_periods=48).mean()) / lsh.rolling(168, min_periods=48).std()
    out['lsz'] = lz.reindex(df.index, method='ffill')
    out['sf60'] = flow(df.sbq, df.sqv, 12)
    out['pf1440'] = flow(df.bq, df.qv, 288)
    return out


def btc_regime(n=200):
    """日期(毫秒) -> 那天程序看到的大盘（昨收 > 最近 n 天收盘均值）"""
    import io, zipfile
    P = []
    for f in sorted(glob.glob('/home/user/ext/btcspot/*.zip')):
        z = zipfile.ZipFile(f); t = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])), header=None)
        P.append(pd.DataFrame({'day': pd.to_datetime(t[0], unit='ms').dt.floor('D'), 'c': t[4].astype(float)}))
    k = pd.read_parquet(f'{BNK}/BTC.parquet', columns=['ts', 'close']); k['day'] = pd.to_datetime(k.ts, unit='ms').dt.floor('D')
    p = k.groupby('day').close.last().rename('c').reset_index()
    s = pd.concat(P); d = pd.concat([s[s.day < p.day.min()], p]).drop_duplicates('day').sort_values('day').set_index('day').c
    bull = (d > d.rolling(n).mean()).shift(1)
    return {int(t.value // 10**6): (None if pd.isna(b) else bool(b)) for t, b in bull.items()}


class XX:
    """假的全网数据：只给程序要用的两个读数"""
    def __init__(self): self.v = {}
    def stats(self, inst, okx_min=None): return dict(self.v)


class Clock:
    t = 0.0
CLOCK = Clock()
FAST = True


def _now():
    return CLOCK.t


def replay(c, cfg_over, t0=None, t1=None, dips=None, feats=None, df=None, regime=None, aux='okx'):
    """跑一个币。dips: [(kind, bar_t, ref, stop, target, note)]（回测算好的急跌信号，按程序方式送进去）。
    返回 (signals, trades, log)"""
    df = load(c) if df is None else df
    feats = features(df) if feats is None else feats
    regime = btc_regime() if regime is None else regime
    time.time = _now                                       # 程序里所有 time.time() 都走回放时钟
    E.STATE_FILE = os.devnull; E.TRADE_LOG = os.devnull; E.LOG_FILE = os.devnull
    of_notify.push = lambda *a, **k: None
    cfg = {"auto": True, "guard_n": 0, "daily_loss_pct": 1e9, "max_positions": 10**6, "btc_ma_days": 200, "symbols": [],
           "paper_equity": 1000.0}
    cfg.update(cfg_over)
    app = E.OrderFlowApp(cfg, None, None, False)
    app.save = lambda: None
    logs = []
    app.say = lambda m: logs.append((CLOCK.t, m))
    app.xx = XX()
    inst = f'{c}-USDT-SWAP'
    from of_core import auto_row_size
    T = df.index.values.astype(np.int64)
    i0 = 0 if t0 is None else int(np.searchsorted(T, t0 - 9 * 86_400_000))     # 提前 9 天开始喂（24 小时涨跌、7 天量、24 小时低点要攒够）
    i1 = len(T) if t1 is None else int(np.searchsorted(T, t1))
    w = df.iloc[i0:i0 + 2000]; tick = float(w.c.median()) * 1e-5
    kw = w[['h', 'l']].copy(); kw.index = pd.to_datetime(kw.index, unit='ms')
    rows = {}
    for tf in E.VIEW_TFS:                                  # 和程序一样：按最近 100 根K线的波动定格子（每根大约 20 格）
        r = kw.resample(tf.replace('m', 'min'), label='left', closed='left').agg({'h': 'max', 'l': 'min'}).dropna().tail(100)
        rows[tf] = auto_row_size(list((r.h - r.l).values), tick)
    eng = E.SymbolEngine(app, inst, '5m', rows, 1.0)
    eng.category = '1'
    if FAST:
        # 加速（结果不变）：① 1 小时持仓量变化用二分查找（程序是从头扫一遍，结果一样）；
        # ② 只画图用的 1m / 15m / 1h 足迹不算（没勾实战打法时没有打法用它们）
        import bisect, types
        def _oi_change(self, ms):
            h = self.ext["oi_hist"]
            if len(h) < 2:
                return None
            t1, v1 = h[-1]
            k = bisect.bisect_right(h, (t1 - ms, float('inf')))
            v0 = h[k - 1][1] if k > 0 else h[0][1]
            return (v1 / v0 - 1) if v0 else None
        eng._oi_change = types.MethodType(_oi_change, eng)
        if not (set(cfg.get('enabled', [])) & set(E.SIGNAL_NAMES)):     # ③ 没勾旧形态打法：旧形态识别器只出提示不下单，跳过
            eng.det.on_bar = lambda b: []
        if not (set(cfg.get('enabled', [])) & set(E.PB_NAMES)):
            for tf in [t for t in eng.builders if t != '5m']:
                del eng.builders[tf]
            eng._wire()
    app.engines = {inst: eng}
    sigs = []
    orig_on_signal = app.on_signal
    def on_signal(e, d):
        sigs.append(d); orig_on_signal(e, d)
    app.on_signal = on_signal
    O, H, L, C, V, BV = (df[x].values for x in ('o', 'h', 'l', 'c', 'v', 'bv'))
    OI = df.oi.values; FU = df.fund.values
    F = {k: feats[k].values for k in feats}
    # 多头摊平做空 B / C 的 SAR / ATR：和程序一样只用最近收完的 K 线
    k1 = df[['h', 'l', 'c']].copy(); k1.index = pd.to_datetime(k1.index, unit='ms')
    h1 = k1.resample('1h', label='left', closed='left').agg({'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    h4 = k1.resample('4h', label='left', closed='left').agg({'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    h1t = (h1.index.values.astype('datetime64[ms]').astype(np.int64)); h4t = (h4.index.values.astype('datetime64[ms]').astype(np.int64))
    import talib

    async def trap_aux(i_):
        now = CLOCK.t * 1000
        a = np.searchsorted(h1t, now - 3_600_000, side='right'); b = np.searchsorted(h4t, now - 14_400_000, side='right')
        x1 = h1.iloc[max(0, a - 60):a]; x4 = h4.iloc[max(0, b - 100):b]
        if len(x4) < 30 or len(x1) < 20:
            return {"err": "K线太少"}
        sar = talib.SAR(x4.h.values, x4.l.values)
        atr = talib.ATR(x1.h.values, x1.l.values, x1.c.values, 14)[-1]
        return {"sar_above": bool(sar[-1] > float(x4.c.iloc[-1])), "atr": float(atr)} if atr > 0 else {"err": "ATR 算不出"}
    app.trap_aux_cb = trap_aux

    dmap = {}
    for x in (dips or []):
        dmap.setdefault(int(x[1]), []).append(x)
    day = None

    async def run():
        nonlocal day
        loop = asyncio.get_event_loop()
        need = (cfg.get('trap') or {}).get('mode', E.TRAP['mode']) in E.TRAP_ATR and 'trap_short' in cfg.get('enabled', [])
        async def settle():
            if need:
                for _ in range(3):
                    await asyncio.sleep(0)
        def trade(ts, px, q, buy):
            CLOCK.t = ts / 1000
            eng.on_trade(float(px), float(q), buy, int(ts))
        for i in range(i0, i1):
            t = int(T[i])
            if i > i0:                                  # 上一根（i-1）收盘时程序已知的读数
                j = i - 1
                if not math.isnan(OI[j]):
                    eng.on_oi(OI[j], OI[j], int(T[j]))
                v = F['oi24'][j]; app.oi24[inst] = (v, t) if not math.isnan(v) else app.oi24.get(inst)
                v = F['lsz'][j]
                if not math.isnan(v): app.lsz[inst] = (v, t)
                eng.ext['funding'] = FU[j]
                app.xx.v = {'sf_60': F['sf60'][j], 'pf_1440': F['pf1440'][j]}
                dd = t - t % 86_400_000
                if dd != day:
                    day = dd; app.btc_bull = regime.get(dd)
            trade(t + 1000, O[i], 1e-12, True)
            await settle()
            for kind, bar_t, ref, stop, target, note in dmap.get(t - 300_000, []):
                CLOCK.t = (t + 20_000) / 1000
                eng.dip_signal(kind, ref, stop, target, bar_t, note)
            trade(t + 21_000, O[i], 1e-12, True)
            sv = max(V[i] - BV[i], 0.0)
            if C[i] >= O[i]:
                trade(t + 60_000, L[i], sv, False); trade(t + 120_000, H[i], BV[i], True)
            else:
                trade(t + 60_000, H[i], BV[i], True); trade(t + 120_000, L[i], sv, False)
            trade(t + 240_000, C[i], 1e-12, True)
            await settle()
    asyncio.run(run())
    trades = pd.DataFrame(app.history)
    return sigs, trades, logs, app
