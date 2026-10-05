"""把过关的 NFI 条件（Top Coins 模式 141~145，只做多）合成一个打法：任一条件触发就进，同一个币有仓位跳过。
组合回测：每笔用权益 10%，最多同时 10 单，复利，100U 起。看每天多少单、收益、回撤。止盈止损多试几档。"""
import sys, os, numpy as np, pandas as pd
sys.path.insert(0, '/home/user/j-j-j-j/orderflow/分析/策略实验室/实战版完整重测'); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sweep_check import dca, LV, CFGS, port
from screen import load, SEG, pf
TAGS = set(os.environ.get('TAGS', '141,142,143,144,145').split(','))
EX = {f'不补 止盈{tp}% 止损{sl}% {hd}h': (np.array([0.0]), np.array([1.0]), tp / 100, sl / 100, hd * 60)
      for tp, sl, hd in [(2, 5, 24), (2, 11, 72), (3, 5, 24), (3, 8, 48), (5, 8, 72), (5, 11, 72), (8, 11, 120)]}
EX['补仓 -2/-4/-6/-8 止盈2% 止损11% 72h'] = (LV, CFGS['①1:1:1:1:1'], 0.02, 0.11, 72 * 60)
EX['补仓 -2/-4/-6/-8 止盈3% 止损11% 72h'] = (LV, CFGS['①1:1:1:1:1'], 0.03, 0.11, 72 * 60)
rows = []; days = {}
for seg in SEG:
    t_lo, t_hi = None, None
    for c, om, o, h, l, cl, k in load(seg):
        t_lo = om[0] if t_lo is None else min(t_lo, om[0]); t_hi = om[-1] if t_hi is None else max(t_hi, om[-1])
        tg = k.enter_tag.fillna('').astype(str).str.split().apply(lambda x: bool(TAGS & set(x))).values
        i0 = np.where(tg[:-2])[0].astype(np.int64) + 1
        if not len(i0): continue
        for nm, (lv, wt, tp, sl, hd) in EX.items():
            r, t0, t1, _ = dca(om, o, h, l, cl, i0, o[i0], np.ones(len(i0)), lv, wt, tp, sl, hd)
            rows += [(seg, nm, c, a * 60000, b * 60000, x) for a, b, x in zip(t0, t1, r)]
    days[seg] = (t_hi - t_lo) / 1440
D = pd.DataFrame(rows, columns=['段', '出场', 'coin', 't_in', 't_out', 'ret']).assign(src='NFI')
out = [f'条件：{sorted(TAGS)}']
for nm in EX:
    out.append(f'\n{nm}')
    for seg in SEG:
        X = D[(D.段 == seg) & (D.出场 == nm)]
        if not len(X): continue
        p = port(X)
        out.append(f'  {seg}: 信号单 {len(X)} 笔 胜{(X.ret > 0).mean():.0%} PF{pf(X.ret):.2f} | 组合 100U→{p["final"]}U 回撤{p["dd"]}% 实际开 {p["n"]} 单（每天 {p["n"] / days[seg]:.2f} 单） 最差一笔 {p["worst"]}%')
print('\n'.join(out)); open(os.path.dirname(os.path.abspath(__file__)) + '/combo_结果.txt', 'w').write(__doc__ + '\n' + '\n'.join(out) + '\n')
