"""多头摊平做空 调试第六轮：在候选（多头摊平 + 低点附近 + 全市场散户偏多，止损 10%）上，每次加一个这个币自己的订单流条件。
c：none 不加 / ls 这个币散户偏多（多空人数比 7 天 z > 0）/ tls 这个币大户偏空（大户多空比 7 天 z < 0）/
   sf 这个币现货 24 小时在卖 / prem 合约比现货贵（溢价 7 天 z > 0）/ moi 全市场持仓量 24 小时也在涨
o：持仓量 24 小时涨超 3% / 5% / 8%；hold：6 / 12 / 24 小时。"""
import numpy as np, pandas as pd
import sim
from s26_trap_ofregime import prep as _p26
NAME = '多头摊平做空 调试第六轮（候选上加单币订单流条件）'
RULES = __doc__
GRID = [{'c': c, 'o': o, 'hold': h} for c in ('none', 'ls', 'tls', 'sf', 'prem', 'moi') for o in (0.03, 0.05, 0.08) for h in (72, 144, 288)]


def prep(df):
    df = _p26(df)
    z = lambda s: (s - s.rolling(288 * 7, min_periods=288).mean()) / s.rolling(288 * 7, min_periods=288).std()
    df['ls_z'] = z(df.ls)
    df['tls_z'] = z(df.tls)
    return df


def trades(df, p):
    m = (df.r24h < -0.05) & (df.oi24h > p['o']) & (df.fund > 0) & (df.c <= df.lo24 * 1.02) & (df.m_ls > 0)
    c = p['c']
    if c == 'ls':
        m &= df.ls_z > 0
    elif c == 'tls':
        m &= df.tls_z < 0
    elif c == 'sf':
        m &= df.sf24h < 0
    elif c == 'prem':
        m &= df.prem_z > 0
    elif c == 'moi':
        m &= df.m_oi > 0
    sig = np.flatnonzero(m.fillna(False).values)
    return sim.run(df, sig, -1, 0.10, hold=p['hold'], entry='market')
