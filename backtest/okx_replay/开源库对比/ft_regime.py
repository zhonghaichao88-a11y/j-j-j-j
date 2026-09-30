"""4 个网友策略 × 2 种改法（_BTC 只做多+大盘过滤 / _LS 多空+大盘过滤），老40币 与 178 个新币，第一年/第二年；合约模式。
结果 ft/bt_regime/<策略>__<币>__<段>；最后汇总 ft_regime.txt。"""
import glob, json, os, subprocess, zipfile
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
os.chdir('/home/user/ext'); env = dict(os.environ, SSL_CERT_FILE='/root/.ccr/ca-bundle.crt')
old = [i.split('-')[0] + '/USDT:USDT' for i in json.load(open('/home/user/okx_data/universe_5m40.json'))]
more = json.load(open('pairs_more.json'))
S = [f'{b}_{v}' for b in ('ADXMomentum', 'AverageStrategy', 'MultiMa', 'FSupertrendStrategy') for v in ('BTC', 'LS')]
PER = {'第一年': '20240929-20250929', '第二年': '20250929-20260930'}
jobs = [(s, g, p) for s in S for g in ('老40', '新178') for p in PER]


def one(job):
    s, g, p = job; d = f'ft/bt_regime/{s}__{g}__{p}'
    if os.path.isdir(d): return
    os.makedirs(d + '.tmp', exist_ok=True)
    r = subprocess.run(['nice', '-n', '5', 'ftvenv/bin/freqtrade', 'backtesting', '-c', 'ft/config_futures.json', '--userdir', 'ft', '--strategy', s,
                        '--timerange', PER[p], '--export', 'trades', '--backtest-directory', d + '.tmp', '-p', *(old if g == '老40' else more)],
                       capture_output=True, text=True, env=env)
    open(d + '.tmp/log.txt', 'w').write(r.stdout[-20000:] + r.stderr[-20000:]); os.rename(d + '.tmp', d); print(d, r.returncode, flush=True)


os.makedirs('ft/bt_regime', exist_ok=True)
with ThreadPoolExecutor(2) as ex: list(ex.map(one, jobs))
rows = []
for s, g, p in jobs:
    d = f'ft/bt_regime/{s}__{g}__{p}'; z = glob.glob(d + '/*.zip')
    if not z: rows.append(dict(策略=s, 币=g, 段=p, 备注='失败')); continue
    Z = zipfile.ZipFile(z[0]); n = [x for x in Z.namelist() if x.endswith('.json') and '_config' not in x][0]
    st = list(json.loads(Z.read(n))['strategy'].values())[0]; t = pd.DataFrame(st['trades'])
    if t.empty: rows.append(dict(策略=s, 币=g, 段=p, 笔数=0)); continue
    pr = t.profit_ratio
    rows.append(dict(策略=s, 币=g, 段=p, 笔数=len(t), 空单=int(st['trade_count_short']), 胜率=f'{(pr > 0).mean() * 100:.0f}%',
                     收益=f"{st['profit_total'] * 100:+.1f}%", 回撤=f"{st['max_drawdown_account'] * 100:.1f}%", 市场=f"{st['market_change'] * 100:+.1f}%"))
R = pd.DataFrame(rows); R.to_csv('ft_regime.csv', index=False)
pd.set_option('display.width', 250); print(R.to_string(index=False))
open('ft_regime.done', 'w').close()
