"""清洗接盘补测更多币：币安 U 本位合约里还没测过的币，逐个下载（合约 5 分钟K线、持仓量/多空比、资金费率、现货 5 分钟K线，2021-12 ~ 2026-09），
用程序本身回放清洗接盘（默认设置），只留回放结果，原始数据删掉（硬盘不够）。
币安没有现货的币：程序实盘也出不了清洗接盘信号（要读币安现货主动买卖），记下来不回放。
结果：/home/user/ext/replay/flush/<币>.parquet；每个币的情况记在 /home/user/ext/more/状态.csv。"""
import os, sys, io, re, time, json, zipfile, datetime as dt, httpx, pandas as pd
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
M = '/home/user/ext/more'; S3 = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision'; DV = 'https://data.binance.vision'
P = os.environ.get('HTTPS_PROXY'); C = httpx.Client(timeout=60, proxy=P, limits=httpx.Limits(max_connections=60))
KC = ['ts', 'open', 'high', 'low', 'close', 'volume', 'ct', 'quote_volume', 'n', 'taker_buy_volume', 'taker_buy_quote_volume', 'ig']
STABLE = {'USDC', 'BUSD', 'TUSD', 'FDUSD', 'USDP', 'DAI', 'USDE', 'EUR', 'BTCDOM'}
CAT = json.load(open('/home/user/ext/long/lab/okx_category.json'))
MONTHS = [f'{y}-{m:02d}' for y in range(2021, 2027) for m in range(1, 13) if (2021, 12) <= (y, m) <= (2026, 9)]
STATUS = f'{M}/状态.csv'


def get(url):
    for k in range(5):
        try:
            r = C.get(url)
            return r if r.status_code == 200 else None
        except Exception:
            time.sleep(2 * (k + 1))


def listing(prefix, delim=True):
    out, marker = [], ''
    while True:
        r = get(f'{S3}?{"delimiter=/&" if delim else ""}prefix={prefix}&marker={marker}')
        if r is None: break
        x = r.text
        out += re.findall(r'<Prefix>([^<]*)</Prefix>', x)[1:] if delim else re.findall(r'<Key>([^<]*)</Key>', x)
        if '<IsTruncated>true' not in x: break
        marker = re.findall(r'<NextMarker>([^<]*)</NextMarker>', x)[0] if delim else out[-1]
    return out


def unzip_csv(r, names=None):
    z = zipfile.ZipFile(io.BytesIO(r.content)); raw = z.read(z.namelist()[0]).decode()
    if names:
        lines = [l for l in raw.splitlines() if l and l[0].isdigit()]
        return pd.read_csv(io.StringIO('\n'.join(lines)), header=None, names=names)
    return pd.read_csv(io.StringIO(raw))


def base_of(sym):
    b = sym[:-4]
    for p in ('1000000', '1000'):
        if b.startswith(p) and len(b) > len(p): return b[len(p):], b
    return b, b


def monthly(path_fmt, cols=KC):
    with ThreadPoolExecutor(12) as ex:
        rs = list(ex.map(lambda m: get(path_fmt.format(m=m)), MONTHS))
    parts = [unzip_csv(r, cols) for r in rs if r]
    return pd.concat(parts) if parts else None


def fetch(coin, sym):
    """下载一个币的原始数据到 more/；返回 (有没有持仓量, 有没有现货)"""
    name, _ = base_of(sym)
    if not os.path.exists(f'/home/user/ext/bnk/{coin}.parquet') and not os.path.exists(f'{M}/k/{coin}.parquet'):
        k = monthly(DV + f'/data/futures/um/monthly/klines/{sym}/5m/{sym}-5m-{{m}}.zip')
        if k is None: return False, False
        k.drop(columns=['ct', 'n', 'ig']).to_parquet(f'{M}/k/{coin}.parquet')
    keys = [x for x in listing(f'data/futures/um/daily/metrics/{sym}/', delim=False) if x.endswith('.zip')]
    keys = [x for x in keys if '2021-12-01' <= x[-14:-4] <= '2026-09-30']
    with ThreadPoolExecutor(24) as ex:
        parts = [p for p in ex.map(lambda x: (lambda r: unzip_csv(r) if r else None)(get(f'{DV}/{x}')), keys) if p is not None]
    if not parts: return False, False
    m = pd.concat(parts); keep = [x for x in ('create_time', 'sum_open_interest_value', 'count_long_short_ratio') if x in m]
    m[keep].to_parquet(f'{M}/met/{coin}.parquet')
    fr = monthly(DV + f'/data/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{{m}}.zip', None)
    if fr is not None: fr.to_parquet(f'{M}/met/{coin}_funding.parquet')
    sp = monthly(DV + f'/data/spot/monthly/klines/{name}USDT/5m/{name}USDT-5m-{{m}}.zip')
    if sp is None: return True, False
    sp = sp.drop(columns=['ct', 'n', 'ig']); sp.loc[sp.ts > 1e14, 'ts'] //= 1000          # 2025 年起现货时间戳是微秒
    sp.to_parquet(f'{M}/spot/{coin}.parquet')
    return True, True


