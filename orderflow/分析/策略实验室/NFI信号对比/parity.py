# 搬进订单流的 of_nfi 和 freqtrade 全量结果对比：在有/没有 141~145 信号的K线上截断（每个周期只留 BARS 根），看最后一根信号是否一致
import sys, os, numpy as np, pandas as pd, time
sys.path.insert(0, '/home/user/j-j-j-j/orderflow')
from of_nfi import NfiSignals, BARS, TF_MIN
D = sys.argv[1]; SIG = sys.argv[2]; coins = sys.argv[3].split(','); N = int(sys.argv[4])
nf = NfiSignals(); assert not nf.err, nf.err
def feather(c, tf):
    return pd.read_feather(f'{D}/futures/{c}_USDT_USDT-{tf}-futures.feather')[['date', 'open', 'high', 'low', 'close', 'volume']]
ok = bad = 0; rng = np.random.default_rng(0); t0 = time.time()
for c in coins:
    full = pd.read_parquet(f'{SIG}/{c}.parquet'); full['t'] = full.enter_tag.fillna('').str.split().apply(lambda x: ' '.join(sorted(set(x) & set(nf.tags))))
    raw = {tf: feather(c, tf) for tf in BARS}; btc4 = feather('BTC', '4h')
    pos = full.index[full.t != ''].tolist(); neg = full.index[(full.t == '') & (full.index > 3000)].tolist()
    picks = list(rng.choice(pos, min(N, len(pos)), replace=False)) + list(rng.choice(neg, N, replace=False))
    for i in picks:
        cut = full.date.iloc[i]                       # 这根K线收盘时算
        fr = {}
        for tf, d in raw.items():
            d = d[d.date + pd.Timedelta(minutes=TF_MIN[tf]) <= cut + pd.Timedelta(minutes=5)].tail(BARS[tf]).reset_index(drop=True)
            fr[(f'{c}/USDT:USDT', tf)] = d
        fr[('BTC/USDT:USDT', '4h')] = btc4[btc4.date + pd.Timedelta(minutes=240) <= cut + pd.Timedelta(minutes=5)].tail(BARS['4h']).reset_index(drop=True)
        hit, tags = nf.signal(f'{c}/USDT:USDT', fr)
        want = full.t.iloc[i]
        same = (tags == want) and (hit == bool(want))
        ok += same; bad += not same
        if not same: print('不一致', c, cut, '程序:', hit, tags, '| freqtrade:', want, flush=True)
    print(c, '一致', ok, '不一致', bad, f'{time.time() - t0:.0f}s', flush=True)
print('合计 一致', ok, '不一致', bad)
