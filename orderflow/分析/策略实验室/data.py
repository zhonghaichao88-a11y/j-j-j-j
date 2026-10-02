"""统一数据入口：一个币一次读进来，5 分钟一行。只用当时及以前的数据算东西由各打法自己负责。
列：o h l c qv(合约成交额) bq(合约主动买成交额) | sqv sbq sc(币安现货) | oi(持仓价值) ls(多空人数比) tls(大户持仓多空比) | fund(当时最近一次资金费率)
attrs: name, mult(1000PEPE 这类价格倍数), new(是不是后加的币), fts/frate(资金费率结算时间和费率)"""
import os, numpy as np, pandas as pd
ROOT = '/home/user/ext/long'
SYMS = dict(l.split() for l in open(f'{ROOT}/syms.txt'))
NEW = set(l.split()[0] for l in open(f'{ROOT}/syms_new.txt'))
TRAIN_END = 1735689600000            # 2025-01-01：之前是训练，之后是考试


import json as _json
_CAT = _json.load(open(os.path.join(os.path.dirname(__file__), 'okx_category.json'))) if os.path.exists(
    os.path.join(os.path.dirname(__file__), 'okx_category.json')) else {}


def coins():
    """只要加密币（欧易 instCategory=1）；股票、黄金白银合约不测（程序里也不做）"""
    return sorted(c for c in SYMS if os.path.exists(f'{ROOT}/k/{c}.parquet') and _CAT.get(c, '1') == '1')


def _mult(sym):
    for p, m in (('1000000', 1_000_000), ('1000', 1000)):
        if sym.startswith(p):
            return m
    return 1


def load(c):
    k = pd.read_parquet(f'{ROOT}/k/{c}.parquet').sort_values('ts').drop_duplicates('ts').set_index('ts')
    df = pd.DataFrame({'o': k.open, 'h': k.high, 'l': k.low, 'c': k.close, 'qv': k.quote_volume, 'bq': k.taker_buy_quote_volume})
    sp = f'{ROOT}/spot/{c}.parquet'
    if os.path.exists(sp):
        s = pd.read_parquet(sp).sort_values('ts').drop_duplicates('ts').set_index('ts').reindex(df.index)
        df['sqv'], df['sbq'], df['sc'] = s.quote_volume, s.taker_buy_quote_volume, s.close
    else:
        df['sqv'] = df['sbq'] = df['sc'] = np.nan
    mp = f'{ROOT}/met/{c}.parquet'
    if os.path.exists(mp):
        m = pd.read_parquet(mp)
        m['ts'] = (pd.to_datetime(m.create_time) - pd.Timestamp('1970-01-01')) // pd.Timedelta(milliseconds=1)
        m = m.sort_values('ts').drop_duplicates('ts').set_index('ts')
        oi = m.sum_open_interest_value.where(m.sum_open_interest_value > 0)
        df['oi'] = oi.reindex(df.index, method='ffill', tolerance=600_000)
        df['ls'] = m.count_long_short_ratio.reindex(df.index, method='ffill', tolerance=600_000) if 'count_long_short_ratio' in m else np.nan
        df['tls'] = m.sum_toptrader_long_short_ratio.reindex(df.index, method='ffill', tolerance=600_000) if 'sum_toptrader_long_short_ratio' in m else np.nan
    else:
        df['oi'] = df['ls'] = df['tls'] = np.nan
    fp = f'{ROOT}/met/{c}_funding.parquet'
    fts = frate = np.array([])
    if os.path.exists(fp):
        f = pd.read_parquet(fp).sort_values('calc_time').drop_duplicates('calc_time')
        fts, frate = f.calc_time.values.astype(np.int64), f.last_funding_rate.values.astype(float)
        df['fund'] = pd.Series(frate, index=fts).reindex(df.index, method='ffill')
    else:
        df['fund'] = np.nan
    df.attrs.update(name=c, mult=_mult(SYMS.get(c, '')), new=c in NEW, fts=fts, frate=frate)
    return df


def flow(b, q, n):
    """主动买卖差 / 成交额，n 根滚动"""
    return (2 * b - q).rolling(n).sum() / q.rolling(n).sum()
