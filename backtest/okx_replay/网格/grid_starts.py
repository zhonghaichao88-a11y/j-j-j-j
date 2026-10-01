import numpy as np, pandas as pd
exec(open('grid.py', encoding='utf-8').read().split("COINS = {")[0])
rows = []
for kind, coins, a, b in (('bnold1h', ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'BNBUSDT', 'ADAUSDT', 'LINKUSDT', 'AVAXUSDT', 'LTCUSDT'], '2022-01-01', '2024-03-01'),
                          ('bntop', ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'BNBUSDT', 'ADAUSDT', 'LINKUSDT', 'AVAXUSDT', 'LTCUSDT'], '2024-06-01', '2026-03-01')):
    for coin in coins:
        p = f'{D}/{coin}_bnold1h.npz' if kind == 'bnold1h' else f'{D}/bntop/{coin}.npz'
        F = npz(p)
        for st in pd.date_range(a, b, freq='MS'):
            s0 = int(st.value // 10**6); s1 = s0 + 182 * 86400000
            m = (F['ts'] >= s0) & (F['ts'] < s1)
            if m.sum() < 4000: continue
            f = {k: v[m] for k, v in F.items()}
            e, n = run(f, 0.2, 20, 0, 0.001)
            rows.append(dict(币=coin, 开始=st.date(), 网格=e.iloc[-1] / 1000 - 1, 持有=f['close'][-1] / f['open'][0] - 1, 回撤=(1 - e / e.cummax()).max()))
R = pd.DataFrame(rows)
print('半年一段，共', len(R), '段（10个主流币 × 每月一个开始时间）')
print('网格赚钱的段占比', f"{(R.网格 > 0).mean()*100:.0f}%", ' 网格比持有好的占比', f"{(R.网格 > R.持有).mean()*100:.0f}%")
print('网格半年收益：中位', f"{R.网格.median()*100:+.1f}%", '平均', f"{R.网格.mean()*100:+.1f}%", '最差', f"{R.网格.min()*100:+.1f}%", '最好', f"{R.网格.max()*100:+.1f}%")
print('持有半年收益：中位', f"{R.持有.median()*100:+.1f}%", '平均', f"{R.持有.mean()*100:+.1f}%")
print('网格回撤中位', f"{R.回撤.median()*100:.0f}%")
R.to_csv('grid_starts.csv', index=False)
