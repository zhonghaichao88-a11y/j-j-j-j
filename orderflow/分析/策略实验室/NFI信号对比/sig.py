# 算 NFI X7 每根5m K线的原始进场信号（不受仓位数限制），存 parquet
import json, os, sys, glob, pandas as pd, time
from freqtrade.configuration import Configuration
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.enums import RunMode
cfgf, datadir, tr, out = sys.argv[1:5]
c = json.load(open(cfgf)); px = os.environ['HTTPS_PROXY']
c['exchange']['ccxt_config'] = {'httpsProxy': px}; c['exchange']['ccxt_async_config'] = {'httpsProxy': px}
pairs = [p.split('_USDT_USDT')[0] + '/USDT:USDT' for p in
         (os.path.basename(f) for f in glob.glob(datadir + '/futures/*-5m-futures.feather'))]
pairs = sorted(set(pairs))
if os.environ.get('PART'):
    k_, n_ = map(int, os.environ['PART'].split('/')); pairs = [p for i, p in enumerate(pairs) if i % n_ == k_ or p.startswith('BTC/')]
if os.environ.get('NONTOP'):
    TOP = set(open('/home/user/ext/nfisig/top.txt').read().split()); pairs = [p for p in pairs if p.split('/')[0] not in TOP or p.startswith('BTC/')]
c['exchange']['pair_whitelist'] = pairs; c['pairlists'] = [{'method': 'StaticPairList'}]
c['datadir'] = datadir; c['strategy'] = 'NostalgiaForInfinityX7'; c['strategy_path'] = os.environ.get('SPATH', '/home/user/ext/ft/strategies')
c['timerange'] = tr; c['user_data_dir'] = '/home/user/ext/ft'; c['timeframe'] = '5m'; c['runmode'] = 'backtest'
json.dump(c, open(f'/tmp/_sigcfg_{os.getpid()}.json', 'w'))
config = Configuration({'config': [f'/tmp/_sigcfg_{os.getpid()}.json'], 'datadir': datadir, 'user_data_dir': '/home/user/ext/ft'}, RunMode.BACKTEST).get_config()
pass
bt = Backtesting(config); bt._set_strategy(bt.strategylist[0])
data, timerange = bt.load_bt_data()
print('pairs', len(data), flush=True)
os.makedirs(out, exist_ok=True)
for pair, df in data.items():
    t = time.time(); fn = out + '/' + pair.split('/')[0] + '.parquet'
    if os.path.exists(fn): continue
    try:
        d = bt.strategy.ft_advise_signals(bt.strategy.advise_indicators(df.copy(), {'pair': pair}), {'pair': pair})
    except Exception as e:
        print('ERR', pair, e, flush=True); continue
    cols = [x for x in ['date', 'open', 'high', 'low', 'close', 'enter_long', 'enter_short', 'enter_tag'] if x in d.columns]
    d = d[cols]; d.to_parquet(fn)
    print(pair, len(d), int(d.get('enter_long', pd.Series([0])).fillna(0).sum()), int(d.get('enter_short', pd.Series([0])).fillna(0).sum()), round(time.time() - t), flush=True)
