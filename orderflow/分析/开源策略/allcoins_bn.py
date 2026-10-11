"""全部币测试：币安 U 本位【全部】USDT 永续（约 900 个，含已下架的），2021~2026 每季度分开跑。
数据：data.binance.vision 的月度 5 分钟K线（每个季度只下需要的 4 个月，算完就删，不占磁盘）。
引擎：策略机的回测引擎（和 freqtrade 逐单对过账：23 个季度 17236 单完全一样）。
条件：最多同时 10 单、每单 = 可用资金 ÷ 剩余空位，1000U 起每季度重来，每边手续费 0.07%，不检查最小下单量；
     每个币前 400 根K线只用来预热不开单（新币上线头 33 小时不做，和 freqtrade 一样）。
另外算：移动止盈每单少 0.3%（更接近实盘的悲观估计）。
用法：python allcoins_bn.py 策略类名"""
import os, sys, io, json, glob, time, zipfile, shutil
from concurrent.futures import ThreadPoolExecutor
import numpy as np, pandas as pd, httpx
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..', 'stratbot'))
import sb_loader, sb_engine  # noqa: E402
from numpy import format_float_positional

cls = sys.argv[1]
SPATH = os.environ.get('STRAT_FILE')                 # 策略文件（不在 stratbot/strategies 里时给路径）
OUT = f'/home/user/ext/ftrun/allbn_{cls}'
CACHE = '/home/user/ext/bnm'
os.makedirs(OUT, exist_ok=True); os.makedirs(CACHE, exist_ok=True)
SYMS = json.load(open('/home/user/ext/bn_um_usdt.json'))
st = sb_loader.Strategy(cls, SPATH)
KEEP = sb_loader.callback_columns(st)                 # 回调里用到的列（全部币时只留这些，省内存）
uses_dp = st.use_custom_stoploss or st.has_custom_exit or st.has_confirm_exit or st.has_confirm_entry
cli = httpx.Client(timeout=60, headers={'User-Agent': 'Mozilla/5.0'}, follow_redirects=True)
COLS = ['open_time', 'open', 'high', 'low', 'close', 'volume']


def month_file(sym, ym):
    """一个币一个月的 5 分钟K线 → npz 缓存（没有这个月就记一个空标记）"""
    p = f'{CACHE}/{sym}_{ym}.npz'
    if os.path.exists(p) or os.path.exists(p + '.none'):
        return p
    url = f'https://data.binance.vision/data/futures/um/monthly/klines/{sym}/5m/{sym}-5m-{ym}.zip'
    for k in range(4):
        try:
            r = cli.get(url)
            if r.status_code == 404:
                open(p + '.none', 'w').close(); return p
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                raw = z.read(z.namelist()[0]).decode()
            first = raw.split('\n', 1)[0]
            d = pd.read_csv(io.StringIO(raw), header=0 if first.startswith('open_time') else None, usecols=range(6))
            d.columns = COLS
            tmp = p[:-4] + '.tmp.npz'                     # 先写临时文件再改名：中途被杀不会留下坏文件
            np.savez_compressed(tmp, **{c: d[c].to_numpy(np.float64 if c != 'open_time' else np.int64) for c in COLS})
            os.replace(tmp, p)
            return p
        except Exception as e:  # noqa: BLE001
            err = e; time.sleep(2 + 3 * k)
    print('下载失败', sym, ym, err, flush=True)
    return p


def load(sym, months, a0, a1):
    parts = []
    for ym in months:
        p = f'{CACHE}/{sym}_{ym}.npz'
        if os.path.exists(p):
            try:
                z = np.load(p)
                parts.append(pd.DataFrame({c: z[c] for c in COLS}))
            except Exception:  # noqa: BLE001  坏文件（以前中途被杀留下的）：删掉重新下
                os.remove(p)
                month_file(sym, ym)
                z = np.load(p)
                parts.append(pd.DataFrame({c: z[c] for c in COLS}))
    if not parts:
        return None
    d = pd.concat(parts, ignore_index=True)
    d = d[(d.open_time >= a0) & (d.open_time <= a1)].drop_duplicates('open_time').sort_values('open_time')
    if len(d) <= st.startup + 1:
        return None
    d['date'] = pd.to_datetime(d.open_time, unit='ms', utc=True)
    return sb_loader.fill_missing(d[['date', 'open', 'high', 'low', 'close', 'volume']].reset_index(drop=True), st.tf_min)


