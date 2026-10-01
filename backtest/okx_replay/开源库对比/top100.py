"""方案三在“实盘选币口径”上：欧易当前 24h 成交额前 100 个加密币（和实盘完全一样），欧易1小时数据 2024-09-30 ~ 2026-09-30。"""
import ccxt, os, glob, json, numpy as np, pandas as pd
import adx_bt as A
D = '/home/user/okx_data'
px = os.environ.get('HTTPS_PROXY'); ex = ccxt.okx({'proxies': {'http': px, 'https': px}}); ex.load_markets()
crypto = {m['id'] for m in ex.markets.values() if m.get('swap') and m.get('quote') == 'USDT' and m.get('settle') == 'USDT' and m.get('active') and str((m.get('info') or {}).get('instCategory') or '1') == '1'}
r = ex.publicGetMarketTickers({'instType': 'SWAP'})['data']
top = [i for _, i in sorted([(float(x['volCcy24h'] or 0) * float(x['last'] or 0), x['instId']) for x in r if x['instId'] in crypto], reverse=True)[:100]]
json.dump(top, open('top100_now.json', 'w'))
def npz(p): z = np.load(p); return {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close')}
fr = {}
for s in top:
    for p in (f'{D}/{s}_1h.npz', f'{D}/okx64/{s}_1h.npz'):
        if os.path.exists(p): fr[s] = npz(p); break
F = '/home/user/ext/ft/data/okx/futures'
for s in top:
    if s in fr: continue
    p = f"{F}/{s.split('-')[0]}_USDT_USDT-1h-futures.feather"
    if os.path.exists(p):
        d = pd.read_feather(p); ts = ((d.date - pd.Timestamp(0, tz='UTC')) // pd.Timedelta('1ms')).astype('int64').values
        fr[s] = dict(ts=ts, open=d.open.values, high=d.high.values, low=d.low.values, close=d.close.values)
print('前100里有数据的', len(fr), '缺', [s for s in top if s not in fr])
b = npz(f'{D}/okx64/BTC_1d.npz'); btc = dict(ts=b['ts'], close=b['close'])
ms = lambda x: int(pd.Timestamp(x, tz='UTC').timestamp() * 1000)
rows = []
for lab, a, c in (('第一年', '2024-10-01', '2025-10-01'), ('第二年', '2025-10-01', '2026-10-01'), ('两年合计', '2024-10-01', '2026-10-01')):
    for fee in (0.001, 0.0015):
        T = A.run(fr, btc, fee=fee, t0=ms(a), t1=ms(c), wallet=990, short=True); T = T[T.why != '结束']
        w = T.r > 0; pf = T.r[w].sum() / -T.r[~w].sum()
        g = T.groupby('why').r.agg(['size', 'mean'])
        rows.append(dict(段=lab, 成本=fee, 笔数=len(T), 胜率=f'{w.mean()*100:.1f}%', PF=round(pf, 3), 每笔=f'{T.r.mean()*100:+.3f}%', 合计U=f'{T.r.sum()*100:+.0f}',
                         反向平仓=f"{int(g.loc['信号','size'])}笔 {g.loc['信号','mean']*100:+.2f}%" if '信号' in g.index else '-', 止损=int(g.loc['止损','size']) if '止损' in g.index else 0))
        print(rows[-1], flush=True)
