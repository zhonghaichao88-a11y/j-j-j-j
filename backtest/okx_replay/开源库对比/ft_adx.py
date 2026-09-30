"""ADXMomentum 加大盘过滤（_BTC 只做多 / _LS 多空）稳健性：固定每笔 100U（钱包1000U，最多10仓，不滚利），
手续费单边 0.10% / 0.15%（≈加 0.05% 滑点）/ 0.20%（≈加 0.10% 滑点）；老40币、178个新币；第一年/第二年。"""
import glob, json, os, subprocess, zipfile
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
os.chdir('/home/user/ext'); env = dict(os.environ, SSL_CERT_FILE='/root/.ccr/ca-bundle.crt')
old = [i.split('-')[0] + '/USDT:USDT' for i in json.load(open('/home/user/okx_data/universe_5m40.json'))]
more = json.load(open('pairs_more.json'))
PER = {'第一年': '20240929-20250929', '第二年': '20250929-20260930'}
jobs = [(s, g, p, fee) for s in ('ADXMomentum_BTC', 'ADXMomentum_LS') for fee in ('0.001', '0.0015', '0.002') for g in ('老40', '新178') for p in PER]


def one(j):
    s, g, p, fee = j; d = f'ft/bt_adx/{s}__{g}__{p}__{fee}'
    if os.path.isdir(d): return
    os.makedirs(d + '.tmp', exist_ok=True)
    r = subprocess.run(['nice', '-n', '5', 'ftvenv/bin/freqtrade', 'backtesting', '-c', 'ft/config_futures_fixed.json', '--userdir', 'ft', '--strategy', s,
                        '--timerange', PER[p], '--fee', fee, '--export', 'trades', '--backtest-directory', d + '.tmp', '-p', *(old if g == '老40' else more)],
                       capture_output=True, text=True, env=env)
    open(d + '.tmp/log.txt', 'w').write(r.stdout[-20000:] + r.stderr[-20000:]); os.rename(d + '.tmp', d); print(d, flush=True)


os.makedirs('ft/bt_adx', exist_ok=True)
with ThreadPoolExecutor(3) as ex: list(ex.map(one, jobs))
rows = []
for s, g, p, fee in jobs:
    d = f'ft/bt_adx/{s}__{g}__{p}__{fee}'; z = glob.glob(d + '/*.zip')
    if not z: rows.append(dict(策略=s, 手续费=fee, 币=g, 段=p, 备注='失败')); continue
    Z = zipfile.ZipFile(z[0]); n = [x for x in Z.namelist() if x.endswith('.json') and '_config' not in x][0]
    st = list(json.loads(Z.read(n))['strategy'].values())[0]; t = pd.DataFrame(st['trades']); pr = t.profit_ratio
    rows.append(dict(策略=s, 手续费=fee, 币=g, 段=p, 笔数=len(t), 空单=int(st['trade_count_short']), 胜率=f'{(pr > 0).mean() * 100:.0f}%',
                     每笔=f'{pr.mean() * 100:+.3f}%', 收益=f"{st['profit_total'] * 100:+.1f}%", 回撤=f"{st['max_drawdown_account'] * 100:.1f}%"))
R = pd.DataFrame(rows); R.to_csv('ft_adx.csv', index=False)
pd.set_option('display.width', 250); print(R.to_string(index=False))
open('ft_adx.done', 'w').close()
