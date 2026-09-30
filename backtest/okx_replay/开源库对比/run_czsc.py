import json, datetime as dt
import numpy as np
from czsc import CZSC, RawBar, Freq

out = {}
for c in ['ETH', 'BTC', 'SOL', 'DOGE', 'HBAR']:
    z = np.load(f'/home/user/ext/cmp/{c}.npz')
    bars = [RawBar(symbol=c, id=i, dt=dt.datetime.utcfromtimestamp(int(z['ts'][i]) / 1000), freq=Freq.F30,
                   open=float(z['open'][i]), close=float(z['close'][i]), high=float(z['high'][i]), low=float(z['low'][i]),
                   vol=float(z['volume'][i]), amount=float(z['volume'][i] * z['close'][i])) for i in range(len(z['ts']))]
    cz = CZSC(bars, max_bi_num=100000)

    def ms(d):
        return int(d.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    pens = [[ms(b.fx_a.dt), ms(b.fx_b.dt)] for b in cz.bi_list]
    out[c] = dict(pens=pens)
    print(c, len(pens), flush=True)
json.dump(out, open('/home/user/ext/cmp/czsc.json', 'w'))
