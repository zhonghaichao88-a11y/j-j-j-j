"""V7 其余策略回放汇总：每个策略 笔数/胜率/平均R/去掉最好5笔/前后半段/盈利币数/组合(最多10笔,每笔1%)。"""
import json, glob, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, '/home/user/j-j-j-j'); os.environ.setdefault('ALPHA_V7_PARAMS_FILE', '/tmp/claude-0/bt_params_unused.json')
import alpha_fast_v7 as V
rows = []
for tf in ('15m', '1h'):
    fs = glob.glob(f'strat_cache_{tf}/*.json')
    if not fs: continue
    T = pd.DataFrame([t for f in fs for t in json.load(open(f))['trades']])
    if T.empty: continue
    mid = T.opened_ms.min() + (T.opened_ms.max() - T.opened_ms.min()) // 2
    for s, d in T.groupby('strategy'):
        d = d.sort_values('opened_ms'); r = d.r.clip(-3, 50)
        a, b = r[d.opened_ms < mid], r[d.opened_ms >= mid]
        coin = d.groupby('symbol').r.sum()
        # 组合：按开仓时间，最多同时 10 笔，每笔风险 1%
        eq = 0.0; peak = 0.0; mdd = 0.0; open_ = []
        for _, t in d.iterrows():
            open_ = [c for c in open_ if c > t.opened_ms]
            if len(open_) >= 10: continue
            open_.append(t.closed_ms); eq += 0.01 * min(max(t.r, -3), 50); peak = max(peak, eq); mdd = max(mdd, peak - eq)
        rows.append(dict(周期=tf, 策略=V.LABELS.get(s, s), 笔数=len(d), 胜率=f'{(d.r > 0).mean() * 100:.0f}%', 平均R=round(r.mean(), 3),
                         去掉最好5笔=round((r.sum() - r.nlargest(5).sum()) / max(1, len(r) - 5), 3),
                         前半=round(a.mean(), 3) if len(a) else None, 后半=round(b.mean(), 3) if len(b) else None,
                         盈利币=f'{(coin > 0).sum()}/{len(coin)}', 组合收益=f'{eq * 100:+.0f}%', 组合回撤=f'{mdd * 100:.0f}%'))
R = pd.DataFrame(rows).sort_values(['周期', '平均R'], ascending=[True, False])
pd.set_option('display.width', 250); pd.set_option('display.max_rows', 200)
print(R.to_string(index=False)); R.to_csv('strat_summary.csv', index=False)
