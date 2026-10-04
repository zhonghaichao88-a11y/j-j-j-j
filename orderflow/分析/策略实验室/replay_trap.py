"""核对：把历史 5 分钟K线（每根按 开-高-低-收 四笔成交）喂给程序的引擎（模拟盘），和回测（s29 选中参数）的单一笔笔对。
持仓量、资金费、散户多空比 z 都用同一份历史数据塞给程序（实盘时这些从欧易读；散户多空比实盘用 2 小时一个点算 7 天 z，和回测的 5 分钟口径略有差别）。"""
import sys, os, time, tempfile, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', '..'))); sys.path.insert(0, '/home/user/ext/long/lab')
import of_engine as E
import data, s28_short4y as B, s29_short4y_refine as S
tmp = tempfile.mkdtemp()
E.STATE_FILE, E.TRADE_LOG, E.LOG_FILE = f'{tmp}/s.json', f'{tmp}/t.jsonl', f'{tmp}/l.txt'
P = {'c': 'ls', 'fr': 0.02, 'fo': 0.03}
A, Z = pd.Timestamp('2025-10-01').value // 10**6, pd.Timestamp('2026-04-01').value // 10**6
z = lambda s: (s - s.rolling(288 * 7, min_periods=288).mean()) / s.rolling(288 * 7, min_periods=288).std()
rows = []
for coin in sys.argv[1:]:
    data.ROOT = '/home/user/ext/long'
    df = B.prep(data.load(coin)); df['ls_z'] = z(df.ls)
    bt = pd.DataFrame(S.run(df, P, coin), columns=['coin', 't', 'ret', 'why', 'bars'])
    bt = bt[(bt.t >= A) & (bt.t < Z)]
    tmp = tempfile.mkdtemp()                       # 每个币用自己的记录文件（不然会读到上一个币的成交）
    E.STATE_FILE, E.TRADE_LOG, E.LOG_FILE = f'{tmp}/s.json', f'{tmp}/t.jsonl', f'{tmp}/l.txt'
    E.OrderFlowApp.say = lambda self, *a, **k: None
    app = E.OrderFlowApp({"auto": True, "enabled": ["trap_short"], "btc_ma_days": 0, "flush_filters": {}, "max_positions": 10,
                          "daily_loss_pct": 100}, None, None, False)
    eng = E.SymbolEngine(app, f'{coin}-USDT-SWAP', '5m', {t: float(df.c.iloc[-1]) * 0.002 for t in E.VIEW_TFS}, 1.0)
    app.engines = {eng.inst: eng}
    sub = df[(df.index >= A - 2 * 86_400_000) & (df.index < Z)]
    oi24 = df.oi.pct_change(288)
    for t, r in sub.iterrows():
        now = time.time() * 1000
        app.oi24[eng.inst] = (oi24.get(t, np.nan), now)
        app.lsz[eng.inst] = (r.ls_z, now)
        eng.ext['funding'] = r.fund
        for k, px in enumerate((r.o, r.h, r.l, r.c)):
            eng.on_trade(float(px), 1.0, k % 2 == 0, int(t) + k * 60_000 + 1)
        h = eng.ext['oi_hist']; h.append((int(t) + 300_000, float(r.oi))); eng.ext['oi_hist'] = h[-40:]
    prog = pd.DataFrame([x for x in app.history if x['k'] == 'trap_short'])
    # 程序开仓时间 ≈ 回测信号K线 + 5 分钟
    pt = set(((prog.t_open // 300_000) * 300_000 - 300_000).astype(int)) if len(prog) else set()
    bset = set(bt.t.astype(int))
    both = pt & bset
    pr = prog.ret.values if len(prog) else np.array([])
    rows.append({'币': coin, '回测笔数': len(bset), '程序笔数': len(pt), '两边都有': len(both), '只在回测': len(bset - pt), '只在程序': len(pt - bset),
                 '回测每笔%': round(bt.ret.mean() * 100, 3) if len(bt) else None, '程序每笔%': round(pr.mean() * 100, 3) if len(pr) else None,
                 '回测平仓方式': bt.why.value_counts().to_dict(), '程序平仓方式': (prog.why.str.contains('多头被清洗').map({True: 'flush', False: None}).fillna(prog.why).value_counts().to_dict() if len(prog) else {})})
    print(rows[-1], flush=True)
    fmt = lambda t: pd.Timestamp(int(t), unit='ms').strftime('%m-%d %H:%M')
    print('   回测信号:', [(fmt(r.t), r.why, round(r.ret * 100, 2)) for r in bt.itertuples()])
    print('   程序开仓:', [(fmt(r.t_open), r.why[:6], round(r.ret * 100, 2)) for r in prog.itertuples()] if len(prog) else [])
pd.DataFrame(rows).to_csv(os.path.join(HERE, 'results', 'replay_trap.csv'), index=False, encoding='utf-8-sig')
