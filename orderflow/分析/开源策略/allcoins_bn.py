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
TOPN = int(os.environ.get('TOPN', 0))               # >0：每个季度只做前 N 个成交额最大的币（按季度开始前 30 天的成交额排，不偷看）
OUT = f'/home/user/ext/ftrun/allbn_{cls}' + (f'_top{TOPN}' if TOPN else '')
CACHE = os.environ.get('BN_CACHE', '/home/user/ext/bnm')     # 两个全部币测试同时跑时各用各的缓存目录（季度末会清缓存）
os.makedirs(OUT, exist_ok=True); os.makedirs(CACHE, exist_ok=True)
SYMS = json.load(open('/home/user/ext/bn_um_usdt.json'))
SYMS_SET = set(SYMS)
st = sb_loader.Strategy(cls, SPATH, runmode='backtest')
KEEP = sb_loader.callback_columns(st)                 # 回调里用到的列（全部币时只留这些，省内存）
uses_dp = st.uses_callbacks
cli = httpx.Client(timeout=60, headers={'User-Agent': 'Mozilla/5.0'}, follow_redirects=True)
COLS = ['open_time', 'open', 'high', 'low', 'close', 'volume']


def month_file(sym, ym, tf=None):
    """一个币一个月的K线（默认策略的主周期）→ npz 缓存（没有这个月就记一个空标记）"""
    tf = tf or st.timeframe
    p = f'{CACHE}/{sym}_{ym}{"" if tf == "5m" else "_" + tf}.npz'
    if os.path.exists(p) or os.path.exists(p + '.none'):
        return p
    url = f'https://data.binance.vision/data/futures/um/monthly/klines/{sym}/{tf}/{sym}-{tf}-{ym}.zip'
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


def load(sym, months, a0, a1, tf=None, min_rows=None):
    tf = tf or st.timeframe
    parts = []
    for ym in months:
        p = f'{CACHE}/{sym}_{ym}{"" if tf == "5m" else "_" + tf}.npz'
        if os.path.exists(p):
            try:
                z = np.load(p)
                parts.append(pd.DataFrame({c: z[c] for c in COLS}))
            except Exception:  # noqa: BLE001  坏文件（以前中途被杀留下的）：删掉重新下
                os.remove(p)
                month_file(sym, ym, tf)
                z = np.load(p)
                parts.append(pd.DataFrame({c: z[c] for c in COLS}))
    if not parts:
        return None
    d = pd.concat(parts, ignore_index=True)
    d = d[(d.open_time >= a0) & (d.open_time <= a1)].drop_duplicates('open_time').sort_values('open_time')
    if len(d) <= (st.startup + 1 if min_rows is None else min_rows):
        return None
    d['date'] = pd.to_datetime(d.open_time, unit='ms', utc=True)
    return sb_loader.fill_missing(d[['date', 'open', 'high', 'low', 'close', 'volume']].reset_index(drop=True), sb_loader.TF_MIN[tf])


def bn_source(t0, t1):
    """策略要别的币/别的周期K线（informative）：币安同一份数据，按 freqtrade 的窗口 [开始 - 预热根数 × 该周期, 结束] 下载"""
    cache = {}

    def src(pair, tf):
        sym = pair.split('/')[0].upper() + 'USDT'
        k = (sym, tf)
        if k not in cache:
            if sym not in SYMS_SET or tf not in sb_loader.TF_MIN:
                cache[k] = None
            else:
                w0_ = t0 - pd.Timedelta(minutes=sb_loader.TF_MIN[tf] * st.startup)
                ms = pd.period_range(w0_.strftime('%Y-%m'), (t1 - pd.Timedelta(seconds=1)).strftime('%Y-%m'), freq='M').strftime('%Y-%m')
                for ym in ms:
                    month_file(sym, ym, tf)
                cache[k] = load(sym, ms, int(w0_.timestamp() * 1000), int(t1.timestamp() * 1000), tf, min_rows=0)
        return cache[k]
    return src