def job(a):
    coin, sym = a
    import warnings; warnings.filterwarnings('ignore')
    import replay as R
    st = time.perf_counter(); row = {'coin': coin, 'sym': sym}
    try:
        oi, spot = fetch(coin, sym); row.update(oi=oi, spot=spot)
        if oi and spot:
            df = R.load(coin); ok = df.oi.notna().values
            row['oi_days'] = int(ok.sum() // 288); row['start'] = str(pd.to_datetime(df.index[ok.argmax()], unit='ms').date()) if ok.any() else ''
            if ok.sum() >= 288 * 30:
                T = df.index.values; t0, t1 = int(T[ok.argmax()]), int(T[len(ok) - 1 - ok[::-1].argmax()]) + 300_000
                sigs, tr, lg, app = R.replay(coin, {"enabled": ["flush_spot"]}, t0=t0, t1=t1, feats=R.features(df), df=df, regime=R.REG)
                tr = tr[tr.k == 'flush_spot'] if len(tr) else tr
                pd.DataFrame([{'t': d['t'], 'skip': d.get('skip') or '', 'traded': bool(d.get('traded')), 'price': d['price']} for d in sigs if d['kind'] == 'flush_spot']).to_parquet(f'/home/user/ext/replay/flush/{coin}.sig.parquet')
                (tr.drop(columns=['kind']) if len(tr) else pd.DataFrame()).to_parquet(f'/home/user/ext/replay/flush/{coin}.parquet')
                row['trades'] = len(tr)
    except Exception as e:
        row['err'] = repr(e)[:200]
    finally:
        for f in (f'{M}/k/{coin}.parquet', f'{M}/met/{coin}.parquet', f'{M}/met/{coin}_funding.parquet', f'{M}/spot/{coin}.parquet'):
            if os.path.exists(f): os.remove(f)
    row['secs'] = round(time.perf_counter() - st)
    return row


def init():
    import replay as R
    R.REG = R.btc_regime()


if __name__ == '__main__':
    syms = [p.rstrip('/').split('/')[-1] for p in listing('data/futures/um/daily/metrics/')]
    syms = [s for s in syms if s.endswith('USDT') and not s[:-4].isdigit()]
    done = {os.path.basename(f)[:-8] for f in os.listdir('/home/user/ext/replay/flush') if f.endswith('.parquet') and not f.endswith('.sig.parquet')}
    seen = set()            # 已完成的从日志里认（状态表列数不固定）
    if os.path.exists(f'{M}/run.log'):
        import ast
        seen = {ast.literal_eval(l.split(' ', 1)[1])['coin'] for l in open(f'{M}/run.log', encoding='utf-8') if re.match(r'^\d\d:\d\d \{', l)}
    todo = []
    for s in syms:
        name, raw = base_of(s)
        if name in STABLE or CAT.get(name, '1') != '1' or CAT.get(raw, '1') != '1': continue
        if name in done or raw in done or raw in seen: continue
        todo.append((raw, s))
    print('币安合约', len(syms), '个；已测', len(done), '；要补', len(todo), flush=True)
    from multiprocessing import Pool
    with Pool(int(os.environ.get('NP', 3)), initializer=init) as p:
        for row in p.imap_unordered(job, todo):
            pd.DataFrame([row]).to_csv(STATUS, mode='a', header=not os.path.exists(STATUS), index=False)
            print(time.strftime('%H:%M'), row, flush=True)
    print('ALL DONE', flush=True)
