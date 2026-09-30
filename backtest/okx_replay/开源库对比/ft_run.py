"""freqtrade-strategies 全部策略：40 个币，第一年/第二年分开回测（手续费单边 0.1%，最多同时 10 仓，资金均分）。
现货策略用现货模式（只做多）；futures 目录里的策略用合约模式（可做空，资金费率按 0）。结果导出到 ft/bt_results/。"""
import json, os, subprocess, sys
os.chdir('/home/user/ext')
env = dict(os.environ, SSL_CERT_FILE='/root/.ccr/ca-bundle.crt')
FUT = ['FAdxSmaStrategy', 'FOttStrategy', 'FReinforcedStrategy', 'FSampleStrategy', 'FSupertrendStrategy', 'TrendFollowingStrategy', 'VolatilitySystem']
SKIP = {'DoesNothingStrategy', 'Freqtrade_backtest_validation_freqtrade1', 'TWAPStrategy', 'AlmgrenChrissStrategy'}
names = subprocess.run(['ftvenv/bin/freqtrade', 'list-strategies', '--userdir', 'ft', '-c', 'ft/config.json', '-1'], capture_output=True, text=True, env=env).stdout.split()
spot = [n for n in names if n not in SKIP and n not in FUT]
insts = json.load(open('/home/user/okx_data/universe_5m40.json'))
PER = {'第一年': '20240929-20250929', '第二年': '20250929-20260930'}
done = set(os.listdir('ft/bt_results')) if os.path.isdir('ft/bt_results') else set()
os.makedirs('ft/bt_results', exist_ok=True)
for mode, lst in (('spot', spot), ('futures', FUT)):
    pairs = [i.split('-')[0] + ('/USDT' if mode == 'spot' else '/USDT:USDT') for i in insts]
    cfg = 'ft/config.json' if mode == 'spot' else 'ft/config_futures.json'
    for s in lst:
        for pn, tr in PER.items():
            tag = f'{s}__{pn}'
            if tag in done: continue
            d = f'ft/bt_results/{tag}'; os.makedirs(d + '.tmp', exist_ok=True)
            r = subprocess.run(['nice', '-n', '5', 'ftvenv/bin/freqtrade', 'backtesting', '-c', cfg, '--userdir', 'ft', '--strategy', s,
                                '--timerange', tr, '--export', 'trades', '--backtest-directory', d + '.tmp', '-p', *pairs],
                               capture_output=True, text=True, env=env)
            open(d + '.tmp/log.txt', 'w').write(r.stdout[-20000:] + r.stderr[-20000:])
            os.rename(d + '.tmp', d); print(tag, r.returncode, flush=True)
print('DONE', flush=True)
