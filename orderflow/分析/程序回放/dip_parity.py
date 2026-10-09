"""急跌抄底三个打法的信号：程序版（of_nfi，每个周期只用程序会拉的那么多根K线）vs 长周期回测（dip_sig_long.py / long_of.py）。
抽查：每个币每个打法 回测有信号的K线（最多 POS 根）+ 随机没信号的K线（NEG 根，15 分钟 / 整点那种只在对应时刻抽）。
数据：/home/user/ext/ft/data_long（2020~2026，币安转成的欧易格式）。"""
import sys, os, glob, numpy as np, pandas as pd
sys.path.insert(0, '/home/user/j-j-j-j/orderflow')
import of_nfi as N
D = '/home/user/ext/ft/data_long/okx/futures'
POS, NEG = int(os.environ.get('POS', 30)), int(os.environ.get('NEG', 30))
coins = sys.argv[1].split(',')
nf = N.NfiSignals(); assert not nf.err, nf.err
fe = lambda c, tf: pd.read_feather(f'{D}/{c}_USDT_USDT-{tf}-futures.feather')[['date', 'open', 'high', 'low', 'close', 'volume']]
V = pd.read_parquet('/home/user/ext/nfisig/long_vn.parquet')
rng = np.random.default_rng(0); tot = {}; bad = {}; rows = []
for c in coins:
    raw = {tf: fe(c, tf) for tf in N.BARS}; btc4 = fe('BTC', '4h')
    L = pd.concat([pd.read_parquet(f) for f in glob.glob(f'/home/user/ext/nfisig/LY/*/{c}.parquet')])
    S = {k: set(pd.to_datetime(L[L.rule == k].t - 300_000, unit='ms', utc=True)) for k in ('nfi_5m', 'nfi_15m')}
    S['vn_dip'] = set(pd.to_datetime(V[V.coin == c].t - 300_000, unit='ms', utc=True))
    d5all = raw['5m']; lo = d5all.date.iloc[3000] + pd.Timedelta(days=1); hi = d5all.date.iloc[-600]
    lo = max(lo, pd.Timestamp('2020-04-01', tz='UTC'))
    rng_ = d5all.date[(d5all.date > lo) & (d5all.date < hi)]
    neg = {'nfi_5m': rng_.sample(NEG, random_state=1).tolist(),
           'nfi_15m': rng_[rng_.dt.minute % 15 == 10].sample(NEG, random_state=2).tolist(),
           'vn_dip': rng_[rng_.dt.minute == 55].sample(NEG, random_state=3).tolist()}
    for kind in S:
        cand = sorted(t for t in S[kind] if lo < t < hi)
        pts = list(rng.choice(cand, min(POS, len(cand)), replace=False)) if cand else []
        for cut in pts + neg[kind]:
            cut = pd.Timestamp(cut)
            fr = {(f'{c}/USDT:USDT', tf): d[d.date + pd.Timedelta(minutes=N.TF_MIN[tf]) <= cut + pd.Timedelta(minutes=5)].tail(N.BARS[tf]).reset_index(drop=True) for tf, d in raw.items()}
            fr[('BTC/USDT:USDT', '4h')] = btc4[btc4.date + pd.Timedelta(minutes=240) <= cut + pd.Timedelta(minutes=5)].tail(N.BARS['4h']).reset_index(drop=True)
            d5 = fr[(f'{c}/USDT:USDT', '5m')]
            if kind == 'nfi_5m':
                fired, ok = nf.evaluate(f'{c}/USDT:USDT', fr); got = bool(fired)
            elif kind == 'nfi_15m':
                fired, ok = nf.evaluate(f'{c}/USDT:USDT', fr)
                got = any((g == 'any' and ok) or g in ok for _, g in N.tf15_triggers(d5))
            else:
                f = N.vn_features(d5); got = bool(f and (f['vn1'] < -5 or f['vn4'] < -4))
            want = cut in S[kind]
            tot[kind] = tot.get(kind, 0) + 1
            rows.append((c, kind, cut, want, got))
            if got != want:
                bad[kind] = bad.get(kind, 0) + 1; print('不一致', kind, c, cut, '程序', got, '回测', want, flush=True)
    print(c, {k: (tot.get(k, 0), bad.get(k, 0)) for k in S}, flush=True)
pd.DataFrame(rows, columns=['coin', 'kind', 't', 'backtest', 'program']).to_csv(f'/home/user/ext/replay/dip_parity_{coins[0]}.csv', index=False)
print('合计（抽查数, 不一致数）', {k: (tot[k], bad.get(k, 0)) for k in tot})
