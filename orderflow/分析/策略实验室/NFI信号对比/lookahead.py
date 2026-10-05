# 偷看未来检查：把所有周期的数据截到 cut 时刻再算信号，对比全量数据算出来的 cut 前 288 根的信号/标签
import json, os, sys, numpy as np, pandas as pd
from freqtrade.configuration import Configuration
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.enums import RunMode
cfgf, datadir, tr, pairs, ncut = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4].split(','), int(sys.argv[5])
c = json.load(open(cfgf)); px = os.environ['HTTPS_PROXY']
c['exchange']['ccxt_config'] = {'httpsProxy': px}; c['exchange']['ccxt_async_config'] = {'httpsProxy': px}
c['exchange']['pair_whitelist'] = sorted(set(pairs + ['BTC/USDT:USDT'])); c['pairlists'] = [{'method': 'StaticPairList'}]
c['strategy'] = 'NostalgiaForInfinityX7'; c['strategy_path'] = '/home/user/ext/nfisig/strat'; c['timerange'] = tr; c['timeframe'] = '5m'
fn = f'/tmp/_la_{os.getpid()}.json'; json.dump(c, open(fn, 'w'))
config = Configuration({'config': [fn], 'datadir': datadir, 'user_data_dir': '/home/user/ext/ft'}, RunMode.BACKTEST).get_config()
bt = Backtesting(config); bt._set_strategy(bt.strategylist[0]); st = bt.strategy
data, _ = bt.load_bt_data()
dp = st.dp; orig = dp.get_pair_dataframe.__func__ if hasattr(dp.get_pair_dataframe, '__func__') else None
CUT = [None]
def gpd(self, pair, timeframe=None, candle_type=''):
    d = orig(self, pair, timeframe, candle_type)
    if CUT[0] is not None and d is not None and len(d):
        tfm = {'5m': 5, '15m': 15, '1h': 60, '4h': 240, '1d': 1440}.get(timeframe or '5m', 5)
        d = d[d.date + pd.Timedelta(minutes=tfm) <= CUT[0] + pd.Timedelta(minutes=5)]   # 只留 cut 时已收盘的K线
    return d
import types; dp.get_pair_dataframe = types.MethodType(gpd, dp)
def sig(df, pair):
    d = st.ft_advise_signals(st.advise_indicators(df.copy(), {'pair': pair}), {'pair': pair})
    return d.set_index('date')[['enter_long', 'enter_short', 'enter_tag']].fillna({'enter_long': 0, 'enter_short': 0, 'enter_tag': ''})
bad = {}; tot = {}
for pair in pairs:
    df = data[pair]; CUT[0] = None; full = sig(df, pair)
    WT = set(os.environ.get('TAGS', '').split(','))
    act = full[full.enter_tag.str.split().apply(lambda x: bool(WT & set(x)))]
    rng = np.random.default_rng(1)
    # 一半切点放在有信号的K线上（专门查信号会不会消失），一半随机
    cuts = list(rng.choice(act.index[act.index > df.date.iloc[3000]], min(ncut // 2, len(act)), replace=False)) if len(act) else []
    cuts += list(rng.choice(df.date.iloc[3000:].values, ncut - len(cuts), replace=False))
    for cut in cuts:
        cut = pd.Timestamp(cut); cut = cut.tz_localize('UTC') if cut.tzinfo is None else cut; CUT[0] = cut
        part = sig(df[df.date <= cut], pair).iloc[-288:]
        f = full.loc[part.index]
        for t in set(' '.join(f.enter_tag).split()) | set(' '.join(part.enter_tag).split()):
            a = f.enter_tag.str.split().apply(lambda x: t in x); b = part.enter_tag.str.split().apply(lambda x: t in x)
            tot[t] = tot.get(t, 0) + int(a.sum() + b.sum()); bad[t] = bad.get(t, 0) + int((a != b).sum())
    print(pair, 'done', flush=True)
print('标签 出现次数 不一致次数')
for t in sorted(tot, key=lambda x: int(x)): print(t, tot[t], bad[t])
