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
                           f'（开仓/平仓时间、平仓原因、收益率）。',
                 'realism': '更接近实盘的测法（freqtrade 用 1 分钟K线细化，30 个大币，2025-01~2026-09，7 个季度）：盈亏比 1.83 → 1.56，'
                            '每单平均 +0.76% → +0.52%。差别全在"移动止盈"：回测假设正好在止盈价成交，实际会晚一点、差一点。'
                            '实盘大概按这个打七折来看；本程序对有持仓的币用实时价格推送，尽量少差。'}
json.dump(out, open(OUT, 'w'), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1)[:1500])
