"""我们系统（老笔/新笔）vs chan.py vs czsc：同一份 30 分钟 K 线（5 个币近一年）的笔、线段、买卖点对比。"""
import json, re
import numpy as np
O = json.load(open('ours.json')); P = json.load(open('chanpy.json')); Z = json.load(open('czsc.json'))
BAR = 1800000


def pts(pens):
    s = set()
    for a, b in pens: s.add(a); s.add(b)
    return np.array(sorted(s))


def match(A, B, tol):
    if not len(A) or not len(B): return 0.0
    i = np.searchsorted(B, A); lo = B[np.clip(i - 1, 0, len(B) - 1)]; hi = B[np.clip(i, 0, len(B) - 1)]
    d = np.minimum(abs(A - lo), abs(A - hi)); return float((d <= tol * BAR).mean())


def cls(label):
    if label.startswith('类2') or label in ('2s',): return '2s'
    m = re.search(r'([123])', label); return m.group(1) if m else '?'


def cls_p(t):
    t = t.split(',')[0]
    return {'1': '1', '1p': '1', '2': '2', '2s': '2s', '3a': '3', '3b': '3'}.get(t, '?')


rows = []
for c in O:
    sysm = {'我们·老笔': O[c]['0'], '我们·新笔': O[c]['1'], 'chan.py': P[c], 'czsc': Z[c]}
    tp = {k: pts(v['pens']) for k, v in sysm.items()}
    r = dict(币=c, **{f'{k}笔数': len(v['pens']) for k, v in sysm.items()})
    for k in ('我们·老笔', '我们·新笔', 'czsc'):
        r[f'{k}转折点与chan.py同一根'] = f"{match(tp[k], tp['chan.py'], 0) * 100:.0f}%"
        r[f'{k}转折点与chan.py差3根内'] = f"{match(tp[k], tp['chan.py'], 3) * 100:.0f}%"
    r['老笔vs新笔同一根'] = f"{match(tp['我们·老笔'], tp['我们·新笔'], 0) * 100:.0f}%"
    r['老笔vsczsc同一根'] = f"{match(tp['我们·老笔'], tp['czsc'], 0) * 100:.0f}%"
    r['线段(老笔/新笔/chan.py)'] = f"{len(O[c]['0']['segs'])}/{len(O[c]['1']['segs'])}/{len(P[c]['segs'])}"
    # 买卖点：chan.py 的笔级买卖点，我们（老笔）在 ±3 根内有没有同方向的点、类别是否一致
    ob = O[c]['0']['bsp']; hit = same = 0
    for t, side, typ in P[c]['bsp']:
        near = [x for x in ob if x[1] == side and abs(x[0] - t) <= 3 * BAR]
        if near: hit += 1; same += any(cls(x[2]) == cls_p(typ) for x in near)
    r['买卖点数(我们老笔/chan.py)'] = f"{len(ob)}/{len(P[c]['bsp'])}"
    r['chan.py买卖点我们也有'] = f"{hit / max(1, len(P[c]['bsp'])) * 100:.0f}%"
    r['且类别相同'] = f"{same / max(1, len(P[c]['bsp'])) * 100:.0f}%"
    rows.append(r)
import pandas as pd
pd.set_option('display.width', 300); pd.set_option('display.max_columns', 50)
df = pd.DataFrame(rows).set_index('币').T
print(df.to_string())
df.to_csv('compare.csv')
