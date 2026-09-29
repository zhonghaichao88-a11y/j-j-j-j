"""全参数网格评估。第一年挑选、第二年检验，并给出“挑参数是否有用”的整体证据。"""
import itertools, json, os, sys
import numpy as np, pandas as pd
from scipy import stats

df = pd.read_parquet(sys.argv[1] if len(sys.argv) > 1 else 'grid_rows.parquet')
split = df.ts.min() + (df.ts.max() - df.ts.min()) // 2
tr_mask = (df.ts < split).values
STRUCT = sorted(df[['pen', 'macd', 'ratio', 'seg']].drop_duplicates().itertuples(index=False, name=None))
LAYERS = ('三层', '大级别+小级别', '中级别+小级别')
BIGK = {'只趋势1类': ('T1',), '1类含盘背': ('T1', 'T1P'), '1类+3类': ('T1', 'T1P', 'T3')}
SMALL = {'小级别只趋势背驰': ('T1',), '小级别含盘背': ('T1', 'T1P')}
STOPS = (0.25, 0.5, 1.0)
EXITS = ('X1', 'X2', 'X3', 'X1_P', 'X1_T', 'X1_PT')
RS = {'不选币': None, '强势前50%': 0.5, '强势前30%': 0.7}
BR = {'不看宽度': None, '宽度>50%': 0.5, '宽度>60%': 0.6}
SIDE = {'多空': None, '只做多': 1}
EXIT_NAME = {'X1': '中级别卖点出', 'X2': '大级别卖点出', 'X3': '走势终完美+移动止损', 'X1_P': '中级别卖点出+分批', 'X1_T': '中级别卖点出+做差价', 'X1_PT': '中级别卖点出+分批+做差价'}


def trimmed(y): return stats.trim_mean(y, 0.01) if len(y) else np.nan


results = []
for s in STRUCT:
    sm = ((df.pen == s[0]) & (df.macd == s[1]) & (df.ratio == s[2]) & (df.seg == s[3])).values
    base = {}
    for lay, (bk, bks), (sk, sks), zero in itertools.product(LAYERS, BIGK.items(), SMALL.items(), (False, True)):
        if lay == '三层': m = df.big.isin(bks) & df.mid.isin(('T1', 'T1P')) & (df.big_zero if zero else True)
        elif lay == '大级别+小级别': m = df.big.isin(bks) & (df.big_zero if zero else True)
        else: m = df.mid.isin(bks) & (df.mid_zero if zero else True)
        base[(lay, bk, sk, zero)] = sm & m.values & df.small.isin(sks).values
    for key, bm in base.items():
        if bm.sum() < 20: continue
        for (rn, rv), (bn, bv), (sn, sv) in itertools.product(RS.items(), BR.items(), SIDE.items()):
            m = bm.copy()
            if rv is not None: m &= (df.rs >= rv).values
            if bv is not None: m &= (df.breadth >= bv).values
            if sv is not None: m &= (df.side == sv).values
            if m.sum() < 20: continue
            for sb, ex in itertools.product(STOPS, EXITS):
                col = f's{sb}_{ex}'
                y = (df[col] - df[col + '_c']).values
                ok = m & np.isfinite(y)
                a = y[ok & tr_mask]; b = y[ok & ~tr_mask]
                results.append(dict(结构=f"{'宽松线段' if s[3] else '严格线段'}/{'新笔' if s[0] else '老笔'}/{'同向' if s[1] == 'same' else '绝对值'}/{s[2]}",
                                    层数=key[0], 大级别点=key[1], 小级别=key[2], MACD回0轴=key[3], 选币=rn, 宽度=bn, 方向=sn,
                                    止损ATR=sb, 出场=EXIT_NAME[ex], n1=len(a), m1=a.mean() if len(a) else np.nan, t1=trimmed(a),
                                    n2=len(b), m2=b.mean() if len(b) else np.nan, t2=trimmed(b), med2=np.median(b) if len(b) else np.nan))
R = pd.DataFrame(results)
R.to_parquet('grid_results.parquet')
ok = R[(R.n1 >= 60) & (R.n2 >= 30)]
if ok.empty:
    print('样本不足：没有两年都达到最少笔数的组合'); sys.exit(0)
print(f'分段 {pd.to_datetime(split, unit="ms").date()}；共测试 {len(R)} 个参数组合，其中两年样本都够的 {len(ok)} 个')
print(f'第一年截尾平均为正的组合比例 {np.mean(ok.t1 > 0) * 100:.1f}%；第二年为正的比例 {np.mean(ok.t2 > 0) * 100:.1f}%')
rho = stats.spearmanr(ok.t1, ok.t2).correlation
print(f'第一年排名与第二年排名的相关系数 {rho:+.2f}（接近 0 = 第一年挑得好不代表第二年好）')
top = ok.sort_values('t1', ascending=False)
print('\n第一年最好的 20 个组合，在第二年的表现（扣成本后，R）：')
cols = ['结构', '层数', '大级别点', '小级别', 'MACD回0轴', '选币', '宽度', '方向', '止损ATR', '出场', 'n1', 't1', 'n2', 't2', 'med2']
with pd.option_context('display.width', 250, 'display.max_columns', 30):
    print(top[cols].head(20).round(3).to_string(index=False))
pick = top.iloc[0]
print('\n【事先规则选中（第一年截尾平均最高、n≥60）】')
print(pick[cols].to_string())
print(f'第一年最好 20 个组合在第二年：为正 {int((top.head(20).t2 > 0).sum())}/20，平均 {top.head(20).t2.mean():+.3f}R')
print(f'第一年最好 100 个组合在第二年：为正 {int((top.head(100).t2 > 0).sum())}/100，平均 {top.head(100).t2.mean():+.3f}R')
for k in ('层数', '出场', '止损ATR', '结构', '选币', '宽度', '方向', 'MACD回0轴', '大级别点', '小级别'):
    g = ok.groupby(k)[['t1', 't2']].median()
    print(f'\n按「{k}」分组的中位数（第一年 / 第二年）：' + '；'.join(f'{i}: {r.t1:+.3f} / {r.t2:+.3f}' for i, r in g.iterrows()))