def tick_over_time(c):
    c = c.copy()
    for col in ['open', 'high', 'low', 'close']:
        c[f'{col}_c'] = (c[col].apply(format_float_positional, precision=14, unique=False, fractional=False, trim='-')
                         .str.extract(r'\.(\d*[1-9])')[0].str.len())
    return 1 / 10 ** c.set_index('date')[['open_c', 'high_c', 'low_c', 'close_c']].max(axis=1).resample('MS').max()


quarters = [(y, q) for y in range(2021, 2027) for q in (1, 2, 3, 4) if not (y == 2026 and q == 4)]
for y, q in quarters:
    of = f'{OUT}/trades{y}q{q}.csv'
    if os.path.exists(of):
        continue
    m0 = (q - 1) * 3 + 1
    t0 = pd.Timestamp(year=y, month=m0, day=1, tz='UTC')
    t1 = (t0 + pd.DateOffset(months=3)) if not (y == 2026 and q == 3) else pd.Timestamp('2026-09-30', tz='UTC')
    months = [(t0 + pd.DateOffset(months=k)).strftime('%Y-%m') for k in (-1, 0, 1, 2)]
    tt = time.time()
    with ThreadPoolExecutor(24) as ex:
        list(ex.map(lambda a: month_file(*a), [(s, ym) for s in SYMS for ym in months]))
    t_dl = time.time() - tt
    w0 = t0 - pd.Timedelta(minutes=st.tf_min * st.startup)
    a0, a1 = int(w0.timestamp() * 1000), int(t1.timestamp() * 1000)
    data, ticks, full = {}, {}, {}
    for s in SYMS:
        d = load(s, months, a0, a1)
        if d is None:
            continue
        p = s[:-4] + '/USDT'
        ticks[p] = (tick_over_time(d), None)
        a, fa = st.analyze(d, p, keep_full=True)
        a = a.iloc[st.startup:].reset_index(drop=True)
        a = a[[c for c in a.columns if c != 'volume' and (c not in ('enter_tag', 'exit_tag') or (a[c] != '').any())]]
        del d
        if len(a) > 1:
            data[p] = a
            if uses_dp:
                cols = list(fa.columns) if KEEP is None else [c for c in fa.columns if c in KEEP or c in (
                    'date', 'open', 'high', 'low', 'close', 'enter_long', 'exit_long')]
                full[p] = fa[cols].reset_index(drop=True)
    pairs = sorted(data)
    T = pd.DataFrame(sb_engine.Backtest(st, max_open=10, fee=0.0007, ticks=ticks, wallet=1000, tradable_ratio=0.99).run(
        data, pairs, full=full or None, startup=st.startup, consume=True))
    T.to_csv(of, index=False)
    json.dump(pairs, open(f'{OUT}/coins{y}q{q}.json', 'w'))
    print(y, f'q{q}', '币', len(pairs), '单', len(T), f'下载 {t_dl:.0f}s 共 {time.time() - tt:.0f}s', flush=True)
    keep = {(t0 + pd.DateOffset(months=k)).strftime('%Y-%m') for k in (2,)}          # 下个季度还要用本季度最后一个月预热
    for f in glob.glob(f'{CACHE}/*'):
        if not any(f'_{k}.npz' in f for k in keep):
            os.remove(f)

rows = []
for y in range(2021, 2027):
    fs = [f for f in sorted(glob.glob(f'{OUT}/trades{y}q*.csv')) if os.path.getsize(f) > 10]
    if not fs:
        continue
    T = pd.concat([pd.read_csv(f) for f in fs]).sort_values('close_ts')
    coins = set().union(*[set(json.load(open(f.replace('trades', 'coins').replace('.csv', '.json')))) for f in fs])
    days = 272 if y == 2026 else 365
    for hair in (0.0, 0.003):
        r = T.profit_ratio - (T.exit_reason == 'trailing_stop_loss') * hair
        pa = r * T.stake
        eq = 1000 + pa.cumsum(); peak = eq.cummax().clip(lower=1000)
        gp, gl = pa[pa > 0].sum(), -pa[pa < 0].sum()
        rows.append({'年': y, '移动止盈少': f'{hair * 100:.1f}%', '参与的币': len(coins), '做过的币': T.pair.nunique(), '单数': len(T),
                     '每天': round(len(T) / days, 1), '胜率': round((r > 0).mean(), 3), '盈亏比': round(gp / gl, 2),
                     '每单平均%': round(r.mean() * 100, 3), '回撤%': round(((peak - eq) / peak).max() * 100, 1)})
R = pd.DataFrame(rows)
print(R.to_string(index=False))
R.to_csv(f'{OUT}/summary.csv', index=False)
