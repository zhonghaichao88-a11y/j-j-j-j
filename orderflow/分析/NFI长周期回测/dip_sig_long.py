"""订单流里两个 NFI 急跌打法，用豆包长数据（2020~2026，137 个币）逐年算信号和每笔结果。
  nfi_5m ：NFI 原版 141~145（头部币条件）在 5 分钟上触发
  nfi_15m：15 分钟急跌（A142 5% / A141 5% / A142 4% / A141 6% / C144 15%）+ NFI 的安全检查（和程序 TF15_RULES 一样）
出场和程序一样：下一根开盘进，止盈 3%、止损 8%、最多 48 小时（labelmod EXITS[1]）。
用法：python dip_sig_long.py 年份...   输出 /home/user/ext/nfisig/LY/<年>/<币>.parquet"""
import json, os, sys, glob, time, numpy as np, pandas as pd
from freqtrade.configuration import Configuration
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.resolvers import StrategyResolver
from freqtrade.enums import RunMode
import talib.abstract as ta
sys.path.insert(0, '/home/user/ext/nfisig')
from labelmod import label
DD = '/home/user/ext/ft/data_long/okx'
TR = {2020: '20200401-20210101', 2021: '20210101-20220101', 2022: '20220101-20230101', 2023: '20230101-20240101',
      2024: '20240101-20250101', 2025: '20250101-20260101', 2026: '20260101-20260930'}
KEEP = {'141', '142', '143', '144', '145'}
TF15 = [('A142', 0.05, 'any'), ('A141', 0.05, 'any'), ('A142', 0.04, '142'), ('A141', 0.06, 'any'), ('C144', 0.15, '144')]


def tf15(d):
    """15 分钟K线收盘那根 5 分钟上判断（分钟 %15 == 10），和程序 tf15_triggers 一样"""
    k = d[['date', 'open', 'high', 'low', 'close']].set_index('date').resample('15min', label='left', closed='left').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    pos = pd.Series(np.arange(len(d)), index=d.date)
    ix = pos.reindex(k.index + pd.Timedelta('10min')).values; good = ~np.isnan(ix); ix = ix[good].astype(int); k = k[good]
    cl, hi, lo = k.close.values.astype(float), k.high.values.astype(float), k.low.values.astype(float)
    r3, r4, r14, r20 = (ta.RSI(cl, timeperiod=p) for p in (3, 4, 14, 20)); r20p = np.r_[np.nan, r20[:-1]]
    sma = ta.SMA(cl, timeperiod=16); _, au = ta.AROON(hi, lo, timeperiod=14)
    wr = ta.WILLR(hi, lo, cl, timeperiod=14); cmax = pd.Series(cl).rolling(48).max().values
    out = {}
    for fam, x, g in TF15:
        if fam == 'A141': m = (r20 < r20p) & (r3 < 30) & (au < 25) & (cl < sma * (1 - x))
        elif fam == 'A142': m = (r3 > 5) & (r4 < 46) & (r20 < r20p) & (cl < sma * (1 - x))
        else: m = (wr < -50) & (r14 < 40) & (cmax >= cl * (1 + x))
        a = np.zeros(len(d), bool); a[ix] = np.nan_to_num(m, nan=0).astype(bool); out[(fam, x, g)] = a
    return out


def tags(d):
    tg = d.enter_tag.fillna('').astype(str).str.split()
    return {t: tg.apply(lambda s, t=t: t in s).values & (d.enter_long.fillna(0).values > 0) for t in KEEP}


for y in map(int, sys.argv[1:]):
    out = f'/home/user/ext/nfisig/LY/{y}'; os.makedirs(out, exist_ok=True)
    if os.path.exists(out + '/_done'): continue
    c = json.load(open(f'/home/user/ext/nfi/cfg_long_{y}.json')); px = os.environ['HTTPS_PROXY']
    c['exchange']['ccxt_config'] = {'httpsProxy': px}; c['exchange']['ccxt_async_config'] = {'httpsProxy': px}
    pairs = sorted({os.path.basename(f).split('_USDT_USDT')[0] + '/USDT:USDT' for f in glob.glob(DD + '/futures/*-5m-futures.feather')})
    c['exchange']['pair_whitelist'] = pairs; c['pairlists'] = [{'method': 'StaticPairList'}]
    c['strategy'] = 'NostalgiaForInfinityX7'; c['strategy_path'] = '/home/user/ext/nfisig/strat_var'; c['timerange'] = TR[y]; c['timeframe'] = '5m'
    fn = f'/tmp/_ly_{os.getpid()}.json'; json.dump(c, open(fn, 'w'))
    config = Configuration({'config': [fn], 'datadir': DD, 'user_data_dir': '/home/user/ext/ft'}, RunMode.BACKTEST).get_config()
    bt = Backtesting(config); bt._set_strategy(bt.strategylist[0]); sv = bt.strategy
    sv.long_entry_signal_params = {k: k.rsplit('_', 2)[1] in KEEP for k in sv.long_entry_signal_params}
    sv.short_entry_signal_params = {k: False for k in sv.short_entry_signal_params}
    c2 = dict(config); c2['strategy_path'] = '/home/user/ext/ft/strategies'
    so = StrategyResolver.load_strategy(c2); so.dp = sv.dp; so.wallets = sv.wallets
    so.long_entry_signal_params = dict(sv.long_entry_signal_params); so.short_entry_signal_params = dict(sv.short_entry_signal_params)
    data, _ = bt.load_bt_data(); print(y, 'pairs', len(data), flush=True)
    t_start = pd.Timestamp(TR[y][:8], tz='UTC')
    for pair, df in data.items():
        coin = pair.split('/')[0]; t0 = time.time()
        if os.path.exists(f'{out}/{coin}.parquet') or len(df) < 3000: continue
        try:
            ind = sv.advise_indicators(df.copy(), {'pair': pair})
            dv = sv.populate_entry_trend(ind.copy(), {'pair': pair}).reset_index(drop=True)
            do = so.populate_entry_trend(ind.copy(), {'pair': pair}).reset_index(drop=True)
        except Exception as e:
            print('ERR', pair, e, flush=True); continue
        G = tags(dv); O = tags(do)
        o, h, l, cl = (dv[k].values.astype(float) for k in ('open', 'high', 'low', 'close'))
        tms = dv.date.values.astype('datetime64[ms]').astype(np.int64)
        ok = (dv.date >= t_start).values & (np.arange(len(dv)) > 2016); ok[-1] = False
        anyg = G['141'] | G['142'] | G['143'] | G['144'] | G['145']
        S = [('nfi_5m', O['141'] | O['142'] | O['143'] | O['144'] | O['145'])]
        trig15 = tf15(dv); m15 = np.zeros(len(dv), bool)
        for (fam, x, g), a in trig15.items(): m15 |= a & (anyg if g == 'any' else G[g])
        S.append(('nfi_15m', m15))
        rows = []
        for nm, m in S:
            idx = np.where(m & ok)[0].astype(np.int64)
            if not len(idx): continue
            r_, du_ = label(o, h, l, cl, idx, 1.0, 0.03, 0.08, 48 * 12)
            rows.append(pd.DataFrame({'rule': nm, 't': tms[idx + 1], 'y': r_.astype(np.float32), 'd': du_}))
        T = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=['rule', 't', 'y', 'd'])
        T.to_parquet(f'{out}/{coin}.parquet')
        print(y, coin, len(T), round(time.time() - t0), flush=True)
    open(out + '/_done', 'w').write('ok')
