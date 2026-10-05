# 程序版（of_nfi：NfiSignals.evaluate + tf15_triggers + vn_features，每个周期只用 BARS 根）vs 回测成交表（L / L2 / L3）
import sys, os, numpy as np, pandas as pd
sys.path.insert(0, '/home/user/j-j-j-j/orderflow')
import of_nfi as N
D = sys.argv[1]; seg = sys.argv[2]; coins = sys.argv[3].split(','); NEG = int(sys.argv[4])
nf = N.NfiSignals(); assert not nf.err, nf.err
R5 = ['L_A141_0.04|own', 'L_A142_0.04|own', 'L_B143_0.02|own', 'L_C144_0.1|own', 'L_D145_0.02|own']
R15 = ['L_15mA142_0.05|any', 'L_15mA141_0.05|any', 'L_15mA142_0.04|own', 'L_15mA141_0.06|any', 'L_15mC144_0.15|own']
RV = ['L_VN4_4.0', 'L_VN1_5.0']
fe = lambda c, tf: pd.read_feather(f'{D}/futures/{c}_USDT_USDT-{tf}-futures.feather')[['date', 'open', 'high', 'low', 'close', 'volume']]
def sigtimes(dirn, c, rules):
    f = f'/home/user/ext/nfisig/{dirn}_{seg}/{c}.parquet'
    if not os.path.exists(f): return set()
    x = pd.read_parquet(f); x = x[x.rule.astype(str).isin(rules)]
    return set((pd.to_datetime(x.t, unit='ms', utc=True) - pd.Timedelta('5min')).tolist())     # 表里存的是进场K线，信号K线早一根
rng = np.random.default_rng(0); tot = {}; bad = {}
for c in coins:
    raw = {tf: fe(c, tf) for tf in N.BARS}; btc4 = fe('BTC', '4h')
    S = {'nfi_5m': sigtimes('L', c, R5), 'nfi_15m': sigtimes('L2', c, R15), 'vn_dip': sigtimes('L3', c, RV)}
    d5all = raw['5m']; lo = d5all.date.iloc[3000]
    cand = {k: [t for t in v if t > lo] for k, v in S.items()}
    neg = {'nfi_5m': d5all.date[d5all.date > lo].sample(NEG, random_state=1).tolist(),
           'nfi_15m': d5all.date[(d5all.date > lo) & (d5all.date.dt.minute % 15 == 10)].sample(NEG, random_state=2).tolist(),
           'vn_dip': d5all.date[(d5all.date > lo) & (d5all.date.dt.minute == 55)].sample(NEG, random_state=3).tolist()}
    for kind in S:
        pts = list(rng.choice(cand[kind], min(15, len(cand[kind])), replace=False)) if cand[kind] else []
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
            if got != want:
                bad[kind] = bad.get(kind, 0) + 1; print('不一致', kind, c, cut, '程序', got, '回测', want, flush=True)
    print(c, {k: (tot.get(k, 0), bad.get(k, 0)) for k in S}, flush=True)
print('合计（抽查数, 不一致数）', {k: (tot[k], bad.get(k, 0)) for k in tot})
