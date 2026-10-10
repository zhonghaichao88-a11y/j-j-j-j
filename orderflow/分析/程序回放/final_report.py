"""程序回放最终汇总：每个打法 程序实跑成绩（全部 / 三段 / 分年）+ 和回测逐笔对照，写到 核对结果.md。"""
import os, sys, io, contextlib, glob, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import compare as C
NAMES = {'nfi5': 'NFI头部币急跌（5分钟）', 'nfi15': '头部币15分钟急跌', 'vn': '大跌抄底（按波动）', 'flush': '清洗接盘（默认：大盘过滤 + 持仓24h不涨）',
         'trapC': '多头摊平做空 C（默认）', 'trapB': '多头摊平做空 B', 'trapA': '多头摊平做空 A', 'trapO': '多头摊平做空 原版',
         'squeeze': '轧空追多', 'momo': '追强势币', 'sweep': '扫止损收回（5 笔补仓，按占用资金）'}
out = ['# 程序回放核对结果', '',
       '把历史 5 分钟K线拆成成交喂给订单流程序本身（模拟盘：吃单手续费 0.05% + 滑点 0.02%，和实盘一样是市价成交），程序自己下单平仓。',
       '每笔 10% 权益、最多 10 单、同币不重叠，100U 起算。数据：币安合约（持仓量等 2021-12 起；急跌抄底 2020 起）。', '']
for k, nm in NAMES.items():
    if not os.path.isdir(f'{C.R}/{k}'): continue
    buf = io.StringIO()
    sys.argv = ['compare.py', k]
    with contextlib.redirect_stdout(buf):
        if k == 'sweep':
            exec(open(os.path.join(HERE, 'sweep_cap.py')).read(), {'__name__': '__main__', '__file__': os.path.join(HERE, 'sweep_cap.py')})
        else:
            exec(open(os.path.join(HERE, 'compare.py')).read(), {'__name__': '__main__', '__file__': os.path.join(HERE, 'compare.py')})
    out += [f'## {nm}', '```', buf.getvalue().strip(), '```', '']
open(os.path.join(HERE, '核对结果.md'), 'w').write('\n'.join(out)); print('\n'.join(out))
