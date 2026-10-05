"""规则实验室评估（事先定好）：
每条规则 = 触发（急跌/急涨类型 + 幅度）× 安全检查（own=NFI 原条件的检查 / any=5 段做多检查任一 / glob=NFI 全局保护 / none=不检查）。
币的范围：头部币（NFI 名单 55 个）/ 全部币。出场 4 种。
过关：三段（2022-23 / 2024-25 / 2025-26）每段都 ≥30 笔（同币不重叠）、PF ≥ 1.3、胜率 ≥ 60%、组合（10% 仓位、最多 10 单）赚钱、前后两半 PF 都 ≥ 1。
然后把过关的规则合成一个做多打法、一个做空打法（任一触发就进，同币不重叠），再跑三段组合。"""
import os, sys, glob, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model import no_overlap, port, pf, EXN
HERE = os.path.dirname(os.path.abspath(__file__))
_P = os.environ.get('LABP', 'L')
SEGS = {'2022-23': f'{_P}_old', '2024-25': f'{_P}_mid', '2025-26': f'{_P}_new'}
DAYS = {'2022-23': 756, '2024-25': 359, '2025-26': 359}
if _P == 'L3':
    EXN = {**EXN, 4: '止盈1倍ATR 止损3倍ATR 48h', 5: '回到24小时均线就走 止损8% 48h', 6: '赚2%后回撤1%走 止损6% 48h'}
TOP = set(open('/home/user/ext/nfisig/top.txt').read().split())


def load():
    P = []
    for seg, d in SEGS.items():
        for f in glob.glob(f'/home/user/ext/nfisig/{d}/*.parquet'):
            x = pd.read_parquet(f)
            if not len(x): continue
            x['coin'] = os.path.basename(f)[:-8]; x['seg'] = seg; x['rule'] = x.rule.astype(str); P.append(x)
    D = pd.concat(P, ignore_index=True); D['top'] = D.coin.isin(TOP)
    return D


def stats(X, k, seg):
    y, dc = f'y{k}', f'd{k}'
    X = X[X[y].notna()]
    if len(X) < 5: return None
    X = no_overlap(X, dc); Xs = X.sort_values('t'); h = len(Xs) // 2
    fin, dd = port(X, y, dc)
    return dict(笔=len(X), 每天=round(len(X) / DAYS[seg], 2), 胜=round((X[y] > 0).mean(), 3), PF=round(pf(X[y]), 2),
                平均=round(X[y].mean() * 100, 3), 前半=round(pf(Xs[y][:h]), 2), 后半=round(pf(Xs[y][h:]), 2), 组合=fin, 回撤=dd,
                币数=X.coin.nunique())


def passed(rs):
    return all(r is not None and r['笔'] >= 30 and r['PF'] >= 1.3 and r['胜'] >= 0.6 and r['组合'] > 100 and r['前半'] >= 1 and r['后半'] >= 1 for r in rs)


if __name__ == '__main__':
    D = load(); print('成交记录', len(D), '规则', D.rule.nunique(), flush=True)
    out = []
    for uni in ('头部币', '全部币'):
        U = D[D.top] if uni == '头部币' else D
        for rule, g in U.groupby('rule'):
            for k in EXN:
                rs = [stats(g[g.seg == s], k, s) for s in SEGS]
                ok = passed(rs)
                row = dict(范围=uni, 规则=rule, 出场=EXN[k], k=k, 过关=ok)
                for s, r in zip(SEGS, rs):
                    row[s] = '无' if r is None else f"{r['笔']}笔 每天{r['每天']} 胜{r['胜']:.0%} PF{r['PF']} 均{r['平均']:+.2f}% 组合{r['组合']} 撤{r['回撤']}%"
                good = [r for r in rs if r]
                row['最差PF'] = min((r['PF'] for r in good), default=0); row['平均每天'] = round(np.mean([r['每天'] for r in good]), 2) if good else 0
                row['平均每笔'] = round(np.mean([r['平均'] for r in good]), 3) if good else 0
                row['分数'] = round(row['平均每天'] * row['平均每笔'], 3) if ok else 0
                out.append(row)
        print(uni, 'done', flush=True)
    S = pd.DataFrame(out).sort_values(['过关', '分数'], ascending=False)
    S.to_csv(HERE + f'/lab_结果{"" if _P == "L" else "_" + _P}.csv', index=False)
    pd.set_option('display.width', 400); pd.set_option('display.max_colwidth', 120)
    for side in 'LS':
        s = S[S.规则.str.startswith(side) & S.过关]
        print(f"\n===== {'做多' if side == 'L' else '做空'}：过关 {len(s)} 个")
        print(s.head(25)[['范围', '规则', '出场', '平均每天', '平均每笔', '最差PF', '2022-23', '2024-25', '2025-26']].to_string(index=False))
