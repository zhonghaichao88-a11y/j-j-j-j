"""再下 20 个币的欧易逐笔成交（2026-04-01 ~ 2026-09-30），压成 1 分钟足迹；持仓量/多空比/资金费率用币安归档（已有）。"""
import sys, json, subprocess, datetime as dt, numpy as np, pandas as pd
sys.path.insert(0, '/home/user/j-j-j-j/orderflow')
import fetch_fp as F
from concurrent.futures import ThreadPoolExecutor
COINS = ['DOGE', 'XRP', 'ADA', 'AVAX', 'LINK', 'LTC', 'BCH', 'DOT', 'SUI', 'OP', 'ARB', 'APT', 'NEAR', 'FIL', 'AAVE', 'UNI', 'TRX', 'WLD', 'INJ', 'ETC']
ins = json.loads(subprocess.check_output(['curl', '-s', 'https://www.okx.com/api/v5/public/instruments?instType=SWAP']))['data']
ins = {d['instId']: d for d in ins}
tk = {d['instId']: float(d['last']) for d in json.loads(subprocess.check_output(['curl', '-s', 'https://www.okx.com/api/v5/market/tickers?instType=SWAP']))['data']}
F.OUT = '/home/user/ext/of/fp2'
for c in COINS:
    d = ins[f'{c}-USDT-SWAP']
    assert d.get('instCategory') == '1'
    ts = float(d['tickSz']); F.CFG[c] = (float(d['ctVal']), max(ts, round(tk[f'{c}-USDT-SWAP'] * 0.0003 / ts) * ts))   # 价位格子 ≈ 价格的 0.03%（和 SOL 差不多）
    F.BIG_USD[c] = 25_000
    # 币安归档的持仓量 / 多空比 / 资金费率 → 和 BTC_metrics.csv 一样的格式
    m = pd.read_parquet(f'/home/user/ext/long/met/{c}.parquet')
    m['t'] = (pd.to_datetime(m.create_time) - pd.Timestamp('1970-01-01')) // pd.Timedelta(milliseconds=1)
    m = m[m.t >= 1772323200000]
    pd.DataFrame({'t': m.t, 'oi': m['sum_open_interest'] if 'sum_open_interest' in m else m['sum_open_interest_value'], 'top_ls_acc': m.get('count_toptrader_long_short_ratio'), 'top_ls_pos': m.get('sum_toptrader_long_short_ratio'),
                  'ls': m.get('count_long_short_ratio'), 'taker_ratio': m.get('sum_taker_long_short_vol_ratio')}).to_csv(f'{F.OUT}/{c}_metrics.csv', index=False)
    f = pd.read_parquet(f'/home/user/ext/long/met/{c}_funding.parquet')
    f = f[f.calc_time >= 1772323200000]
    pd.DataFrame({'t': f.calc_time, 'funding': f.last_funding_rate}).to_csv(f'{F.OUT}/{c}_funding.csv', index=False)
d0 = dt.date(2026, 4, 1)
jobs = [(c, d0 + dt.timedelta(days=i)) for c in COINS for i in range(183)]
with ThreadPoolExecutor(6) as ex:
    for (s, d), r in zip(jobs, ex.map(lambda a: F.one(*a), jobs)):
        if not str(r).startswith('ok') and r != 'skip':
            print(s, d, r, flush=True)
print('ALL DONE', flush=True)
