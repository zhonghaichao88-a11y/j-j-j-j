"""把 1 小时数据合成 UTC 日线（当天 24 根齐全才算），去掉价格冻结段。输出 {数据集: DataFrame(收盘价，行=日期，列=币)}。"""
import glob, os, json, numpy as np, pandas as pd
D = '/home/user/okx_data'
def npz(p): z = np.load(p); return {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close')}
def daily(f):
    h, l = f['high'], f['low']; ok = ~(h == l)      # 冻结K线
    s = pd.DataFrame({'ts': f['ts'][ok], 'o': f['open'][ok], 'h': h[ok], 'l': l[ok], 'c': f['close'][ok]})
    s['d'] = s.ts // 86400000
    g = s.groupby('d').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), n=('c', 'size'))
    g = g[g.n >= 24]; g.index = pd.to_datetime(g.index * 86400000, unit='ms'); return g
def build():
    out = {}
    sets = {'更早137(2021-10~2024-09)': [p for p in glob.glob(f'{D}/*_bnold1h.npz')],
            '币安独有416(2024-09~2026-08)': [p for p in glob.glob(f'{D}/*_bn1h.npz')],
            '币安前100(2024-05~2026-09)': glob.glob(f'{D}/bntop/*.npz'),
            '欧易(2024-09~2026-09)': [p for p in glob.glob(f'{D}/*_1h.npz') if '_bn' not in p] + glob.glob(f'{D}/okx64/*_1h.npz')}
    for name, ps in sets.items():
        C, O, H, L = {}, {}, {}, {}
        for p in ps:
            k = os.path.basename(p).split('_')[0].replace('.npz', '')
            g = daily(npz(p))
            if len(g) < 60: continue
            C[k], O[k], H[k], L[k] = g.c, g.o, g.h, g.l
        out[name] = dict(c=pd.DataFrame(C).sort_index(), o=pd.DataFrame(O).sort_index(), h=pd.DataFrame(H).sort_index(), l=pd.DataFrame(L).sort_index())
        print(name, out[name]['c'].shape, flush=True)
    pd.to_pickle(out, 'daily.pkl')
if __name__ == '__main__': build()
