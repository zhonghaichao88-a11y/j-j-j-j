"""初筛过关的开源策略，用全部币按年复测（合约模式，跟实盘一样）。
条件：OKX 全部有数据的 USDT 合约（137 个；只做多的老策略用现货模式跑同样的K线），2021~2026 每年单独跑（一次跑 6 年会爆内存），
手续费每边 0.07%（吃单 0.05% + 滑点 0.02%），最多同时 10 单、每单约 10% 资金，1000U 起，含资金费。
用法：python fulltest.py 阶段/策略id ...（例：screen/9636912944_UniversalMACD）"""
import os, sys, json, glob, subprocess, zipfile, time, re
import pandas as pd
RUN = '/home/user/ext/ftrun'; FT = '/home/user/ext/ftvenv/bin/freqtrade'; DATA = '/home/user/ext/ft/data_long/okx'; SPOT = '/home/user/ext/ft/data_spot/okx'
PAIRS = sorted({os.path.basename(f).split('-')[0].replace('_USDT_USDT', '/USDT:USDT') for f in glob.glob(f'{DATA}/futures/*-5m-futures.feather')})
YEARS = {2021: '20210101-20220101', 2022: '20220101-20230101', 2023: '20230101-20240101', 2024: '20240101-20250101',
         2025: '20250101-20260101', 2026: '20260101-20260930'}
for sid in sys.argv[1:]:
    src = f'{RUN}/{sid}'; d = json.load(open(f'{src}/done.json')); wd = f'{RUN}/full/{sid.split("/")[-1]}'
    os.makedirs(f'{wd}/user_data/csvs', exist_ok=True)
    c = json.load(open(f'{src}/c.json'))
    fut = c['trading_mode'] == 'futures'      # 老写法(V2)的策略合约模式不让跑，只做多的就用现货模式跑同一份合约K线（不算资金费）
    c.update(max_open_trades=10, fee=0.0007)
    c['exchange']['pair_whitelist'] = PAIRS if fut else [p.replace(':USDT', '') for p in PAIRS]
    json.dump(c, open(f'{wd}/c.json', 'w'))
    out = {}
    for y, tr in YEARS.items():
        if os.path.exists(f'{wd}/{y}.json'): out[y] = json.load(open(f'{wd}/{y}.json')); continue
        bt = f'{wd}/bt{y}'; os.makedirs(bt, exist_ok=True); t = time.time()
        try:
            p = subprocess.run([FT, 'backtesting', '-c', f'{wd}/c.json', '--userdir', '/home/user/ext/ft', '--datadir', DATA if fut else SPOT,
                                '--strategy', d['cls'], '--strategy-path', f'{src}/s', '--timerange', tr, '--export', 'trades',
                                '--backtest-directory', bt + '/', '--cache', 'none'], capture_output=True, text=True, timeout=4 * 3600, cwd=wd)
            log = (p.stdout + p.stderr)[-2000:]
        except subprocess.TimeoutExpired:
            log = 'TIMEOUT'
        r = {'secs': round(time.time() - t)}
        z = sorted(glob.glob(f'{bt}/*.zip'))
        if z:
            with zipfile.ZipFile(z[-1]) as f:
                j = json.loads(f.read([n for n in f.namelist() if n.endswith('.json') and 'config' not in n and 'market' not in n][0]))
            s = list(j['strategy'].values())[0]; T = pd.DataFrame(s['trades'])
            gp = T.profit_abs[T.profit_abs > 0].sum() if len(T) else 0; gl = -T.profit_abs[T.profit_abs < 0].sum() if len(T) else 0
            r.update(n=s['total_trades'], win=round(s.get('wins', 0) / max(s['total_trades'], 1), 3), profit=round(s['profit_total'] * 100, 1),
                     dd=round(s.get('max_drawdown_account', 0) * 100, 1), pf=round(gp / gl, 2) if gl > 0 else (99 if gp > 0 else 0),
                     coins=int(T.pair.nunique()) if len(T) else 0, hold_h=round(T.trade_duration.mean() / 60, 1) if len(T) else 0)
            T.to_csv(f'{wd}/trades{y}.csv', index=False)
        else:
            r['err'] = [l for l in log.split('\n') if 'rror' in l or 'TIMEOUT' in l or 'Killed' in l][-1:] or [log[-300:]]
        for f in glob.glob(f'{bt}/*'): os.remove(f)
        json.dump(r, open(f'{wd}/{y}.json', 'w')); out[y] = r
        print(d['cls'], y, r, flush=True)
    json.dump(out, open(f'{wd}/summary.json', 'w'))
