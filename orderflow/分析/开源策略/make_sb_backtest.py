"""生成策略机网页上显示的回测成绩（stratbot/sb_backtest.json）：全币复测的每年结果 + 和 freqtrade 逐单对账的结果。
用法：python make_sb_backtest.py"""
import json, glob, os
import pandas as pd
RUN = '/home/user/ext/ftrun/full'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'stratbot', 'sb_backtest.json')
STRATS = {'ElliotV8_original_ichiv3': 'ddc545fbf0_ElliotV8_original_ichiv3'}
out = {}
for name, d in STRATS.items():
    years = {}
    for y in range(2021, 2027):
        f = f'{RUN}/{d}/{y}.json'
        if not os.path.exists(f):
            continue
        r = json.load(open(f))
        T = pd.concat([pd.read_csv(x) for x in sorted(glob.glob(f'{RUN}/{d}/trades{y}q*.csv'))])
        days = 272 if y == 2026 else 365
        years[str(y)] = {'n': r['n'], 'win': r['win'], 'pf': r['pf'], 'dd': r['dd'], 'per_day': round(len(T) / days, 1),
                         'hold_h': r.get('hold_h')}
    par = [json.load(open(f)) for f in sorted(glob.glob(f'{RUN}/{d}/parity20*.json'))]
    n_all, n_same = sum(p['ft'] for p in par), sum(p['same'] for p in par)
    out[name] = {'years': years,
                 'desc': 'freqtrade 回测：欧易 98 个币（现在还在交易的），最多同时 10 单、平分资金，每边手续费 0.07%，2021~2026 每季度分开跑',
                 'parity': f'本程序的回测引擎和 freqtrade 逐单对账：{len(par)} 个季度共 {n_all} 单，{n_same} 单完全一样'
                           f'（开仓/平仓时间、平仓原因、收益率）。注意：回测假设移动止盈正好在止盈价成交，实盘每 2 秒看一次价，会差一点。'}
json.dump(out, open(OUT, 'w'), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1)[:1500])
