"""核对：实盘模块 alpha_v7_scheme2 的逐根状态机 与 研究脚本 yuanwen4（三层_日线_30m_5m_实战只做二买）逐笔一致。
用同一份 5m2y 数据、同样的全历史结构；实盘模块由模拟持仓执行它给出的动作（开仓/加仓/减半/清仓），止损在 5 分钟逐根检查。
大盘宽度设为 0.5（多空都放行；研究里宽度是事后筛选，这里比较未筛选的全部持仓）。
用法: python3 verify_scheme2_port.py BTC-USDT-SWAP ETH-USDT-SWAP ...
"""
import os, sys
sys.path.insert(0, os.environ.get('ALPHA_REPO', '/home/user/j-j-j-j'))
DATA = os.environ.get('OKX_DATA', '/home/user/okx_data'); sys.path.insert(0, DATA); os.chdir(DATA)
sys.argv = [sys.argv[0], '5m'] + sys.argv[1:]
import numpy as np
import yuanwen4 as Y
import alpha_v7_scheme2 as S2

VAR = '三层_日线_30m_5m_实战只做二买'


def live_port(inst):
    frames = Y.load(inst)
    lv = {tf: S2.Level(frames[tf], S2.MSOF[tf]) for tf in S2.TFS}
    f = frames['5m']; ts = f['ts']; o, h, l, c = f['open'], f['high'], f['low'], f['close']
    X = S2._new_state(); pos = None; trades = []
    for i in range(len(ts)):
        T = int(ts[i]) + S2.M5
        if pos:
            d = pos['d']; adv = l[i] if d == 1 else h[i]
            if d * (adv - pos['stop']) <= 0:
                trades.append((pos['start'], d, pos['stages'], '止损')); pos = None
        view = dict(side='long' if pos['d'] == 1 else 'short', s2_stages=pos['stages'], s2_sold1=pos['sold1']) if pos else None
        out = S2._step(X, lv, i, T, view, 0.5, True)
        a = out.get('action')
        if pos and a:
            if a['type'] == 'close':
                trades.append((pos['start'], pos['d'], pos['stages'], a['reason'].replace('方案二：', '').replace('，清仓', ''))); pos = None
            elif a['type'] == 'reduce_half': pos['sold1'] = a['sold1']
            elif a['type'] == 'mark_sold1': pos['sold1'] = a['price']
            elif a['type'] == 'add': pos['stages'] += a['stage']; pos['stop'] = a['stop']
        if not pos and out.get('entry'):
            e = out['entry']; pos = dict(d=e['side'], stages=e['stage'], stop=e['stop'], sold1=None, start=T)
    if pos: trades.append((pos['start'], pos['d'], pos['stages'], '数据结束'))
    return trades


def research(inst):
    Y.VARIANTS = {VAR: Y.V5[VAR]}
    out = Y.one(inst)
    return [(r['start'], r['side'], r['stages'], r['exit']) for r in out[VAR]]


if __name__ == '__main__':
    total = same = 0
    for inst in sys.argv[2:]:
        a = live_port(inst); b = research(inst)
        sa, sb = set(a), set(b); total += len(sb); same += len(sa & sb)
        print(f'{inst}: 研究 {len(b)} 笔，实盘模块 {len(a)} 笔，完全一致 {len(sa & sb)} 笔')
        for x in sorted(sa ^ sb)[:6]: print('   不一致:', '研究' if x in sb else '实盘', x)
    print(f'合计：研究 {total} 笔，一致 {same} 笔')
