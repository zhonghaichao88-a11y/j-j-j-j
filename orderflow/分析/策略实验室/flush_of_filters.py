"""清洗接盘：不用大盘过滤，改用订单流过滤。
事先定的规则（看结果前写好）：
- 候选过滤全部是订单流数据：24h 持仓量变化、24h 合约主动买卖、24h 现货主动买卖、资金费率、散户多空人数比 7 天 z、大户多空持仓比 7 天 z、
  合约对现货溢价 7 天 z、全市场（BTC）24h 持仓量变化、全市场（BTC）24h 合约主动买卖。
- 每个过滤只用 2024 年（训练）的单子定门槛：按训练期分三档，去掉训练期最差的那一档（保留 2/3）。
- 训练期最好档和最差档每笔差距要 ≥ 50 基点才算"有区分度"，否则不用。
- 然后只看 2025 年以后、最近 6 个月、新币，和"大盘过滤"、"不过滤"比。
- 组合：训练期区分度最大的 2 个过滤一起用。"""
import sys, numpy as np, pandas as pd, json
from multiprocessing import Pool
import data, sim, report
from data import flow, TRAIN_END
import s00_flush as S
from s16_bearshort import bear_days

FEATS = {'oi24h': '持仓量 24 小时变化', 'pf24h': '合约主动买卖 24 小时', 'sf24h': '现货主动买卖 24 小时', 'fund_rate': '资金费率',
         'ls_z': '散户多空人数比(7天z)', 'tls_z': '大户多空持仓比(7天z)', 'prem_z': '合约对现货溢价(7天z)',
         'btc_oi24h': 'BTC 持仓量 24 小时变化', 'btc_pf24h': 'BTC 合约主动买卖 24 小时'}
_B = None


def btc_feats():
    global _B
    if _B is None:
        b = data.load('BTC')
        _B = pd.DataFrame({'btc_oi24h': b.oi.pct_change(288), 'btc_pf24h': flow(b.bq, b.qv, 288)})
    return _B


def one(c):
    df = S.prep(data.load(c))
    tr = S.trades(df, S.GRID[0])
    if not tr:
        return []
    z = lambda s: (s - s.rolling(288 * 7, min_periods=288).mean()) / s.rolling(288 * 7, min_periods=288).std()
    f = pd.DataFrame({'oi24h': df.oi.pct_change(288), 'pf24h': flow(df.bq, df.qv, 288), 'sf24h': flow(df.sbq, df.sqv, 288),
                      'fund_rate': df.fund, 'ls_z': z(df.ls), 'tls_z': z(df.tls), 'prem_z': z(df.c / df.sc - 1)}, index=df.index)
    f = f.join(btc_feats(), how='left')
    T = pd.DataFrame(tr, columns=sim.COLS)
    return T.join(f, on='t').to_dict('records')


if __name__ == '__main__':
    btc_feats()
    with Pool(4) as p:
        rows = sum(p.map(one, data.coins()), [])
    T = pd.DataFrame(rows)
    T['bull'] = ~(T.t // 86_400_000).map(bear_days()).fillna(False).astype(bool)
    T.to_parquet('/home/user/ext/long/lab/results/flush_of_feats.parquet')
    tr = T.t < TRAIN_END
    def st(X):
        sp = report.split(X)
        g = lambda k: sp[k]
        return {'全部': g('全部'), '训练2024': g('训练2024'), '考试2025+': g('考试2025+'), '最近6个月': g('最近6个月'), '新币': g('新币'),
                '100U': report.portfolio(X), '过关': report.passed(sp)}
    out = {'不过滤': st(T), '大盘过滤(BTC 200天线)': st(T[T.bull])}
    keep_rules, spread = {}, {}
    for k, nm in FEATS.items():
        s = T.loc[tr, k].dropna()
        if len(s) < 100:
            continue
        q1, q2 = s.quantile([1 / 3, 2 / 3])
        band = lambda v: np.where(v <= q1, 0, np.where(v <= q2, 1, 2))
        b = pd.Series(band(T[k]), index=T.index).where(T[k].notna())
        m = T[tr].groupby(b[tr]).ret.mean() * 1e4
        worst = int(m.idxmin()); spread[k] = float(m.max() - m.min())
        keep = b.notna() & (b != worst)
        keep_rules[k] = keep
        out[f'{nm}：去掉训练期最差档'] = dict(st(T[keep]), 训练档每笔=[round(m.get(i, np.nan), 1) for i in range(3)], 去掉=['低', '中', '高'][worst],
                                         门槛=[round(float(q1), 5), round(float(q2), 5)], 区分度ok=spread[k] >= 50)
    good = sorted([k for k in spread if spread[k] >= 50], key=lambda k: -spread[k])[:2]
    if len(good) == 2:
        out[f'组合：{FEATS[good[0]]} + {FEATS[good[1]]}'] = st(T[keep_rules[good[0]] & keep_rules[good[1]]])
    json.dump(out, open('/home/user/ext/long/lab/results/flush_of_filters.json', 'w'), ensure_ascii=False, indent=1, default=str)
    for k, v in out.items():
        te, rc, nw = v['考试2025+'], v['最近6个月'], v['新币']
        print(f"{k:40s} 训练PF {v['训练2024'].get('PF')} | 考试 {te.get('笔数')}笔 PF {te.get('PF')} 每笔{te.get('每笔基点')} | 近6月 {rc.get('笔数')}笔 PF {rc.get('PF')} | 新币PF {nw.get('PF')} | 100U {v['100U'].get('27个月后')} 回撤{v['100U'].get('最大回撤%')} | {'过关' if v['过关'] else ''} {v.get('去掉','')} {v.get('训练档每笔','')} {'' if v.get('区分度ok', True) else '(区分度不够)'}")
