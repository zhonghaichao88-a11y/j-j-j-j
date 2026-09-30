"""压缩释放突破 + 真实订单流过滤：老40币设计，新30/另34验证。
delta = 突破K线主动买卖差 / 总成交（已按开仓方向取正负：>0 表示和突破方向一致）；delta_prev = 前3根平均。"""
import pandas as pd
T = pd.read_csv('sq_flow.csv')
FILT = {'不过滤': lambda d: d.r == d.r,
        '突破K主动成交同向(Delta>0)': lambda d: d.delta > 0,
        'Delta>0.05': lambda d: d.delta > 0.05, 'Delta>0.10': lambda d: d.delta > 0.10, 'Delta>0.20': lambda d: d.delta > 0.20,
        'Delta反向(<0，吸收/假突破)': lambda d: d.delta < 0,
        '前3根也同向': lambda d: (d.delta > 0) & (d.delta_prev > 0),
        'Delta>0.05+压缩≥12根': lambda d: (d.delta > 0.05) & (d.sql >= 12),
        'Delta>0.05+只做多': lambda d: (d.delta > 0.05) & (d.d > 0),
        'Delta>0.05+放量1.5': lambda d: (d.delta > 0.05) & (d.vr >= 1.5)}
out = []
for name, fn in FILT.items():
    row = dict(过滤=name)
    for g, lbl in (('old40', '老40(设计)'), ('new30', '新30(验证)'), ('rest34', '另34(验证)')):
        G = T[T.grp == g]; x = G[fn(G).values]; r = x.r.clip(-3, 50)
        row[lbl] = f'{len(x)}笔 胜{(x.r > 0).mean() * 100:.0f}% {r.mean():+.3f}' if len(x) else '0笔'
    x = T[fn(T).values]; r = x.r.clip(-3, 50)
    row['三批合计'] = f'{len(x)}笔 胜{(x.r > 0).mean() * 100:.0f}% {r.mean():+.3f}' if len(x) else '0笔'
    out.append(row)
pd.set_option('display.width', 250)
print(pd.DataFrame(out).to_string(index=False))
