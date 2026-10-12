"""自己查"偷看未来"：同一个币，一次用全部K线算信号，另一次每次只给到当前这根K线为止再算（实盘就是这样），
比较每根K线的进场/出场信号是不是一样。不一样 = 信号用到了之后的K线（回测成绩不可信）。
用法：python lookahead_check.py 策略文件 策略类名 [币 抽查根数]"""
import sys, os, warnings
warnings.filterwarnings('ignore')
import pandas as pd, numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..', 'stratbot'))
import sb_loader  # noqa: E402
import inf_data  # noqa: E402
f, cls = sys.argv[1], sys.argv[2]
pair = sys.argv[3] if len(sys.argv) > 3 else 'BTC/USDT'
N = int(sys.argv[4]) if len(sys.argv) > 4 else 150
st = sb_loader.Strategy(cls, f, runmode='backtest')
d = pd.read_feather(f"/home/user/ext/ft/data_spot/okx/{pair.replace('/', '_')}-5m.feather")
d = d[(d.date >= '2024-01-01') & (d.date < '2024-03-01')].reset_index(drop=True)
t0, t1 = d.date.iloc[0], d.date.iloc[-1]
src = inf_data.okx_source(t0, t1, max(st.startup, 300), 'spot')
full = st.analyze(d, pair, inf=src)
start = max(st.startup + 300, 600)
ks = np.linspace(start, len(d) - 1, N).astype(int)
sigrows = np.where((full.enter_long == 1) | (full.exit_long == 1))[0]       # 有信号的K线一定要查（全部数据算出有信号的）
sigrows = sigrows[sigrows >= start][:N]
ks = np.unique(np.r_[ks, sigrows])
bad = []
for k in ks:
    part = st.analyze(d.iloc[:k + 1].reset_index(drop=True), pair, inf=lambda p, tf: (src(p, tf) or pd.DataFrame()).pipe(lambda x: x[x.date <= d.date.iloc[k]] if len(x) else None))
    a, b = full.iloc[k], part.iloc[-1]
    if int(a.enter_long) != int(b.enter_long) or int(a.exit_long) != int(b.exit_long):
        bad.append((str(d.date.iloc[k]), int(a.enter_long), int(b.enter_long), int(a.exit_long), int(b.exit_long)))
sig = int(full.enter_long.iloc[ks].sum() + full.exit_long.iloc[ks].sum())
print(f'{cls} {pair}：抽查 {len(ks)} 根K线（其中有信号 {sig} 根），信号不一样 {len(bad)} 根', '→ 偷看未来' if bad else '→ 没发现偷看')
for x in bad[:5]:
    print('  ', x, '（全部数据算的 进场/实时算的 进场/全部数据 出场/实时 出场）')
