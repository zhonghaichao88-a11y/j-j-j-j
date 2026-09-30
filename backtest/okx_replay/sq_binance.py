"""压缩释放突破 + 币安官方免费数据（data.binance.vision，U本位合约）：
每笔交易开仓前一刻的 持仓量变化(4h/24h)、主动买卖比、大户/全体多空比、盘口深度(±1%/±2%)买卖失衡、资金费率。
方向已按开仓方向调整（>0 表示对这笔交易有利/同向）。输出 sq_bn.csv；再按各批币评估过滤。"""
import io, os, json, zipfile, urllib.request, ssl
import numpy as np, pandas as pd
from concurrent.futures import ThreadPoolExecutor
H = 3600000; D = 86400000
ctx = ssl.create_default_context(cafile='/root/.ccr/ca-bundle.crt')
opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https': os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')}),
                                     urllib.request.HTTPSHandler(context=ctx))
CACHE = 'bn_cache'; os.makedirs(CACHE, exist_ok=True)


def get(url):
    fn = os.path.join(CACHE, url.split('/')[-1].replace('.zip', '.csv'))
    if os.path.exists(fn): return fn if os.path.getsize(fn) else None
    for _ in range(3):
        try:
            data = opener.open(url, timeout=60).read(); z = zipfile.ZipFile(io.BytesIO(data))
            open(fn, 'wb').write(z.read(z.namelist()[0])); return fn
        except urllib.error.HTTPError as e:
            if e.code == 404: open(fn, 'w').close(); return None
        except Exception:
            pass
    return None


def sym_for(inst):
    base = inst.split('-')[0]
    for s in (base + 'USDT', '1000' + base + 'USDT'):
        if get(f'https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-2026-08.zip'): return s
    return None


def day_str(ms): return pd.Timestamp(ms, unit='ms').strftime('%Y-%m-%d')


def metrics(s, ms):
    rows = []
    for dd in {day_str(ms - D), day_str(ms)}:
        fn = get(f'https://data.binance.vision/data/futures/um/daily/metrics/{s}/{s}-metrics-{dd}.zip')
        if fn: rows.append(pd.read_csv(fn))
    if not rows: return None
    m = pd.concat(rows); m['t'] = (pd.to_datetime(m.create_time) - pd.Timestamp(0)) // pd.Timedelta('1ms')
    return m[m.t < ms].sort_values('t')


def depth(s, ms):
    fn = get(f'https://data.binance.vision/data/futures/um/daily/bookDepth/{s}/{s}-bookDepth-{day_str(ms)}.zip')
    if not fn: return None
    b = pd.read_csv(fn); b['t'] = (pd.to_datetime(b.timestamp) - pd.Timestamp(0)) // pd.Timedelta('1ms')
    b = b[(b.t < ms) & (b.t >= ms - 10 * 60000)]
    if b.empty: return None
    last = b[b.t == b.t.max()].set_index('percentage').notional
    out = {}
    for p in (1, 2):
        bid, ask = last.get(-float(p)), last.get(float(p))
        if bid is not None and ask is not None and bid + ask > 0: out[f'dep{p}'] = (bid - ask) / (bid + ask)
    return out


FUND = {}


def funding(s, ms):
    mon = pd.Timestamp(ms, unit='ms').strftime('%Y-%m'); key = (s, mon)
    if key not in FUND:
        fn = get(f'https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{mon}.zip')
        FUND[key] = pd.read_csv(fn) if fn else None
    f = FUND[key]
    if f is None or f.empty: return None
    tcol = [c for c in f.columns if 'time' in c.lower()][0]; rcol = [c for c in f.columns if 'rate' in c.lower() and 'funding' in c.lower()][0]
    f = f[f[tcol] < ms]
    return float(f.sort_values(tcol)[rcol].iloc[-1]) if len(f) else None


T = pd.read_csv('sq_flow.csv')
SYM = {}
for inst in T.inst.unique(): SYM[inst] = sym_for(inst)
print('币安有合约的币', sum(v is not None for v in SYM.values()), '/', len(SYM), flush=True)


def one(row):
    s = SYM.get(row['inst']); ms = int(row['opened']) - H + H  # 开仓时刻 = 突破K收盘
    if not s: return None
    out = {}
    m = metrics(s, ms)
    if m is not None and len(m) > 10:
        oi = m.sum_open_interest_value.values; t = m.t.values
        def at(dt):
            i = np.searchsorted(t, ms - dt) - 1
            return oi[i] if i >= 0 else np.nan
        out['oi4'] = oi[-1] / at(4 * H) - 1; out['oi24'] = oi[-1] / at(24 * H) - 1
        last1h = m[m.t >= ms - H]
        out['taker'] = np.log(last1h.sum_taker_long_short_vol_ratio.mean()) if len(last1h) else np.nan
        out['top_ls'] = np.log(m.count_toptrader_long_short_ratio.iloc[-1]); out['all_ls'] = np.log(m.count_long_short_ratio.iloc[-1])
    dp = depth(s, ms)
    if dp: out.update(dp)
    fr = funding(s, ms)
    if fr is not None: out['fund'] = fr
    return out


with ThreadPoolExecutor(8) as ex: res = list(ex.map(one, [r for _, r in T.iterrows()]))
rows = []
for (_, r), x in zip(T.iterrows(), res):
    if not x: continue
    d = r.d; y = r.to_dict()
    for k in ('taker', 'dep1', 'dep2'): y[k] = x.get(k, np.nan) * d        # 同向为正
    y['oi4'] = x.get('oi4', np.nan); y['oi24'] = x.get('oi24', np.nan)
    y['top_ls'] = x.get('top_ls', np.nan) * d; y['all_ls'] = x.get('all_ls', np.nan) * d   # >0：多空比偏向本方向（拥挤）
    y['fund'] = x.get('fund', np.nan) * d                                               # >0：本方向在付费（拥挤）
    rows.append(y)
B = pd.DataFrame(rows); B.to_csv('sq_bn.csv', index=False)
print('有币安数据的交易', len(B), '/', len(T), flush=True)
FILT = {'不过滤': lambda d: d.r == d.r, '持仓量4h增加': lambda d: d.oi4 > 0, '持仓量4h减少': lambda d: d.oi4 < 0,
        '持仓量24h增加': lambda d: d.oi24 > 0, '币安主动买卖同向': lambda d: d.taker > 0,
        '盘口±1%同向(本方向挂单多)': lambda d: d.dep1 > 0, '盘口±2%同向': lambda d: d.dep2 > 0,
        '散户多空比不拥挤(全体)': lambda d: d.all_ls < 0, '大户多空比同向': lambda d: d.top_ls > 0,
        '资金费率不拥挤(本方向收钱)': lambda d: d.fund <= 0,
        'OKX Delta>5%': lambda d: d.delta > .05, 'Delta>5%+持仓量4h增加': lambda d: (d.delta > .05) & (d.oi4 > 0),
        'Delta>5%+币安主动同向': lambda d: (d.delta > .05) & (d.taker > 0)}
out = []
for name, fn in FILT.items():
    row = dict(过滤=name)
    for g, lbl in (('old40', '老40'), ('new30', '新30'), ('rest34', '另34'), ('more', '更多币'), (None, '全部')):
        G = B if g is None else B[B.grp == g]
        if G.empty: continue
        x = G[fn(G).fillna(False).values]; rr = x.r.clip(-3, 50)
        row[lbl] = f'{len(x)}笔 {rr.mean():+.3f}' if len(x) else '0笔'
    out.append(row)
pd.set_option('display.width', 300)
print(pd.DataFrame(out).to_string(index=False))
