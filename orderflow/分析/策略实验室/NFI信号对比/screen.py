"""NFI X7 全部 81 个进场条件（包括作者默认关掉的）逐个筛选。
每个条件单独当一个打法：信号K线收盘后下一根开盘进场，同一个币有仓位时跳过。
出场三种：A 不补 止盈2%/止损5%/24h；B 不补 止盈2%/止损11%/72h；C 补仓 -2/-4/-6/-8% 1:1:1:1:1，均价止盈2%，离第一笔11%止损，72h。
事先定好的过关规则（每种出场分别判）：
  三段（2022-23 / 2024-25 / 2025-26）里有信号的段 ≥2 段，且每段：笔数 ≥20、PF ≥1.3、比同方向随机进场 PF 高 ≥0.2、前后两半 PF 都 ≥1；
  全部合计 ≥60 笔，且合计赚钱的币 ≥60%。"""
import sys, os, glob, numpy as np, pandas as pd
sys.path.insert(0, '/home/user/j-j-j-j/orderflow/分析/策略实验室/实战版完整重测')
from sweep_check import dca, LV, CFGS
HERE = os.path.dirname(os.path.abspath(__file__))
EX = {'A': (np.array([0.0]), np.array([1.0]), 0.02, 0.05, 24 * 60),
      'B': (np.array([0.0]), np.array([1.0]), 0.02, 0.11, 72 * 60),
      'C': (LV, CFGS['①1:1:1:1:1'], 0.02, 0.11, 72 * 60)}
SEG = {'2022-23': 'old', '2024-25': 'mid', '2025-26': 'new'}


def pf(x):
    x = np.asarray(x); n = -x[x < 0].sum(); return x[x > 0].sum() / n if n > 0 else (9.99 if len(x) else np.nan)


def load(seg):
    for f in sorted(glob.glob(f'/home/user/ext/nfisig/{SEG[seg]}/*.parquet')):
        k = pd.read_parquet(f)
        if len(k) < 3000: continue
        om = (pd.to_datetime(k.date).astype('int64').values // 60_000_000_000).astype(np.int64)
        yield os.path.basename(f)[:-8], om, *(k[x].values.astype(float) for x in ('open', 'high', 'low', 'close')), k


def run():
    rows = []
    for seg in SEG:
        for c, om, o, h, l, cl, k in load(seg):
            el = k.get('enter_long', pd.Series(0, index=k.index)).fillna(0).values > 0
            es = k.get('enter_short', pd.Series(0, index=k.index)).fillna(0).values > 0
            tg = k.enter_tag.fillna('').astype(str).values
            per = {}
            for i in np.where(el | es)[0]:
                if i + 1 >= len(om) - 1: continue
                for t in tg[i].split():
                    per.setdefault(t, []).append(i + 1)
            rng = np.random.default_rng(abs(hash(c + seg)) % 2**32)
            ri = np.sort(rng.integers(0, len(om) - 1, 300)).astype(np.int64)
            for ex, (lv, wt, tp, sl, hd) in EX.items():
                for s in (1.0, -1.0):          # 随机对照：每个币每段 300 笔
                    rr, rt, _, _ = dca(om, o, h, l, cl, ri, o[ri], np.full(len(ri), s), lv, wt, tp, sl, hd)
                    rows += [(seg, ex, 'R' + ('L' if s > 0 else 'S'), c, int(t), x) for t, x in zip(rt, rr)]
                for t, idx in per.items():
                    i0 = np.array(sorted(set(idx)), np.int64); s = -1.0 if int(t) >= 500 else 1.0
                    r, t0, _, _ = dca(om, o, h, l, cl, i0, o[i0], np.full(len(i0), s), lv, wt, tp, sl, hd)
                    rows += [(seg, ex, t, c, int(a), x) for a, x in zip(t0, r)]
    D = pd.DataFrame(rows, columns=['段', '出场', 'tag', '币', 't', 'ret'])
    D.to_parquet('/home/user/ext/nfisig/screen_trades.parquet'); return D


if __name__ == '__main__':
    D = pd.read_parquet('/home/user/ext/nfisig/screen_trades.parquet') if os.environ.get('REUSE') else run()
    R = D[D.tag.str.startswith('R')]; T = D[~D.tag.str.startswith('R')]
    rpf = R.groupby(['段', '出场', 'tag']).ret.apply(pf)
    out = []
    for (ex, tag), g in T.groupby(['出场', 'tag']):
        side = 'RS' if int(tag) >= 500 else 'RL'; segs = []; ok = True
        for seg, gs in g.groupby('段'):
            gs = gs.sort_values('t'); h = len(gs) // 2
            p, base = pf(gs.ret), rpf.get((seg, ex, side), np.nan)
            p1, p2 = pf(gs.ret[:h]), pf(gs.ret[h:])
            segs.append(f'{seg}:{len(gs)}笔 胜{(gs.ret > 0).mean():.0%} PF{p:.2f}(随机{base:.2f}) 半{p1:.1f}/{p2:.1f}')
            ok &= len(gs) >= 20 and p >= 1.3 and p - base >= 0.2 and p1 >= 1 and p2 >= 1
        cw = (g.groupby('币').ret.sum() > 0).mean()
        ok &= g.段.nunique() >= 2 and len(g) >= 60 and cw >= 0.6
        out.append(dict(出场=ex, tag=tag, 过关=ok, 笔=len(g), PF=round(pf(g.ret), 2), 胜=round((g.ret > 0).mean(), 3),
                        赚钱币=round(cw, 2), 明细=' | '.join(segs)))
    S = pd.DataFrame(out).sort_values(['出场', '过关', 'PF'], ascending=[True, False, False])
    S.to_csv(HERE + '/screen_结果.csv', index=False)
    for ex in EX:
        s = S[S.出场 == ex]
        print(f'\n=== 出场 {ex}：{s.过关.sum()} / {len(s)} 个条件过关')
        print(s[s.过关][['tag', '笔', 'PF', '胜', '赚钱币', '明细']].to_string(index=False))
    print('\n随机基准：'); print(rpf.unstack().round(2).to_string())
