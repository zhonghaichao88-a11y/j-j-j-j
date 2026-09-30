"""独立回测 vs freqtrade：老40币（与 freqtrade 同一份数据：ft/data/okx/futures 1h）、BTC 日线同源，固定每笔100U，手续费0.1%。"""
import json, glob, zipfile, sys
import pandas as pd
import adx_bt as A
F = '/home/user/ext/ft/data/okx/futures'
pairs = [i.split('-')[0] for i in json.load(open('/home/user/okx_data/universe_5m40.json'))]


def load(base, tf):
    d = pd.read_feather(f'{F}/{base}_USDT_USDT-{tf}-futures.feather')
    return dict(ts=(d.date.astype('int64') // 10**6).values if str(d.date.dtype).endswith('ns, UTC]') else (d.date - pd.Timestamp(0, tz='UTC')) // pd.Timedelta('1ms'),
                open=d.open.values, high=d.high.values, low=d.low.values, close=d.close.values)


frames = {p + '/USDT:USDT': load(p, '1h') for p in pairs}
for k in frames: frames[k]['ts'] = pd.Series(frames[k]['ts']).astype('int64').values
btc = load('BTC', '1d'); btc['ts'] = pd.Series(btc['ts']).astype('int64').values
for name, t0, t1 in (('第一年', '2024-09-29', '2025-09-29'), ('第二年', '2025-09-29', '2026-09-30')):
    a, b = (int(pd.Timestamp(x, tz='UTC').timestamp() * 1000) for x in (t0, t1))
    T = A.run(frames, btc, fee=0.001, t0=a, t1=b, wallet=990)
    print(name, '独立回测', A.summary(T))
    z = glob.glob(f'ft/bt_adx/ADXMomentum_BTC__老40__{name}__0.001/*.zip')
    if z:
        Z = zipfile.ZipFile(z[0]); n = [x for x in Z.namelist() if x.endswith('.json') and '_config' not in x][0]
        s = list(json.loads(Z.read(n))['strategy'].values())[0]; t = pd.DataFrame(s['trades'])
        print(name, 'freqtrade', dict(笔数=len(t), 胜率=f'{(t.profit_ratio > 0).mean() * 100:.0f}%', 每笔=f'{t.profit_ratio.mean() * 100:+.3f}%',
                                      收益=f"{s['profit_total'] * 100:+.1f}%", 最大回撤=f"{s['max_drawdown_account'] * 100:.1f}%"))
