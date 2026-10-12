"""回测"一根K线里按最高价算"的乐观程度检查：同一批币、同一段时间，freqtrade 回测跑两遍——
只用 5 分钟K线 vs 加 --timeframe-detail 1m（每根 5 分钟K线里按 1 分钟K线的先后顺序走），看收益差多少。
数据：币安现货 1 分钟/5 分钟K线（data.binance.vision 按月下载），存成 freqtrade 的格式。
用法：python detail_check.py 策略文件 策略类名 配置c.json 开始月 结束月（例 2022-01 2022-03）"""
import os, sys, io, json, zipfile, subprocess, glob
import pandas as pd, httpx
sfile, cls, cfg, m0, m1 = sys.argv[1:6]
OUT = f'/home/user/ext/det/{cls}'; DATA = f'{OUT}/data'; os.makedirs(DATA, exist_ok=True)
c = json.load(open(cfg)); pairs = c['exchange']['pair_whitelist']
px = os.environ.get('HTTPS_PROXY'); c['exchange']['ccxt_config'] = {'httpsProxy': px}; c['exchange']['ccxt_async_config'] = {'httpsProxy': px}
json.dump(c, open(f'{OUT}/c.json', 'w'))
months = pd.period_range(pd.Period(m0, 'M') - 1, m1, freq='M').strftime('%Y-%m')
cli = httpx.Client(timeout=120, follow_redirects=True)
for p in pairs:
    sym = p.split('/')[0] + 'USDT'
    for tf in ('5m', '1m'):
        f = f"{DATA}/{p.replace('/', '_')}-{tf}.feather"
        if os.path.exists(f):
            continue
        parts = []
        for ym in months:
            r = cli.get(f'https://data.binance.vision/data/spot/monthly/klines/{sym}/{tf}/{sym}-{tf}-{ym}.zip')
            if r.status_code != 200:
                continue
            raw = zipfile.ZipFile(io.BytesIO(r.content)).read(zipfile.ZipFile(io.BytesIO(r.content)).namelist()[0]).decode()
            d = pd.read_csv(io.StringIO(raw), header=None, usecols=range(6))
            d.columns = ['ts', 'open', 'high', 'low', 'close', 'volume']
            if d.ts.iloc[0] > 1e14:            # 2025 年以后币安改成微秒
                d['ts'] = d.ts // 1000
            parts.append(d)
        if parts:
            d = pd.concat(parts).drop_duplicates('ts').sort_values('ts')
            d['date'] = pd.to_datetime(d.ts, unit='ms', utc=True)
            d[['date', 'open', 'high', 'low', 'close', 'volume']].reset_index(drop=True).to_feather(f)
    print('数据', p, flush=True)
tr = pd.Period(m0, 'M').start_time.strftime('%Y%m%d') + '-' + (pd.Period(m1, 'M') + 1).start_time.strftime('%Y%m%d')
res = {}
for name, extra in (('5m', []), ('1m细看', ['--timeframe-detail', '1m'])):
    bt = f'{OUT}/bt_{name}'; os.makedirs(bt, exist_ok=True)
    for x in glob.glob(f'{bt}/*'): os.remove(x)
    subprocess.run(['/home/user/ext/ftvenv/bin/freqtrade', 'backtesting', '-c', f'{OUT}/c.json', '--userdir', '/home/user/ext/ft', '--datadir', DATA,
                    '--strategy', cls, '--strategy-path', os.path.dirname(sfile), '--timerange', tr, '--export', 'trades',
                    '--backtest-directory', bt + '/', '--cache', 'none'] + extra, capture_output=True, text=True, cwd=OUT)
    z = sorted(glob.glob(f'{bt}/*.zip'))
    if not z:
        res[name] = '没跑出来'; continue
    with zipfile.ZipFile(z[-1]) as zf:
        j = json.loads(zf.read([n for n in zf.namelist() if n.endswith('.json') and 'config' not in n and 'market' not in n][0]))
    T = pd.DataFrame(list(j['strategy'].values())[0]['trades'])
    gp, gl = T.profit_abs[T.profit_abs > 0].sum(), -T.profit_abs[T.profit_abs < 0].sum()
    res[name] = {'单数': len(T), '胜率': round((T.profit_abs > 0).mean(), 3), '盈亏比': round(gp / gl, 2) if gl else 99,
                 '每单平均%': round(T.profit_ratio.mean() * 100, 3), '总收益%': round(T.profit_abs.sum() / c['dry_run_wallet'] * 100, 1),
                 '按原因': T.groupby('exit_reason').profit_ratio.agg(['count', 'mean']).round(4).to_dict()}
    print(name, res[name], flush=True)
json.dump(res, open(f'{OUT}/result.json', 'w'), ensure_ascii=False, indent=1)
