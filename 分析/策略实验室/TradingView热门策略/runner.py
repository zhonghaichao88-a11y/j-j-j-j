"""跑 TradingView 移植策略：币安 2024-01 ~ 2026-09，5 分钟数据合成各周期；每个币各自模拟，汇总后按 训练2024 / 考试2025+ / 最近6个月 / 新币 看"""
import os, sys, json, time, inspect, numpy as np, pandas as pd
LAB = '/home/user/j-j-j-j/orderflow/分析/策略实验室'
sys.path.insert(0, LAB); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data as D, report as R, tvsim
CACHE = '/home/user/ext/tv/cache'


def frame(coin, tf):
    f = f'{CACHE}/{tf}/{coin}.parquet'
    if os.path.exists(f):
        return pd.read_parquet(f)
    k = pd.read_parquet(f'{D.ROOT}/k/{coin}.parquet').sort_values('ts').drop_duplicates('ts')
    k = k.set_index(pd.to_datetime(k.ts, unit='ms'))
    df = pd.DataFrame({'o': k.open, 'h': k.high, 'l': k.low, 'c': k.close, 'v': k.volume}).astype(float)
    if tf != 5:
        df = df.resample(f'{tf}min', label='left', closed='left').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last', 'v': 'sum'}).dropna()
    df.index = df.index.values.astype('datetime64[ms]').astype(np.int64)
    if tf != 5:                                  # 5 分钟直接读原始数据，不另存（省硬盘）
        os.makedirs(f'{CACHE}/{tf}', exist_ok=True)
        df.to_parquet(f)
    return df


def run_one(name, fn, tf, coins=None, **kw):
    rows = []
    for c in coins or D.coins():
        df = frame(c, tf)
        if len(df) < 500:
            continue
        kw2 = dict(kw, coin=c) if 'coin' in inspect.signature(fn).parameters else kw
        sig = fn(df.reset_index(names='t'), **kw2)      # 指标统一用 0..n-1 行号，时间在 t 列
        r = tvsim.run(df, **sig)
        if len(r):
            t = df.index.values
            T = pd.DataFrame({'coin': c, 't_in': t[r[:, 0].astype(int)], 't_out': t[r[:, 1].astype(int)],
                              'side': r[:, 2], 'ret': r[:, 3], 'why': r[:, 4], 'bars': r[:, 5]})
            T['t'] = T.t_in
            T = T[np.isfinite(T.ret)]
            rows.append(T)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=['coin', 't', 't_in', 't_out', 'side', 'ret', 'why', 'bars'])


def summary(name, T, tf):
    sp = R.split(T)
    try:
        pf = R.portfolio(T.assign(ret=T.ret.clip(lower=-1.0))) if len(T) else {}     # 1 倍仓位最多亏光这一笔
    except Exception as ex:
        pf = {'错误': str(ex)}
    hold_h = float(np.median(T.bars) * tf / 60) if len(T) else 0
    return {'策略': name, '周期分钟': tf, '笔数': len(T), '每天约': round(len(T) / 1004, 1), '持仓中位小时': round(hold_h, 1),
            '全部': sp['全部'], '训练2024': sp['训练2024'], '考试2025+': sp['考试2025+'], '最近6个月': sp['最近6个月'],
            '新币': sp['新币'], '过关': bool(R.passed(sp)), '组合100U': pf}
