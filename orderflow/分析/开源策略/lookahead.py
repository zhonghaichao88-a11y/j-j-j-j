"""对初筛排前面的策略跑 freqtrade lookahead-analysis（偷看未来数据检测）。用法：python lookahead.py 阶段 策略id...（id = 初筛目录名）"""
import os, sys, json, subprocess, re
RUN = '/home/user/ext/ftrun'; FT = '/home/user/ext/ftvenv/bin/freqtrade'
stage = sys.argv[1]
for sid in sys.argv[2:]:
    wd = f'{RUN}/{stage}/{sid}'; d = json.load(open(f'{wd}/done.json')); c = json.load(open(f'{wd}/c.json'))
    c['exchange']['pair_whitelist'] = c['exchange']['pair_whitelist'][:int(os.environ.get('LA_PAIRS', 5))]
    json.dump(c, open(f'{wd}/c_la.json', 'w'))
    p = subprocess.run([FT, 'lookahead-analysis', '-c', f'{wd}/c_la.json', '--userdir', '/home/user/ext/ft',
                        '--datadir', '/home/user/ext/ft/data_long/okx' if c['trading_mode'] == 'futures' else '/home/user/ext/ft/data_spot/okx',
                        '--strategy', d['cls'], '--strategy-path', f'{wd}/s', '--timerange', os.environ.get('LA_TR', '20250101-20250701'),
                        '--minimum-trade-amount', '10', '--targeted-trade-amount', '20'], capture_output=True, text=True, timeout=3600, cwd=wd)
    out = p.stdout + p.stderr
    open(f'{wd}/lookahead_out.txt', 'w').write(out[-20000:])
    tab = [l for l in out.split('\n') if '│' in l or 'bias' in l.lower()][-6:]
    if re.search(r'no bias detected', out): verdict = False
    elif re.search(r'less than minimum_trade_amount', out): verdict = '单太少没测'
    elif re.search(r'│\s*True\s*│', out) or re.search(r'(?<!no )bias detected', out): verdict = True
    else: verdict = '?'
    json.dump({'has_bias': verdict, 'table': tab[-4:]}, open(f'{wd}/lookahead.json', 'w'), ensure_ascii=False)
    print(d['cls'], '偷看未来:', verdict, flush=True)
    for l in tab[-4:]: print('   ', l[:200])
