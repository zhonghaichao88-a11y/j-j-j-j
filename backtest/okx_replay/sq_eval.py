"""压缩释放突破验证汇总：每个 数据集×设置 的笔数/胜率/平均R/去掉最好5笔/前后半段/盈利币/组合(最多10仓,每笔1%)。"""
import glob, json, os
import pandas as pd
rows = []
LBL = {'old40_1h': '老40币·1小时(约一年)', 'new30_1h': '新30币·1小时(一年)', 'rest34_1h': '另34币·1小时(半年~一年)', 'old40_5m': '老40币·5分钟(两年)', 'new30_5m': '新30币·5分钟(一年)'}
SL = {'default': '回测设置(趋势仓开)', 'page': '你页面设置(三项关)'}
for ds in LBL:
    for st in SL:
        fs = glob.glob(f'sq_cache_{ds}_{st}/*.json')
        T = pd.DataFrame([t for f in fs for t in json.load(open(f))['trades']])
        if T.empty: continue
        T = T.sort_values('opened_ms'); r = T.r.clip(-3, 50)
        mid = T.opened_ms.min() + (T.opened_ms.max() - T.opened_ms.min()) // 2
        a, b = r[T.opened_ms < mid], r[T.opened_ms >= mid]; coin = T.groupby('symbol').r.sum()
        eq = peak = mdd = 0.0; op = []
        for _, t in T.iterrows():
            op = [c for c in op if c > t.opened_ms]
            if len(op) >= 10: continue
            op.append(t.closed_ms); eq += 0.01 * min(max(t.r, -3), 50); peak = max(peak, eq); mdd = max(mdd, peak - eq)
        rows.append(dict(数据=LBL[ds], 设置=SL[st], 币数=len(fs), 笔数=len(T), 胜率=f'{(T.r > 0).mean() * 100:.0f}%', 平均R=round(r.mean(), 3),
                         去掉最好5笔=round((r.sum() - r.nlargest(5).sum()) / max(1, len(r) - 5), 3), 前半=round(a.mean(), 3), 后半=round(b.mean(), 3),
                         盈利币=f'{(coin > 0).sum()}/{len(coin)}', 组合收益=f'{eq * 100:+.0f}%', 组合回撤=f'{mdd * 100:.0f}%'))
R = pd.DataFrame(rows); pd.set_option('display.width', 250)
print(R.to_string(index=False)); R.to_csv('sq_summary.csv', index=False)
