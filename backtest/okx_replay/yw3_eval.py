"""原文全套第三版的结果汇总 + 主观部分规则化：
大盘环境 = 市场宽度（日线收在 EMA50 上方的币占比，做空取反）≥ 50%；
板块/强势 = 30 日涨幅在全体币中的排名（做空取反）位于前 50%；
资金轮动 = 组合最多同时 5 组持仓，同一时刻多个信号优先相对强度高的；每组满仓风险 1%（每批 1/3%）。
资金费率（情绪，原文没有，自定替代）：入场前最近一次结算的费率；做多时 >0.01%（基础费率）算拥挤，做空时 <-0.01% 算拥挤。接口只有约3个月。
持仓量（资金进出，原文没有，自定替代）：入场前已结束的最近 3 天日线持仓量（币本位）变化 >0 算资金流入。"""
import json, os, numpy as np, pandas as pd
D = 86400000
cats = json.load(open('categories.json')); CRYPTO = [i for i, c in cats.items() if c == '1']


def daily(suf, ms):
    out = {}
    for inst in CRYPTO:
        p = f'{inst}_{suf}.npz'
        if not os.path.exists(p): continue
        z = np.load(p); ts = z['ts']; c = z['close']; b = ts // D
        last = np.flatnonzero(np.r_[b[1:] != b[:-1], True]); full = (ts[last] + ms) % D == 0
        dts = b[last][full] * D; dc = c[last][full]
        if len(dc) < 60: continue
        e = np.empty(len(dc)); e[0] = dc[0]
        for i in range(1, len(dc)): e[i] = e[i - 1] + 2 / 51 * (dc[i] - e[i - 1])
        out[inst] = (dts, dc, e)
    return out


def features(d, dly):
    rs = []; br = []
    for x in d.itertuples():
        rets = {}; above = []
        for inst, (ts, c, e) in dly.items():
            k = int(np.searchsorted(ts + D, x.start, side='right')) - 1
            if k >= 30: rets[inst] = c[k] / c[k - 30] - 1; above.append(c[k] > e[k])
        if x.inst in rets and len(rets) >= 10:
            v = sorted(rets.values()); rank = v.index(rets[x.inst]) / (len(v) - 1); b = float(np.mean(above))
            rs.append(rank if x.side == 1 else 1 - rank); br.append(b if x.side == 1 else 1 - b)
        else: rs.append(np.nan); br.append(np.nan)
    return d.assign(rs=rs, breadth=br)


FO = json.load(open('fund_oi.json')) if os.path.exists('fund_oi.json') else {}


def fund_oi(d):
    fund = []; oi3 = []
    for x in d.itertuples():
        g = FO.get(x.inst) or {}; fr = g.get('funding') or []; oi = g.get('oi') or []
        f = np.nan
        if fr and x.start >= fr[0][0] + D:
            k = int(np.searchsorted([t for t, _ in fr], x.start, side='right')) - 1
            if k >= 0: f = fr[k][1] * x.side
        fund.append(f)
        o = np.nan
        if oi:
            ts = np.array([t for t, _ in oi]); v = np.array([q for _, q in oi])
            k = int(np.searchsorted(ts + D, x.start, side='right')) - 1
            if k >= 3 and v[k - 3] > 0: o = v[k] / v[k - 3] - 1
        oi3.append(o)
    return d.assign(fund=fund, oi3=oi3)


def portfolio(d, cap=5, risk=0.01):
    d = d.sort_values(['start', 'rs'], ascending=[True, False]); open_ = []; eq = 1.0; peak = 1.0; dd = 0.0; events = []
    for x in d.itertuples():
        open_ = [e for e in open_ if e > x.start]
        if len(open_) >= cap: continue
        open_.append(x.end); events.append((x.end, x.pnl * risk))
    for t, g in sorted(events):
        eq *= 1 + g; peak = max(peak, eq); dd = max(dd, 1 - eq / peak)
    return eq - 1, dd, len(events)


FILT = {'不加主观': lambda d: np.ones(len(d), bool), '+大盘(宽度)': lambda d: d.breadth >= 0.5, '+强势(相对强度)': lambda d: d.rs >= 0.5,
        '+大盘+强势': lambda d: (d.breadth >= 0.5) & (d.rs >= 0.5),
        '+持仓量流入': lambda d: d.oi3 > 0, '(对照)持仓量流出': lambda d: d.oi3 <= 0,
        '有资金费率数据·不加': lambda d: d.fund.notna(), '+资金费率不拥挤': lambda d: d.fund <= 0.0001, '(对照)资金费率拥挤': lambda d: d.fund > 0.0001,
        '全部主观(大盘+强势+持仓量流入)': lambda d: (d.breadth >= 0.5) & (d.rs >= 0.5) & (d.oi3 > 0),
        '全部主观+资金费率不拥挤': lambda d: (d.breadth >= 0.5) & (d.rs >= 0.5) & (d.oi3 > 0) & (d.fund <= 0.0001)}


def table(res, periods, dly):
    rows = []
    for name, v in res.items():
        d = pd.DataFrame(v)
        if d.empty: continue
        d = fund_oi(features(d, dly))
        for ptag, pm in periods(d):
            for fname, fm in FILT.items():
                x = d[np.asarray(pm) & np.asarray(fm(d), bool)]
                if len(x) < 5: continue
                used = x.used.sum(); ret, mdd, n = portfolio(x)
                rows.append(dict(规则=name, 主观=fname, 段=ptag, 持仓=len(x), 扣成本后=round(x.pnl.sum() / used, 3), 扣成本前=round(x.gross.sum() / used, 3),
                                 去掉最好5笔=round((x.pnl.sum() - x.pnl.nlargest(5).sum()) / used, 3), 胜率=f'{(x.pnl > 0).mean() * 100:.0f}%',
                                 组合收益=f'{ret * 100:+.0f}%', 组合回撤=f'{mdd * 100:.0f}%'))
    return pd.DataFrame(rows)


if __name__ == '__main__':
    ra = pd.DataFrame(json.load(open('research_15m_cx76.json'))); split = ra.ts.min() + (ra.ts.max() - ra.ts.min()) // 2
    out = []
    r = json.load(open('yuanwen3_15m.json')); out.append(table(r, lambda d: [('A 26年4–6月', d.start < split), ('B 26年7–9月', d.start >= split)], daily('15m', 900000)))
    r = json.load(open('yuanwen3_15m_old.json')); out.append(table(r, lambda d: [('C 25年9月–26年3月', d.start > 0)], daily('15m_old', 900000)))
    if os.path.exists('yuanwen3_5m2y.json'):
        r = json.load(open('yuanwen3_5m2y.json')); d0 = pd.DataFrame(r['同级别分解']); mid = d0.start.min() + (d0.start.max() - d0.start.min()) // 2
        out.append(table(r, lambda d: [('5m两年·第一年', d.start < mid), ('5m两年·第二年', d.start >= mid)], daily('5m2y', 300000)))
    R = pd.concat(out); R.to_csv('yuanwen3_summary.csv', index=False)
    pd.set_option('display.width', 250); pd.set_option('display.max_rows', 500)
    print(R.to_string(index=False))
