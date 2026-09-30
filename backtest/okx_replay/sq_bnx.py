"""币安独有币验证：压缩释放突破（1小时，回测设置）的交易 + 开仓前一刻的币安数据：
Delta（突破K主动买卖差，来自币安K线）、持仓量4h/24h变化、主动买卖比、盘口±2%、大户/全体多空比、资金费率。
对比：不过滤 / A 只加持仓量24h增加 / C 持仓量24h + 7项打分调仓；各看胜率、盈亏比、每份收益、按季度、按币。"""
import glob, json, os
import numpy as np, pandas as pd
from concurrent.futures import ThreadPoolExecutor
import types
SB = types.ModuleType('SB')                  # 复用 sq_binance 里的 metrics / depth / funding（只取函数部分，不跑它的主流程）
_src = open('sq_binance.py', encoding='utf-8').read(); exec(_src[:_src.index("T = pd.read_csv('sq_flow.csv')")], SB.__dict__)
H = 3600000
rows = []
for fn in glob.glob('sq_cache_bnx_1h_default/*.json'):
    s = os.path.basename(fn)[:-5]
    for t in json.load(open(fn))['trades']: rows.append(dict(sym=s, **t))
T = pd.DataFrame(rows); print('交易', len(T), '币', T.sym.nunique(), flush=True)
K = {}


def one(r):
    s = r['sym']; ms = int(r['opened_ms']); d = 1 if r['side'] == 'long' else -1
    if s not in K:
        z = np.load(f'{s}_bn1h.npz'); K[s] = dict(zip(z['ts'].tolist(), zip(z['volume'].tolist(), z['tbuy'].tolist())))
    v, tb = K[s].get(ms - H, (0, 0)); out = dict(delta=((2 * tb - v) / v * d) if v > 0 else np.nan)
    m = SB.metrics(s, ms)
    if m is not None and len(m) > 10:
        oi = m.sum_open_interest_value.values.astype(float); t = m.t.values
        def at(dt):
            i = np.searchsorted(t, ms - dt) - 1
            return oi[i] if i >= 0 and oi[i] > 0 else np.nan
        out['oi4'] = oi[-1] / at(4 * H) - 1; out['oi24'] = oi[-1] / at(24 * H) - 1
        last = m[m.t >= ms - H]
        out['taker'] = np.log(last.sum_taker_long_short_vol_ratio.astype(float).mean()) * d if len(last) else np.nan
        out['top_ls'] = np.log(float(m.count_toptrader_long_short_ratio.iloc[-1])) * d
        out['all_ls'] = np.log(float(m.count_long_short_ratio.iloc[-1])) * d
    dp = SB.depth(s, ms)
    out['dep2'] = dp.get('dep2', np.nan) * d if dp else np.nan
    fr = SB.funding(s, ms); out['fund'] = fr * d if fr is not None else np.nan
    return out


with ThreadPoolExecutor(8) as ex: F = pd.DataFrame(list(ex.map(one, [r for _, r in T.iterrows()])))
X = pd.concat([T.reset_index(drop=True), F], axis=1); X['r'] = X.r.clip(-3, 50)
X.to_csv('sq_bnx_feats.csv', index=False)
X['q'] = pd.to_datetime(X.opened_ms, unit='ms').dt.to_period('Q').astype(str)
C = [X.delta > .05, X.oi4 > 0, X.taker > 0, X.dep2 > 0, X.top_ls > 0, X.all_ls < 0, X.fund <= 0]
X['score'] = sum(c.fillna(False).astype(int) for c in C)
m = {0: 0, 1: 0, 2: 0, 3: 0, 4: 1, 5: 2, 6: 2, 7: 2}
P = {'不过滤': np.ones(len(X)), 'A 只加持仓量24h增加': (X.oi24 > 0).astype(float).values,
     'C 持仓量24h增加+打分调仓': ((X.oi24 > 0) * X.score.map(m)).values}
print('有持仓量数据的交易', int(X.oi24.notna().sum()))
out = []
for n, w in P.items():
    sel = w > 0; r = X.r.values; pr = w * r; cs = pd.Series(pr[sel]).groupby(X.sym.values[sel]).sum()
    win = r[sel][r[sel] > 0].mean(); los = -r[sel][r[sel] <= 0].mean()
    out.append(dict(方案=n, 笔数=int(sel.sum()), 胜率=f'{(r[sel] > 0).mean() * 100:.0f}%', 盈亏比=round(win / los, 2),
                    每份风险=round(pr.sum() / w.sum(), 3), 总份=round(pr.sum(), 1),
                    去掉最好5笔=round((pr.sum() - np.sort(pr)[-5:].sum()) / max(w.sum() - 5, 1), 3), 赚钱的币=f'{(cs > 0).sum()}/{len(cs)}'))
pd.set_option('display.width', 300)
print(pd.DataFrame(out).to_string(index=False))
print('\n按季度（总份）：')
print(pd.DataFrame({n: pd.Series(w * X.r.values).groupby(X.q.values).sum().round(1) for n, w in P.items()}).T.to_string())
