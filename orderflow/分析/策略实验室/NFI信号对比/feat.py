# 每个币：算 NFI 全部指标 + 全部进场条件（头部币条件放开到所有币），
# 在每个整点（5 分钟K线 xx:55 收盘）取一个样本：特征（价格类指标换成相对收盘价的百分比）+ 进场标签 +
# 下一根开盘市价进场后做多/做空在几种止盈止损下的收益（扣手续费滑点）。另存完整 5 分钟 OHLC + 标签给组合回测用。
import json, os, sys, glob, time, numpy as np, pandas as pd
from numba import njit
from freqtrade.configuration import Configuration
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.enums import RunMode
cfgf, datadir, tr, out = sys.argv[1:5]
TAKER, MAKER, SLIP, STOP_SLIP = 0.0005, 0.0002, 0.0002, 0.0005
EXITS = [(0.02, 0.05, 24 * 12), (0.03, 0.08, 48 * 12), (0.05, 0.11, 72 * 12), (0.015, 0.03, 12 * 12)]   # 止盈, 止损, 最多几根5分钟


@njit(cache=True)
def label(o, h, l, c, idx, side, tp, sl, hold):
    n = len(o); res = np.full(len(idx), np.nan); dur = np.full(len(idx), -1, np.int16)
    for q in range(len(idx)):
        i = idx[q] + 1
        if i >= n - 1: continue
        e = o[i] * (1 + side * SLIP); tg = e * (1 + side * tp); st = e * (1 - side * sl); ex = np.nan; fee = TAKER
        for j in range(i, min(n, i + hold)):
            if (l[j] <= st) if side > 0 else (h[j] >= st):
                ex = (min(o[j], st) if side > 0 else max(o[j], st)) * (1 - side * STOP_SLIP); fee += TAKER; dur[q] = j - i + 1; break
            if (h[j] >= tg) if side > 0 else (l[j] <= tg):
                ex = tg; fee += MAKER; dur[q] = j - i + 1; break
        if np.isnan(ex):
            if i + hold > n: continue                   # 数据不够看完这笔，不算
            ex = c[min(n, i + hold) - 1] * (1 - side * SLIP); fee += TAKER; dur[q] = hold
        res[q] = side * (ex / e - 1) - fee
    return res, dur


c = json.load(open(cfgf)); px = os.environ['HTTPS_PROXY']
c['exchange']['ccxt_config'] = {'httpsProxy': px}; c['exchange']['ccxt_async_config'] = {'httpsProxy': px}
pairs = sorted({os.path.basename(f).split('_USDT_USDT')[0] + '/USDT:USDT' for f in glob.glob(datadir + '/futures/*-5m-futures.feather')})
if os.environ.get('PART'):
    k_, n_ = map(int, os.environ['PART'].split('/')); pairs = [p for i, p in enumerate(pairs) if i % n_ == k_ or p.startswith('BTC/')]
c['exchange']['pair_whitelist'] = pairs; c['pairlists'] = [{'method': 'StaticPairList'}]
c['strategy'] = 'NostalgiaForInfinityX7'; c['strategy_path'] = '/home/user/ext/nfisig/strat_all'; c['timerange'] = tr; c['timeframe'] = '5m'
fn = f'/tmp/_feat_{os.getpid()}.json'; json.dump(c, open(fn, 'w'))
config = Configuration({'config': [fn], 'datadir': datadir, 'user_data_dir': '/home/user/ext/ft'}, RunMode.BACKTEST).get_config()
bt = Backtesting(config); bt._set_strategy(bt.strategylist[0]); st = bt.strategy
data, _ = bt.load_bt_data(); print('pairs', len(data), flush=True)
os.makedirs(out + '/f', exist_ok=True)
NOISY = set('121 603 662 664 605 665 667 506 669 105 505 666 670 663 170 545 543 563 592'.split())   # 几乎每根都触发、筛选时和随机一样的条件，不当触发点
DROP = {'open', 'high', 'low', 'close', 'volume', 'CVD_BUY_VOL', 'CVD_SELL_VOL', 'LARGE_BUBBLE_THR', 'live_data_ok', 'bt_agefilter_ok', 'enter_long', 'enter_short', 'exit_long', 'exit_short'}
for pair, df in data.items():
    coin = pair.split('/')[0]; t = time.time()
    if os.path.exists(f'{out}/f/{coin}.parquet') or len(df) < 3000: continue
    try:
        d = st.ft_advise_signals(st.advise_indicators(df.copy(), {'pair': pair}), {'pair': pair})
    except Exception as e:
        print('ERR', pair, e, flush=True); continue
    d = d.reset_index(drop=True); tag = d.enter_tag.fillna('').astype(str).values
    trig = np.array([any(x not in NOISY for x in t.split()) for t in tag])   # 有 NFI 条件触发的K线（去掉每根都触发的两个空壳）
    idx = np.where((d.date.dt.minute == 55).values | trig)[0]; idx = idx[(idx > 2016) & (idx < len(d) - 1)]      # 前 7 天指标没算满，不要
    cl = d.close.values.astype(float); F = {}
    for col in d.columns:
        if col in DROP or col in ('date', 'enter_tag', 'exit_tag') or d[col].dtype.kind not in 'fiub': continue
        v = d[col].values.astype(float)
        s = v[idx]; ok = np.isfinite(s) & (cl[idx] > 0)
        if ok.sum() > 50 and np.corrcoef(s[ok], cl[idx][ok])[0, 1] > 0.9 and 0.5 < np.nanmedian(s[ok] / cl[idx][ok]) < 2:
            s = s / cl[idx] - 1                                   # 价格类（均线、布林带、最高最低价）→ 离现价多远
        F[col] = np.clip(s, -6e4, 6e4).astype(np.float16)
    # 自己加几个简单的：过去 1/4/24 小时涨跌、成交量相对 24 小时均量
    for nb, nm in ((12, 'ret_1h'), (48, 'ret_4h'), (288, 'ret_24h')):
        F[nm] = (cl[idx] / cl[np.maximum(idx - nb, 0)] - 1).astype(np.float32)
    vol = d.volume.values.astype(float); vm = pd.Series(vol).rolling(288, min_periods=50).mean().values
    F['vol_rel_1h'] = (pd.Series(vol).rolling(12).sum().values[idx] / (vm[idx] * 12 + 1e-12)).astype(np.float32)
    o, h, l = d.open.values.astype(float), d.high.values.astype(float), d.low.values.astype(float)
    for k_, (tp, sl, hd) in enumerate(EXITS):
        for side, nm in ((1.0, 'L'), (-1.0, 'S')):
            r_, du_ = label(o, h, l, cl, idx.astype(np.int64), side, tp, sl, hd); F[f'y{nm}{k_}'] = r_.astype(np.float32); F[f'd{nm}{k_}'] = du_
    Fd = pd.DataFrame(F); Fd.insert(0, 'hourly', (d.date.dt.minute.values[idx] == 55)); Fd.insert(0, 'tag', tag[idx]); Fd.insert(0, 'i', idx); Fd.insert(0, 'date', d.date.values[idx])
    Fd.to_parquet(f'{out}/f/{coin}.parquet', compression='zstd')
    print(coin, len(Fd), Fd.shape[1], round(time.time() - t), flush=True)
