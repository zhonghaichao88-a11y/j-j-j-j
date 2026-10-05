# 规则实验室：NFI 头部币条件的"安全检查"（141~145 做多、641/642 做空，触发部分已去掉，所有币都算）
# + 自己配的触发（急跌/急涨幅度可调），做多做空各一套。每个币算一次指标，几百种组合一起出成交，存每笔 4 种出场的收益和持仓时长。
import json, os, sys, glob, time, numpy as np, pandas as pd
from freqtrade.configuration import Configuration
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.enums import RunMode
sys.path.insert(0, '/home/user/ext/nfisig')
from labelmod import label, EXITS
cfgf, datadir, tr, out = sys.argv[1:5]
c = json.load(open(cfgf)); px = os.environ['HTTPS_PROXY']
c['exchange']['ccxt_config'] = {'httpsProxy': px}; c['exchange']['ccxt_async_config'] = {'httpsProxy': px}
pairs = sorted({os.path.basename(f).split('_USDT_USDT')[0] + '/USDT:USDT' for f in glob.glob(datadir + '/futures/*-5m-futures.feather')})
if os.environ.get('PART'):
    k_, n_ = map(int, os.environ['PART'].split('/')); pairs = [p for i, p in enumerate(pairs) if i % n_ == k_ or p.startswith('BTC/')]
c['exchange']['pair_whitelist'] = pairs; c['pairlists'] = [{'method': 'StaticPairList'}]
c['strategy'] = 'NostalgiaForInfinityX7'; c['strategy_path'] = '/home/user/ext/nfisig/strat_var'; c['timerange'] = tr; c['timeframe'] = '5m'
fn = f'/tmp/_lab_{os.getpid()}.json'; json.dump(c, open(fn, 'w'))
config = Configuration({'config': [fn], 'datadir': datadir, 'user_data_dir': '/home/user/ext/ft'}, RunMode.BACKTEST).get_config()
bt = Backtesting(config); bt._set_strategy(bt.strategylist[0]); st = bt.strategy
KEEP = {'141', '142', '143', '144', '145', '641', '642'}
st.long_entry_signal_params = {k: k.rsplit('_', 2)[1] in KEEP for k in st.long_entry_signal_params}
st.short_entry_signal_params = {k: k.rsplit('_', 2)[1] in KEEP for k in st.short_entry_signal_params}
for t in ('641', '642'): st.short_entry_signal_params[f'short_entry_condition_{t}_enable'] = True
data, _ = bt.load_bt_data(); print('pairs', len(data), flush=True)
os.makedirs(out, exist_ok=True)


import talib.abstract as _ta
TFG = {'15m': ([0.03, 0.04, 0.05, 0.06], [0.08, 0.10, 0.15]), '1h': ([0.04, 0.06, 0.08, 0.10], [0.10, 0.15, 0.20]),
       '4h': ([0.06, 0.08, 0.10, 0.15], [0.15, 0.20, 0.30])}


