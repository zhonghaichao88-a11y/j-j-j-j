"""NFI X7 的进场信号 vs 随机进场，用和扫止损一样的出场比：
① 5 笔补仓 -2/-4/-6/-8%（1:1:1:1:1 和 1:1:2:2:4），均价止盈 2%，离第一笔 11% 止损，72 小时；
② 不补仓：止盈 2% / 止损 11% / 72 小时；③ 不补仓：止盈 2% / 止损 5% / 24 小时。
信号来自 freqtrade 直接跑 NostalgiaForInfinityX7 每根 5 分钟K线的 enter_long/enter_short（不受最多持仓数限制），
信号K线收盘后下一根开盘进场；同一个币有仓位时新信号跳过（和扫止损测试一样）。
数据：OKX 2022-2023（17 币）、2024-10~2025-09（40 币）、2025-10~2026-09（221 币）。"""
import sys, os, glob, numpy as np, pandas as pd
sys.path.insert(0, '/home/user/j-j-j-j/orderflow/分析/策略实验室/实战版完整重测')
from sweep_check import dca, LV, CFGS
EX = {**{'补仓' + k: (LV, w, 0.02, 0.11, 72 * 60) for k, w in CFGS.items()},
      '不补 止盈2 止损11 72h': (np.array([0.0]), np.array([1.0]), 0.02, 0.11, 72 * 60),
      '不补 止盈2 止损5 24h': (np.array([0.0]), np.array([1.0]), 0.02, 0.05, 24 * 60)}
SEG = {'2022-23': 'old', '2024-25': 'mid', '2025-26': 'new'}


def pf(x):
    x = np.asarray(x); n = -x[x < 0].sum(); return x[x > 0].sum() / n if n > 0 else np.nan


rows = []; tags = []
for seg, d in SEG.items():
    for f in sorted(glob.glob(f'/home/user/ext/nfisig/{d}/*.parquet')):
        c = os.path.basename(f)[:-8]; k = pd.read_parquet(f)
        if len(k) < 3000: continue
        om = pd.to_datetime(k.date).dt.tz_localize(None).values.astype('datetime64[m]').astype(np.int64)
        o, h, l, cl = (k[x].values.astype(float) for x in ('open', 'high', 'low', 'close'))
        el = k.get('enter_long', pd.Series(0, index=k.index)).fillna(0).values > 0
        es = k.get('enter_short', pd.Series(0, index=k.index)).fillna(0).values > 0
        sig = np.where(el)[0].tolist() + np.where(es)[0].tolist()
        sides = np.array([1.0] * el.sum() + [-1.0] * es.sum())
        if not sig: continue
        i0 = np.array(sig) + 1; ok = i0 < len(om) - 1; i0, sides = i0[ok], sides[ok]
        srt = np.argsort(i0, kind='stable'); i0, sides = i0[srt].astype(np.int64), sides[srt]
        for t in k.enter_tag[el | es].fillna('').values: tags.append((seg, str(t).strip()))
        rng = np.random.default_rng(abs(hash(c + seg)) % 2**32)
        for nm, (lv, wt, tp, sl, hd) in EX.items():
            r, t0, _, _ = dca(om, o, h, l, cl, i0, o[i0], sides, lv, wt, tp, sl, hd)
            ri = np.sort(rng.integers(0, len(om) - 1, max(len(i0) * 3, 30))).astype(np.int64)
            rr, _, _, _ = dca(om, o, h, l, cl, ri, o[ri], rng.choice(sides, len(ri)), lv, wt, tp, sl, hd)
            rows += [(seg, nm, 'NFI', c, x) for x in r] + [(seg, nm, '随机', c, x) for x in rr]
D = pd.DataFrame(rows, columns=['段', '出场', '进场', '币', 'ret'])
D.to_csv(os.path.dirname(os.path.abspath(__file__)) + '/nfi_trades.csv.gz', index=False)
out = []
for (seg, nm), g in D.groupby(['段', '出场'], sort=False):
    a = g[g.进场 == 'NFI']; b = g[g.进场 == '随机']
    cw = a.groupby('币').ret.sum()
    out.append(f'{seg} {nm}: NFI {len(a)}笔 胜{(a.ret > 0).mean():.0%} PF{pf(a.ret):.2f} 平均{a.ret.mean()*100:+.2f}% 赚钱币{(cw > 0).mean():.0%} | 随机 胜{(b.ret > 0).mean():.0%} PF{pf(b.ret):.2f}')
T = pd.DataFrame(tags, columns=['段', 'tag'])
out.append('\n信号最多的 tag：\n' + T.groupby('段').tag.value_counts().groupby(level=0).head(8).to_string())
txt = '\n'.join(out); print(txt)
open(os.path.dirname(os.path.abspath(__file__)) + '/结果.txt', 'w').write(__doc__ + '\n' + txt + '\n')
