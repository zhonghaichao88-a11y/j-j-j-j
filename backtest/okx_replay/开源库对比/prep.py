# 导出 30 分钟 K 线（近一年）为 chan.py 的 CSV 与 czsc 用的 npz；并算我们系统的笔/线段/买卖点
import sys, json, numpy as np, datetime as dt
sys.path.insert(0, '/home/user/j-j-j-j'); sys.path.insert(0, '/home/user/okx_data')
import alpha_v7_chan as C, live_gap as L
from qjt import macd
COINS = ['ETH', 'BTC', 'SOL', 'DOGE', 'HBAR']
out = {}
for c in COINS:
    f = L.ld(f'/home/user/okx_data/{c}-USDT-SWAP_30m.npz', 1800000); f = {k: v[-17520:] for k, v in f.items()}
    with open(f'/home/user/ext/chan.py/{c}_30m.csv', 'w') as w:
        w.write('time,open,high,low,close\n')
        for i in range(len(f['ts'])):
            t = dt.datetime.utcfromtimestamp(f['ts'][i] / 1000).strftime('%Y-%m-%d %H:%M:%S')
            w.write(f"{t},{f['open'][i]},{f['high'][i]},{f['low'][i]},{f['close'][i]}\n")
    np.savez(f'/home/user/ext/cmp/{c}.npz', **f)
    dif, h = macd(f['close']); res = {}
    for pm in (0, 1):
        r = C.analyze(f, h, max_level=1, signal_level=0, pen_mode=pm)
        res[pm] = dict(pens=[[int(x['a']['t']), int(x['b']['t'])] for x in r['strokes'] if x['confirmed']],
                       segs=[[int(x['a']['t']), int(x['b']['t'])] for x in r['segments']],
                       bsp=[[int(x['ts']), int(x['side']), x['label']] for x in r['signals']],
                       bsp_seg=[[int(x['ts']), int(x['side']), x['label']] for x in r['display_signals'] if x.get('level') == 1])
    out[c] = res; print(c, len(f['ts']), {pm: (len(v['pens']), len(v['segs']), len(v['bsp'])) for pm, v in res.items()}, flush=True)
json.dump(out, open('/home/user/ext/cmp/ours.json', 'w'))
