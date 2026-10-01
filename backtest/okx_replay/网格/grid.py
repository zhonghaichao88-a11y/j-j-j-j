"""现货网格机器人模拟（欧易/币安“现货网格”那种）：用 1 小时 K 线，无前视。
设定：价格区间 = 开始价 ±W，等比 N 格；开始时把“开始价以上那些格”对应的币买好（一半左右资金买币）。
价格每跌过一格买一份、涨过上一格卖一份，每份赚一格的差价减两边手续费。超出区间就停在那（持有币或全是钱）。
每隔 R 天按当时价格重开一次（把币按市价卖掉再重新建网格），R=0 表示不重开。
K 线内走法：阳线按 开→低→高→收，阴线按 开→高→低→收（偏保守）。对照：同期买入持有。"""
import numpy as np, pandas as pd, glob, os
D = '/home/user/okx_data'
def npz(p): z = np.load(p); return {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close')}

def run(f, W=0.2, N=20, R=30, fee=0.001, cap=1000.0):
    ts, o, h, l, c = f['ts'], f['open'], f['high'], f['low'], f['close']
    cash = cap; eq = []; fills = 0
    def setup(p, cash):
        lv = p * np.exp(np.linspace(np.log(1 - W), np.log(1 + W), N + 1))
        per = cash / N                                   # 每格资金
        held = lv[1:] > p                                 # 开始价以上的格：先持有一份币（等到涨到那格卖）
        qty = np.where(held, per / p, 0.0)
        cash -= (qty * p).sum() * (1 + fee)
        return lv, per, qty, cash
    lv, per, qty, cash = setup(o[0], cash); start = ts[0]; last = o[0]
    def walk(a, b):
        nonlocal cash, fills
        if b < a:   # 下跌：跌破格 i 的价位 → 在格 i 买一份（对应卖在 i+1）
            for i in range(N):
                if qty[i] == 0 and b <= lv[i] < a:
                    q = per / lv[i]; qty[i] = q; cash -= q * lv[i] * (1 + fee); fills += 1
        else:
            for i in range(N):
                if qty[i] > 0 and a < lv[i + 1] <= b:
                    cash += qty[i] * lv[i + 1] * (1 - fee); qty[i] = 0; fills += 1
    for k in range(len(ts)):
        if R and ts[k] - start >= R * 86400000:
            cash += (qty * o[k]).sum() * (1 - fee); qty[:] = 0
            lv, per, qty, cash = setup(o[k], cash); start = ts[k]; last = o[k]
        path = (o[k], l[k], h[k], c[k]) if c[k] >= o[k] else (o[k], h[k], l[k], c[k])
        p0 = last
        for p in path: walk(p0, p); p0 = p
        last = c[k]
        eq.append(cash + (qty * c[k]).sum())
    return pd.Series(eq, index=pd.to_datetime(ts, unit='ms')), fills

COINS = {'更早(2022-2024)': ('bnold1h', ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'BNBUSDT', 'ADAUSDT', 'LINKUSDT', 'AVAXUSDT', 'LTCUSDT']),
         '最近(2024-06~2026-09)': ('bntop', ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'BNBUSDT', 'ADAUSDT', 'LINKUSDT', 'AVAXUSDT', 'LTCUSDT'])}
rows = []
for per_name, (kind, coins) in COINS.items():
    for coin in coins:
        p = f'{D}/{coin}_bnold1h.npz' if kind == 'bnold1h' else f'{D}/bntop/{coin}.npz'
        if not os.path.exists(p): continue
        f = npz(p)
        if kind == 'bntop':
            m = f['ts'] >= int(pd.Timestamp('2024-06-01').value // 10**6); f = {k: v[m] for k, v in f.items()}
        else:
            m = f['ts'] >= int(pd.Timestamp('2022-01-01').value // 10**6); f = {k: v[m] for k, v in f.items()}
        bh = f['close'][-1] / f['open'][0] - 1
        for W, N, R in ((0.1, 20, 30), (0.2, 20, 30), (0.2, 40, 30), (0.3, 30, 90), (0.2, 20, 0)):
            for fee in (0.0005, 0.001):
                e, n = run(f, W, N, R, fee)
                yrs = (e.index[-1] - e.index[0]).days / 365
                rows.append(dict(时期=per_name, 币=coin, 区间=f'±{int(W*100)}%', 格数=N, 重开=f'{R}天' if R else '不重开', 手续费=fee,
                                 网格年化=(e.iloc[-1] / 1000) ** (1 / yrs) - 1, 持有年化=(1 + bh) ** (1 / yrs) - 1,
                                 网格回撤=(1 - e / e.cummax()).max(), 成交=n))
    print(per_name, 'ok', flush=True)
R = pd.DataFrame(rows); R.to_csv('grid_results.csv', index=False)
g = R.groupby(['时期', '区间', '格数', '重开', '手续费']).agg(网格年化中位=('网格年化', 'median'), 持有年化中位=('持有年化', 'median'),
      网格赚钱币数=('网格年化', lambda s: (s > 0).sum()), 比持有好=('网格年化', 'size'), 回撤中位=('网格回撤', 'median'))
g['比持有好'] = R.groupby(['时期', '区间', '格数', '重开', '手续费']).apply(lambda x: (x.网格年化 > x.持有年化).sum())
pd.set_option('display.width', 250); print((g * [100, 100, 1, 1, 100]).round(1).to_string())
