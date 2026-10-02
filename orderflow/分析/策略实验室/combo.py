"""几个打法合在一起跑组合：看加进新打法后，收益和回撤是变好还是变差"""
import pandas as pd, runner, report, importlib
def trades(mod, k=0):
    S = importlib.import_module(mod); S.GRID = [S.GRID[k]]
    res, _, _ = runner.run(mod); return res[0]['T']
A = trades('s00_flush'); B = trades('s00_squeeze'); C = pd.read_parquet('results/trades_s07_dayflush_2.parquet')
A.to_parquet('results/trades_flush.parquet'); B.to_parquet('results/trades_squeeze.parquet')
# 同一个币同一时间只能有一单：合并后按进场时间去掉重叠的
def merge(*ts):
    T = pd.concat(ts).sort_values('t_in')
    keep, busy = [], {}
    for i, r in T.iterrows() if False else enumerate(T.itertuples()):
        if r.t_in >= busy.get(r.coin, 0):
            keep.append(i); busy[r.coin] = r.t_out
    return T.iloc[keep]
for name, T in (('清洗接盘+轧空追多（现在的）', merge(A, B)), ('+多日大清洗', merge(A, B, C))):
    for size, cap in ((0.10, 10), (0.05, 10)):
        print(name, f'每笔{size:.0%} 最多{cap}单', len(T), '笔', report.portfolio(T, size, cap))
# 多日大清洗和清洗接盘是不是同一天出手
d1 = set(A.t // 86_400_000); d3 = set(C.t // 86_400_000)
print('多日大清洗出手的日子里，清洗接盘也出手的比例', round(len(d1 & d3) / len(d3) * 100), '%')
