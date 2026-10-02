"""汇总：训练（2024）/ 考试（2025-01 以后）、原来 46 个币 / 后加的币、按年、组合模拟（每笔 10%，最多同时 10 单，滚利）"""
import numpy as np, pandas as pd
from data import TRAIN_END, NEW


def pf(r):
    r = np.asarray(r)
    neg = -r[r < 0].sum()
    return float(r[r > 0].sum() / neg) if neg > 0 else float('inf')


def stats(T):
    if len(T) == 0:
        return {'笔数': 0}
    r = T.ret.values
    y = T.groupby(pd.to_datetime(T.t, unit='ms').dt.year).ret.mean() * 1e4
    return {'笔数': len(r), '胜率%': round((r > 0).mean() * 100), '每笔基点': round(r.mean() * 1e4, 1),
            '中位数': round(np.median(r) * 1e4, 1), 'PF': round(pf(r), 2), '各年': {int(k): int(v) for k, v in y.items()}}


def split(T):
    tr, te = T[T.t < TRAIN_END], T[T.t >= TRAIN_END]
    old, new = T[~T.coin.isin(NEW)], T[T.coin.isin(NEW)]
    return {'全部': stats(T), '训练2024': stats(tr), '考试2025+': stats(te), '原46币': stats(old), '新币': stats(new)}


def portfolio(T, size=0.10, cap=10, start=100.0):
    if len(T) == 0:
        return {}
    rows = T.sort_values('t_in')
    ev = sorted([(a, 0, k) for k, a in enumerate(rows.t_in.values)] + [(b, 1, k) for k, b in enumerate(rows.t_out.values)])
    R = rows.ret.values
    eq, pos, curve = start, {}, []
    for t, typ, k in ev:
        if typ == 0:
            if len(pos) < cap:
                pos[k] = eq * size
        elif k in pos:
            eq += pos.pop(k) * R[k]
            curve.append((t, eq))
    if not curve:
        return {}
    c = pd.Series([e for _, e in curve], index=pd.to_datetime([t for t, _ in curve], unit='ms'))
    m = c.resample('ME').last().pct_change()
    yrs = max((c.index[-1] - c.index[0]).days / 365, 0.1)
    return {'27个月后': round(c.iloc[-1]), '年化%': round(((c.iloc[-1] / start) ** (1 / yrs) - 1) * 100),
            '最大回撤%': round((c / c.cummax() - 1).min() * 100), '亏钱月': f'{int((m < 0).sum())}/{int(m.notna().sum())}'}


def passed(sp):
    """过关标准（事先定好）：考试期 PF ≥ 1.15、每笔为正、笔数 ≥ 50；新币 PF ≥ 1.1；训练期也为正"""
    te, nw, tr = sp['考试2025+'], sp['新币'], sp['训练2024']
    new_ok = nw.get('PF', 0) >= 1.1 if nw.get('笔数', 0) >= 30 else True      # 只做 BTC/ETH 的打法没有新币，不看这条
    return (te.get('笔数', 0) >= 50 and te.get('PF', 0) >= 1.15 and te.get('每笔基点', -1) > 0
            and new_ok and tr.get('每笔基点', -1) > 0)
