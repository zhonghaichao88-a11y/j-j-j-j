"""初筛过关的开源策略，用全部币按年复测（合约模式，跟实盘一样）。
条件（每年按季度分段跑，见下）：OKX 全部有数据的 USDT 合约（137 个；只做多的老策略用现货模式跑同样的K线），2021~2026 每年单独跑（一次跑 6 年会爆内存），
手续费每边 0.07%（吃单 0.05% + 滑点 0.02%），最多同时 10 单、每单约 10% 资金，1000U 起，含资金费。
用法：python fulltest.py 阶段/策略id ...（例：screen/9636912944_UniversalMACD）"""
import os, sys, json, glob, subprocess, zipfile, time, re
import pandas as pd
RUN = '/home/user/ext/ftrun'; FT = '/home/user/ext/ftvenv/bin/freqtrade'; DATA = '/home/user/ext/ft/data_long/okx'; SPOT = '/home/user/ext/ft/data_spot/okx'
PAIRS = sorted({os.path.basename(f).split('-')[0].replace('_USDT_USDT', '/USDT:USDT') for f in glob.glob(f'{DATA}/futures/*-5m-futures.feather')})
YEARS = {2021: '20210101-20220101', 2022: '20220101-20230101', 2023: '20230101-20240101', 2024: '20240101-20250101',
         2025: '20250101-20260101', 2026: '20260101-20260930'}
# 一年 137 个币 5 分钟线一次跑要 6~7G 内存会被系统杀掉：每年拆 4 个季度跑再合并（季度末没平的单按收盘强平，影响很小）。
# 收益 = 全年每单盈亏相加 ÷ 1000U 起始资金（每季度从 1000U 重新开始，不复利）；回撤按全年平仓顺序累计。
QUARTERS = {y: [(q + 1, f'{y}{m1}01-' + (f'{y}{m2}01' if m2 != '01' else f'{y + 1}0101')) for q, (m1, m2) in
                enumerate([('01', '04'), ('04', '07'), ('07', '10'), ('10', '01')])] for y in YEARS}
QUARTERS[2026] = [(1, '20260101-20260401'), (2, '20260401-20260701'), (3, '20260701-20260930')]
for sid in sys.argv[1:]:
    src = f'{RUN}/{sid}'; d = json.load(open(f'{src}/done.json')); wd = f'{RUN}/full/{sid.split("/")[-1]}'
    os.makedirs(f'{wd}/user_data/csvs', exist_ok=True)
    c = json.load(open(f'{src}/c.json'))
    fut = c['trading_mode'] == 'futures'      # 老写法(V2)的策略合约模式不让跑，只做多的就用现货模式跑同一份合约K线（不算资金费）
    c.update(max_open_trades=10, fee=0.0007)
    c['exchange']['pair_whitelist'] = PAIRS if fut else [p.replace(':USDT', '') for p in PAIRS]
    json.dump(c, open(f'{wd}/c.json', 'w'))
    out = {}
    for y in YEARS:
        if os.path.exists(f'{wd}/{y}.json'): out[y] = json.load(open(f'{wd}/{y}.json')); continue
        Ts, errs, secs = [], [], 0
        for q, tr in QUARTERS[y]:
            qf = f'{wd}/trades{y}q{q}.csv'
            if os.path.exists(qf):
                Ts.append(pd.read_csv(qf)); continue
            bt = f'{wd}/bt{y}q{q}'; os.makedirs(bt, exist_ok=True); t = time.time()
            try:
                p = subprocess.run([FT, 'backtesting', '-c', f'{wd}/c.json', '--userdir', '/home/user/ext/ft', '--datadir', DATA if fut else SPOT,
                                    '--strategy', d['cls'], '--strategy-path', f'{src}/s', '--timerange', tr, '--export', 'trades',
                                    '--backtest-directory', bt + '/', '--cache', 'none'], capture_output=True, text=True, timeout=4 * 3600, cwd=wd)
                log = (p.stdout + p.stderr)[-2000:]; rc = p.returncode
            except subprocess.TimeoutExpired:
                log, rc = 'TIMEOUT', -1
            secs += time.time() - t
            z = sorted(glob.glob(f'{bt}/*.zip'))
            if z:
                with zipfile.ZipFile(z[-1]) as f:
                    j = json.loads(f.read([n for n in f.namelist() if n.endswith('.json') and 'config' not in n and 'market' not in n][0]))
                T = pd.DataFrame(list(j['strategy'].values())[0]['trades'])
                if not len(T): T = pd.DataFrame(columns=['pair', 'open_date', 'close_date', 'profit_abs', 'trade_duration'])
                T.to_csv(qf, index=False); Ts.append(T)
            else:
                errs.append(f'q{q} rc={rc} ' + ((' '.join(l for l in log.split('\n') if 'rror' in l or 'TIMEOUT' in l)[-200:]) or log[-200:]))
            for f in glob.glob(f'{bt}/*'): os.remove(f)
        if errs:   # 有季度没跑出来（多半是内存不够被杀）：先不写年度结果，下次重跑只补缺的季度
            print(d['cls'], y, '没跑完', errs, flush=True); continue
        T = pd.concat(Ts, ignore_index=True) if Ts else pd.DataFrame()
        r = {'secs': round(secs)}
        if len(T):
            T = T.sort_values('close_date'); eq = 1000 + T.profit_abs.cumsum(); peak = eq.cummax().clip(lower=1000)
            gp, gl = T.profit_abs[T.profit_abs > 0].sum(), -T.profit_abs[T.profit_abs < 0].sum()
            r.update(n=len(T), win=round((T.profit_abs > 0).mean(), 3), profit=round(T.profit_abs.sum() / 10, 1),
                     dd=round(((peak - eq) / peak).max() * 100, 1), pf=round(float(gp / gl), 2) if gl > 0 else (99 if gp > 0 else 0),
                     coins=int(T.pair.nunique()), hold_h=round(float(T.trade_duration.mean()) / 60, 1))
        else:
            r.update(n=0)
        json.dump(r, open(f'{wd}/{y}.json', 'w')); out[y] = r
        print(d['cls'], y, r, flush=True)
    json.dump(out, open(f'{wd}/summary.json', 'w'))
