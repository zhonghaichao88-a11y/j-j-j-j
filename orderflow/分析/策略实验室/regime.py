"""大盘过滤：只在 BTC 收盘价高于它的 N 天均线（用前一天的日线收盘算，不偷看）时才做这些做多打法"""
import numpy as np, pandas as pd, data, report
b = data.load('BTC'); d = b.c.groupby(b.index.values // 86_400_000).last()
for N in (50, 100, 200):
    ma = d.rolling(N).mean()
    bull = (d > ma).shift(1)                      # 用到前一天收盘为止的数据
    for mod in ('s00_flush', 's00_squeeze', 's07_dayflush'):
        for tag in ('all', 'filtered'):
            T = pd.read_parquet(f'results/trades_{mod}_{tag}.parquet')
            ok = (T.t // 86_400_000).map(bull).fillna(False).astype(bool)
            sp = report.split(T[ok])
            print(f'MA{N} {mod} {"过滤" if tag == "filtered" else "不过滤"}: 留下 {ok.mean()*100:.0f}% | 训练PF {sp["训练2024"].get("PF")} 考试PF {sp["考试2025+"].get("PF")} 最近6个月 {sp["最近6个月"].get("笔数")}笔 PF {sp["最近6个月"].get("PF")}')
    print('  BTC 在均线上方的天数，最近6个月:', round(bull[bull.index >= 1775001600000 // 86_400_000].mean() * 100), '%')
