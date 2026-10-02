"""组合规则检验（2024-01 ~ 2026-03，46 个币，5 分钟）。
规则和阈值在看结果之前全部定好（固定数值，不用全样本分位数，不偷看未来）。
同一个币 4 小时内只算一次；按季度看稳定性；扣吃单成本 0.12% 和挂单成本 0.04% 各算一遍。
过关标准（事先定好）：扣吃单成本后平均为正，9 个季度里至少 6 个为正，2024 和 2025~26 两段都为正。"""
import pandas as pd, numpy as np, sys

F = pd.read_parquet('/home/user/ext/long/F2.parquet').reset_index()
F['year'] = np.where(F.ts < 1735689600000, '2024', '2025-26')
TAKER, MAKER = 0.0012, 0.0004


def g(c):
    return F[c] if c in F else pd.Series(np.nan, index=F.index)


RULES = {
    # 名字: (条件, 方向)
    '杠杆推涨（现货没跟、费率高）→ 空': ((g('r_60') > .02) & (g('oi_60') > .03) & (g('div_60') < -.05) & (g('fund_z') > 1), -1),
    '杠杆砸盘（现货没跟、费率低）→ 多': ((g('r_60') < -.02) & (g('oi_60') > .03) & (g('div_60') > .05) & (g('fund_z') < -1), 1),
    '多头清洗 + 现货接盘 → 多': ((g('r_60') < -.02) & (g('oi_60') < -.03) & (g('sf_60') > 0), 1),
    '空头挤压 + 现货在卖 → 空': ((g('r_60') > .02) & (g('oi_60') < -.03) & (g('sf_60') < 0), -1),
    '多头清洗（不看现货）→ 多': ((g('r_60') < -.02) & (g('oi_60') < -.03), 1),
    '现货领先买、价格没动 → 多': ((g('div_240') > .1) & (g('sf_240') > .05) & (g('r_240').abs() < .01), 1),
    '现货领先卖、价格没动 → 空': ((g('div_240') < -.1) & (g('sf_240') < -.05) & (g('r_240').abs() < .01), -1),
    'Coinbase 溢价走高 → 多': ((g('cb_prem_chg') > .0005) & (g('cb_prem') > 0), 1),
    'Coinbase 溢价走低 → 空': ((g('cb_prem_chg') < -.0005) & (g('cb_prem') < 0), -1),
    'BTC 涨了山寨没跟、BTC 主动买 → 多山寨': ((g('btc_r_60') > .015) & (g('r_60') < .003) & (g('btc_pf_60') > 0), 1),
    'BTC 跌了山寨没跟、BTC 主动卖 → 空山寨': ((g('btc_r_60') < -.015) & (g('r_60') > -.003) & (g('btc_pf_60') < 0), -1),
    '暴跌 + 日线上涨趋势 → 多': ((g('r_60') < -.03) & (g('ema_1d') > 0), 1),
    '暴跌 + 日线下跌趋势 → 多': ((g('r_60') < -.03) & (g('ema_1d') < 0), 1),
    '散户极度看多 → 空': ((g('ls_z') > 2), -1),
    '散户极度看空 → 多': ((g('ls_z') < -2), 1),
    '费率极高 + 合约主动卖 → 空': ((g('fund_z') > 2) & (g('pf_60') < 0), -1),
    '费率极低 + 合约主动买 → 多': ((g('fund_z') < -2) & (g('pf_60') > 0), 1),
    '高于昨日POC 1~3%、合约主动卖 → 空（回POC）': ((g('poc_dist') > .01) & (g('poc_dist') < .03) & (g('pf_60') < 0), -1),
    '低于昨日POC 1~3%、合约主动买 → 多（回POC）': ((g('poc_dist') < -.01) & (g('poc_dist') > -.03) & (g('pf_60') > 0), 1),
    '顺日线趋势 + 现货和合约都在买 → 多': ((g('ema_1d') > 0) & (g('ema_4h') > 0) & (g('sf_60') > .05) & (g('pf_60') > .05), 1),
    '顺日线趋势 + 现货和合约都在卖 → 空': ((g('ema_1d') < 0) & (g('ema_4h') < 0) & (g('sf_60') < -.05) & (g('pf_60') < -.05), -1),
}


def events(mask, gap=48):
    E = F[mask.fillna(False).values][['inst', 'ts', 'q', 'year', 'fwd_60', 'fwd_240']].sort_values(['inst', 'ts'])
    keep, last = [], {}
    for i, (inst, ts) in enumerate(zip(E.inst, E.ts)):
        if ts - last.get(inst, -10**15) >= gap * 300_000:
            keep.append(i)
            last[inst] = ts
    return E.iloc[keep]


rows = []
for name, (mask, side) in RULES.items():
    E = events(mask)
    for h in (60, 240):
        r = (E[f'fwd_{h}'] * side).dropna()
        if len(r) < 30:
            rows.append({'规则': name, '持有': h, '次数': len(r)})
            continue
        net = r - TAKER
        byq = net.groupby(E.loc[r.index, 'q']).mean()
        byy = net.groupby(E.loc[r.index, 'year']).mean()
        hours = E.loc[r.index, 'ts'] // 3_600_000
        ok = net.mean() > 0 and (byq > 0).sum() >= 6 and (byy > 0).all()
        rows.append({'规则': name, '持有': h, '次数': len(r), '不同小时': hours.nunique(),
                     '胜率%': round((r > 0).mean() * 100), '扣成本前基点': round(r.mean() * 1e4, 1),
                     '扣吃单后基点': round(net.mean() * 1e4, 1), '扣挂单后基点': round((r.mean() - MAKER) * 1e4, 1),
                     '赚钱季度': f'{(byq > 0).sum()}/{len(byq)}',
                     '2024': round(byy.get('2024', np.nan) * 1e4, 1), '2025-26': round(byy.get('2025-26', np.nan) * 1e4, 1),
                     '过关': '✔' if ok else ''})
R = pd.DataFrame(rows)
pd.set_option('display.width', 250)
pd.set_option('display.max_colwidth', 40)
print(R.to_string(index=False))
