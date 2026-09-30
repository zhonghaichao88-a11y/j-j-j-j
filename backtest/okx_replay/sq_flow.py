"""压缩释放突破 + 真实订单流：下载每笔交易开仓当天的 OKX 官方逐笔成交（按北京时间分天），
算突破K线（开仓前那根1小时K）的主动买/卖成交额、Delta 比例，以及前3根的 Delta 比例。
结果写入 sq_flow.csv（每笔交易一行）。原始文件算完即删。"""
import io, json, os, zipfile, urllib.request, ssl
import numpy as np, pandas as pd
from concurrent.futures import ThreadPoolExecutor
H = 3600000; BJ = 8 * H; D = 86400000
T = pd.read_csv('sq_trades_feats.csv')
ctx = ssl.create_default_context(cafile='/root/.ccr/ca-bundle.crt')
proxy = urllib.request.ProxyHandler({'https': os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')})
opener = urllib.request.build_opener(proxy, urllib.request.HTTPSHandler(context=ctx))


def day_of(ms): return int((ms + BJ) // D)


def fetch(inst, day):
    ds = pd.Timestamp(day * D, unit='ms').strftime('%Y%m%d'); dd = pd.Timestamp(day * D, unit='ms').strftime('%Y-%m-%d')
    url = f'https://static.okx.com/cdn/okex/traderecords/trades/daily/{ds}/{inst}-trades-{dd}.zip'
    for _ in range(3):
        try:
            data = opener.open(url, timeout=120).read(); break
        except Exception as exc:
            err = exc
    else:
        return None
    z = zipfile.ZipFile(io.BytesIO(data)); n = z.namelist()[0]
    df = pd.read_csv(z.open(n), usecols=['side', 'price', 'size', 'created_time'])
    df['q'] = df.price * df['size']          # 按张数×价格（相对值足够算 Delta 比例）
    return df


def bars(df, starts):
    out = {}
    t = df.created_time.values; buy = (df.side.values == 'buy'); q = df.q.values
    for s in starts:
        m = (t >= s) & (t < s + H)
        b, se = q[m & buy].sum(), q[m & ~buy].sum()
        out[s] = (b, se)
    return out


# 需要的K线：突破K（开仓时间前1小时）+ 前3根
need = {}
for _, r in T.iterrows():
    for k in range(4):
        s = int(r.opened) - H * (k + 1)
        need.setdefault((r.inst, day_of(s)), set()).add(s)
cache_fn = 'sq_flow_bars.json'
done = json.load(open(cache_fn)) if os.path.exists(cache_fn) else {}


def job(key):
    inst, day = key; k = f'{inst}|{day}'
    if k in done: return k, done[k]
    df = fetch(inst, day)
    if df is None: return k, None
    res = {str(s): v for s, v in bars(df, sorted(need[key])).items()}
    return k, res


keys = sorted(need)
print('需要下载', len(keys), '个文件', flush=True)
with ThreadPoolExecutor(6) as ex:
    for i, (k, v) in enumerate(ex.map(job, keys)):
        done[k] = v
        if i % 20 == 0:
            json.dump(done, open(cache_fn, 'w')); print(i, k, 'ok' if v else '缺', flush=True)
json.dump(done, open(cache_fn, 'w'))
rows = []
for _, r in T.iterrows():
    vals = []
    for k in range(4):
        s = int(r.opened) - H * (k + 1); v = (done.get(f'{r.inst}|{day_of(s)}') or {}).get(str(s))
        vals.append(v)
    if vals[0] is None or sum(vals[0]) <= 0: continue
    b, s = vals[0]; dr = (b - s) / (b + s)
    prev = [((x[0] - x[1]) / (x[0] + x[1])) if x and sum(x) > 0 else np.nan for x in vals[1:]]
    rows.append(dict(**r.to_dict(), delta=dr * r.d, delta_prev=np.nanmean(prev) * r.d, flow_usd=b + s))
pd.DataFrame(rows).to_csv('sq_flow.csv', index=False)
print('DONE', len(rows), flush=True)
