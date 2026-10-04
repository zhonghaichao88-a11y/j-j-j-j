"""多头摊平做空 调试（第五轮，看结果前写好）：不用 BTC 均线，改用"全市场订单流"判断现在适不适合做空。
基础：24 小时跌超 d、持仓量 24 小时涨超 5%、资金费率 > 0；x=low 时再要求价格在 24 小时低点 2% 以内。
全市场状态 m（每小时，用所有币的中位数，只用已经收完的小时）：
  none 不加
  pf   全市场合约 24 小时主动卖多于买（中位数 < 0）
  oi   全市场持仓量 24 小时在涨（中位数 > 0）：大家都在加杠杆
  fund 全市场资金费率偏高（中位数 > 0.01%，多头拥挤）
  ls   全市场散户多空人数比偏多（7 天 z 分数中位数 > 0）
止损 sd，拿 hold 根 5 分钟K线。调参期 2024-01 ~ 2025-06，考试期 2025-07 ~ 2026-09（挑参数时不看）。"""
import numpy as np, pandas as pd
import sim
from s24_trap_tune import prep as _p24
NAME = '多头摊平做空 调试（全市场订单流状态代替大盘均线）'
RULES = __doc__
GRID = [{'d': d, 'x': x, 'm': m, 'sd': sd, 'hold': h} for d in (0.05, 0.08) for x in ('none', 'low')
        for m in ('none', 'pf', 'oi', 'fund', 'ls') for sd in (0.05, 0.10) for h in (144, 288)]
_M = None


def market():
    global _M
    if _M is None:
        F = pd.read_parquet('/home/user/ext/of_ind.parquet', columns=['t', 'perp_flow_24h', 'oi_24h', 'funding', 'retail_ls_z'])
        _M = F.groupby('t').median()
        _M.index = _M.index + 3_600_000           # 这一小时收完以后才能用
    return _M


def prep(df):
    df = _p24(df)
    M = market()
    T = df.index.values
    key = ((T + 300_000) // 3_600_000) * 3_600_000          # 这根 5 分钟K线收盘时，最近一个已经收完的小时
    m = M.reindex(key)
    df['m_pf'] = m.perp_flow_24h.values
    df['m_oi'] = m.oi_24h.values
    df['m_fund'] = m.funding.values
    df['m_ls'] = m.retail_ls_z.values
    return df


def trades(df, p):
    m = (df.r24h < -p['d']) & (df.oi24h > 0.05) & (df.fund > 0)
    if p['x'] == 'low':
        m &= df.c <= df.lo24 * 1.02
    k = p['m']
    if k == 'pf':
        m &= df.m_pf < 0
    elif k == 'oi':
        m &= df.m_oi > 0
    elif k == 'fund':
        m &= df.m_fund > 0.0001
    elif k == 'ls':
        m &= df.m_ls > 0
    sig = np.flatnonzero(m.fillna(False).values)
    return sim.run(df, sig, -1, p['sd'], hold=p['hold'], entry='market')