def rules(d):
    """在 15 分钟 / 1 小时 / 4 小时K线上判断急跌急涨（和 NFI 141/142/144 同一个思路，幅度放大），
    信号落在那根大K线收盘的那根 5 分钟K线上"""
    R = []
    base = d[['date', 'open', 'high', 'low', 'close', 'volume']].set_index('date')
    pos = pd.Series(np.arange(len(d)), index=d.date)
    for tf, (dips, falls) in TFG.items():
        rule_ = {'15m': '15min', '1h': '1h', '4h': '4h'}[tf]
        k = base.resample(rule_, label='left', closed='left').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
        last5 = k.index + pd.Timedelta(rule_) - pd.Timedelta('5min')
        ix = pos.reindex(last5).values; good = ~np.isnan(ix); ix = ix[good].astype(int); k = k[good]
        cl, hi, lo = k.close.values.astype(float), k.high.values.astype(float), k.low.values.astype(float)
        r3, r4, r14, r20 = (_ta.RSI(cl, timeperiod=p) for p in (3, 4, 14, 20)); r20p = np.r_[np.nan, r20[:-1]]
        sma = _ta.SMA(cl, timeperiod=16); ad, au = _ta.AROON(hi, lo, timeperiod=14)
        wr = _ta.WILLR(hi, lo, cl, timeperiod=14); cmax = pd.Series(cl).rolling(48).max().values; cmin = pd.Series(cl).rolling(48).min().values
        def put(arr):
            o_ = np.zeros(len(d), bool); o_[ix] = np.nan_to_num(arr, nan=0).astype(bool); return o_
        for x in dips:
            R += [(f'L_{tf}A141_{x}', 1, put((r20 < r20p) & (r3 < 30) & (au < 25) & (cl < sma * (1 - x)))),
                  (f'L_{tf}A142_{x}', 1, put((r3 > 5) & (r4 < 46) & (r20 < r20p) & (cl < sma * (1 - x)))),
                  (f'S_{tf}A641_{x}', -1, put((r20 > r20p) & (r3 > 70) & (ad < 25) & (cl > sma * (1 + x)))),
                  (f'S_{tf}A642_{x}', -1, put((r4 > 54) & (r20 > r20p) & (cl > sma * (1 + x))))]
        for x in falls:
            R += [(f'L_{tf}C144_{x}', 1, put((wr < -50) & (r14 < 40) & (cmax >= cl * (1 + x)))),
                  (f'S_{tf}C144_{x}', -1, put((wr > -50) & (r14 > 60) & (cl >= cmin * (1 + x))))]
    return R


for pair, df in data.items():
    coin = pair.split('/')[0]; t0 = time.time()
    if os.path.exists(f'{out}/{coin}.parquet') or len(df) < 3000: continue
    try:
        d = st.ft_advise_signals(st.advise_indicators(df.copy(), {'pair': pair}), {'pair': pair}).reset_index(drop=True)
    except Exception as e:
        print('ERR', pair, e, flush=True); continue
    tg = d.enter_tag.fillna('').astype(str).str.split()
    F = {t: tg.apply(lambda s, t=t: t in s).values for t in KEEP}
    F['L_any'] = F['141'] | F['142'] | F['143'] | F['144'] | F['145']; F['S_any'] = F['641'] | F['642']
    F['L_glob'] = d.protections_long_global.fillna(False).values.astype(bool); F['S_glob'] = d.protections_short_global.fillna(False).values.astype(bool)
    own = {'A141': '141', 'A142': '142', 'B143': '143', 'C144': '144', 'D145': '145', 'A641': '641', 'A642': '642'}
    o, h, l, cl = (d[k].values.astype(float) for k in ('open', 'high', 'low', 'close'))
    tms = d.date.values.astype('datetime64[ms]').astype(np.int64); ok = np.arange(len(d)) > 2016; ok[-1] = False
    rows = []
    for nm, side, trig in rules(d):
        fam = nm.split('_')[1][-4:]; sd = nm[0]
        fl = {'none': np.ones(len(d), bool), 'glob': F[f'{sd}_glob'], 'any': F[f'{sd}_any']}
        if fam in own and (own[fam] < '500') == (side > 0): fl['own'] = F[own[fam]]
        for fname, m in fl.items():
            idx = np.where(np.nan_to_num(trig, nan=0).astype(bool) & m & ok)[0].astype(np.int64)
            if not len(idx): continue
            rec = {'rule': f'{nm}|{fname}', 't': tms[idx + 1]}
            for k_, (tp, sl, hd) in enumerate(EXITS):
                r_, du_ = label(o, h, l, cl, idx, float(side), tp, sl, hd); rec[f'y{k_}'] = r_.astype(np.float32); rec[f'd{k_}'] = du_
            rows.append(pd.DataFrame(rec))
    T = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=['rule', 't'])
    T['rule'] = T.rule.astype('category'); T.to_parquet(f'{out}/{coin}.parquet', compression='zstd')
    print(coin, len(T), round(time.time() - t0), flush=True)
