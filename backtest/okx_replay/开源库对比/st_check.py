"""独立回测 vs freqtrade：FSupertrendStrategy_LS，老40/新178，两年，手续费0.1%，钱包990。"""
import json, glob, zipfile, numpy as np, pandas as pd, time
import st_bt as S, adx_bt as A
F = '/home/user/ext/ft/data/okx/futures'
def load(base, tf):
    d = pd.read_feather(f'{F}/{base}_USDT_USDT-{tf}-futures.feather')
    ts = ((d.date - pd.Timestamp(0, tz='UTC')) // pd.Timedelta('1ms')).astype('int64').values
    return dict(ts=ts, open=d.open.values, high=d.high.values, low=d.low.values, close=d.close.values)
btc = load('BTC', '1d')
groups = {'老40': [i.split('-')[0] for i in json.load(open('/home/user/okx_data/universe_5m40.json'))]}
cfg = json.load(open('ft/config.json')); 
for g in ('新178',):
    try:
        z = glob.glob(f'ft/bt_regime/FSupertrendStrategy_LS__{g}__第一年/*.zip')[0]; Z = zipfile.ZipFile(z)
        n = [x for x in Z.namelist() if x.endswith('.json') and '_config' not in x][0]
        s = list(json.loads(Z.read(n))['strategy'].values())[0]
        groups[g] = sorted({x.split('/')[0] for x in s['pairlist']})
    except Exception as e: print('新178 列表读取失败', e)
for g, pairs in groups.items():
    frames = {}
    for p in pairs:
        try: frames[p + '/USDT:USDT'] = load(p, '1h')
        except Exception: pass
    for name, t0, t1 in (('第一年', '2024-09-29', '2025-09-29'), ('第二年', '2025-09-29', '2026-09-30')):
        a, b = (int(pd.Timestamp(x, tz='UTC').timestamp() * 1000) for x in (t0, t1))
        t = time.time(); T = S.run(frames, btc, fee=0.001, t0=a, t1=b, wallet=990)
        print(g, name, '独立回测', A.summary(T), f'{time.time() - t:.0f}秒', flush=True)
        z = glob.glob(f'ft/bt_regime/FSupertrendStrategy_LS__{g}__{name}/*.zip')
        if z:
            Z = zipfile.ZipFile(z[0]); n = [x for x in Z.namelist() if x.endswith('.json') and '_config' not in x][0]
            s = list(json.loads(Z.read(n))['strategy'].values())[0]; tr = pd.DataFrame(s['trades'])
            print(g, name, 'freqtrade', dict(笔数=len(tr), 空单=int(tr.is_short.sum()), 胜率=f'{(tr.profit_ratio > 0).mean() * 100:.0f}%',
                  每笔=f'{tr.profit_ratio.mean() * 100:+.3f}%', 收益=f"{s['profit_total'] * 100:+.1f}%", 最大回撤=f"{s['max_drawdown_account'] * 100:.1f}%",
                  出场=tr.exit_reason.value_counts().to_dict()), flush=True)
            print('  独立回测出场', T.why.value_counts().to_dict())