def tick_over_time(c):
    c = c.copy()
    for col in ['open', 'high', 'low', 'close']:
        c[f'{col}_c'] = (c[col].apply(format_float_positional, precision=14, unique=False, fractional=False, trim='-')
                         .str.extract(r'\.(\d*[1-9])')[0].str.len())
    return 1 / 10 ** c.set_index('date')[['open_c', 'high_c', 'low_c', 'close_c']].max(axis=1).resample('MS').max()


quarters = [(y, q) for y in range(2021, 2027) for q in (1, 2, 3, 4) if not (y == 2026 and q == 4)]
def others_running():
    """别的全部币测试（一个要 5~7G 内存，同一时间只能跑一个）"""
    me = os.getpid()
    for d in os.listdir('/proc'):
        if d.isdigit() and int(d) != me:
            try:
                a = open(f'/proc/{d}/cmdline', 'rb').read().split(b'\0')
            except OSError:
                continue
            if len(a) > 1 and a[0].endswith(b'python') and a[1] == b'allcoins_bn.py':
                return True
    return False


while others_running():                              # 排队：等前一个全部币测试跑完
    time.sleep(60)
QONLY = [x for x in os.environ.get('QONLY', '').split(',') if x]   # 只跑这些季度（例 2026q1,2026q2）：分几路同时跑时用


def year_pf(y):
    """这一年 4 个季度都算完了：全年盈亏比；没算完返回 None"""
    fs = sorted(glob.glob(f'{OUT}/trades{y}q*.csv'))
    if len(fs) < (3 if y == 2026 else 4):
        return None
    T = pd.concat([pd.read_csv(f) for f in fs if os.path.getsize(f) > 10] or [pd.DataFrame(columns=['profit_ratio', 'stake'])])
    pa = T.profit_ratio * T.stake
    gl = -pa[pa < 0].sum()
    return (pa[pa > 0].sum() / gl) if gl > 0 else 99


for y, q in quarters:
    bad = [yy for yy in range(2021, y) if (year_pf(yy) or 99) < 1.1]
    if bad and not QONLY and os.environ.get('ALL_YEARS') != '1':     # 已经有一年不到 1.1：结论已定（不过关），后面不用再算
        print(f'{bad[0]} 年全部币盈亏比 {year_pf(bad[0]):.2f} 不到 1.1，不再往下算', flush=True)
        break
    of = f'{OUT}/trades{y}q{q}.csv'
    if os.path.exists(of) or (QONLY and f'{y}q{q}' not in QONLY):
        continue
    while True:                                       # 一个季度要 5~7G 内存：可用不到 8G 就等（别的全部币测试在跑时排队，免得一起被系统杀掉）
        avail = next(int(l.split()[1]) for l in open('/proc/meminfo') if l.startswith('MemAvailable'))
        if avail > 8 * 1024 * 1024:
            break
        time.sleep(60)
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
    syms = SYMS
    if TOPN:
        b0, b1 = int((t0 - pd.Timedelta(days=30)).timestamp() * 1000), int(t0.timestamp() * 1000)
        qv = {}
        for s_ in SYMS:
            p_ = f'{CACHE}/{s_}_{months[0]}{"" if st.timeframe == "5m" else "_" + st.timeframe}.npz'
            if os.path.exists(p_):
                try:
                    z = np.load(p_)
                    m_ = (z['open_time'] >= b0) & (z['open_time'] < b1)
                    qv[s_] = float((z['volume'][m_] * z['close'][m_]).sum())
                except Exception:  # noqa: BLE001
                    pass
        syms = [k for k, v in sorted(qv.items(), key=lambda kv: -kv[1]) if v > 0][:TOPN]
    src = bn_source(t0, t1)
    st.s.dp.whitelist = [x[:-4] + '/USDT' for x in syms]
    for s in syms:
        d = load(s, months, a0, a1)
        if d is None:
            continue
        p = s[:-4] + '/USDT'
        ticks[p] = (tick_over_time(d), None)
        a, fa = st.analyze(d, p, keep_full=True, inf=src)
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
        if not any(f'_{k}.npz' in f or f'_{k}_' in f for k in keep):
            os.remove(f)

if QONLY:                                             # 分路跑的只负责算季度，汇总由主路做
    sys.exit(0)
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
