"""订单流做空 第七轮：四年多数据（2022-01~2023-12 币安 38 币 + 2024-01~2026-09 币安 111 币），看结果前写好。
进场（下一根 5 分钟K线市价做空）：
  E1 多头摊平：24 小时跌超 5%、持仓量 24 小时涨超 o、资金费率 > 0
  E2 杠杆推涨现货不跟：24 小时涨超 5%、持仓量 24 小时涨超 o、现货 24 小时主动卖多于买、资金费率 > 0
  E3 杠杆堆着不涨：持仓量 24 小时涨超 o、24 小时涨跌在 ±2% 以内、资金费率 > 0
平仓：
  X1 固定拿 24 小时
  X2 看到多头被清洗就平（1 小时跌超 2% 且持仓量 1 小时降超 3%，那根收盘平），最多拿 48 小时
止损 sd（K线内触发）。同一个币拿着时不再开。
分 5 段看：2022 / 2023 / 2024 / 2025 上半年 / 2025-07 以后。
过关：总 PF ≥ 1.15、5 段里至少 4 段 PF ≥ 1、比同条件随机做空好、去掉最赚 5 个币 PF > 1.05。
挑参数：看 5 段 PF 的中位数，不看最好的那段。"""
import sys, os, json, numpy as np, pandas as pd
import data, report
from data import flow
FEE, SLIP, SSLIP = 0.0005, 0.0002, 0.0005
GRID = [{'e': e, 'o': o, 'x': x, 'sd': sd} for e in ('E1', 'E2', 'E3') for o in (0.05, 0.10) for x in ('X1', 'X2') for sd in (0.05, 0.10)]
SEG = [('2022', '2022-01-01', '2023-01-01'), ('2023', '2023-01-01', '2024-01-01'), ('2024', '2024-01-01', '2025-01-01'),
       ('2025上', '2025-01-01', '2025-07-01'), ('2025下~', '2025-07-01', '2027-01-01')]


def prep(df):
    df['r24h'] = df.c.pct_change(288); df['r60'] = df.c.pct_change(12)
    df['oi24h'] = df.oi.pct_change(288); df['oi60'] = df.oi.pct_change(12)
    df['sf24h'] = flow(df.sbq, df.sqv, 288)
    return df


def entries(df, p):
    o = p['o']
    if p['e'] == 'E1':
        m = (df.r24h < -0.05) & (df.oi24h > o) & (df.fund > 0)
    elif p['e'] == 'E2':
        m = (df.r24h > 0.05) & (df.oi24h > o) & (df.sf24h < 0) & (df.fund > 0)
    else:
        m = (df.oi24h > o) & (df.r24h.abs() < 0.02) & (df.fund > 0)
    return m.fillna(False).values


def run(df, sig_mask, p, coin):
    O, H, L, C = df.o.values, df.h.values, df.l.values, df.c.values
    T = df.index.values
    flush = ((df.r60 < -0.02) & (df.oi60 < -0.03)).fillna(False).values
    fts, frate = df.attrs.get('fts', np.array([])), df.attrs.get('frate', np.array([]))
    n = len(O); out = []; busy = -1
    maxh = 288 if p['x'] == 'X1' else 576
    for i in np.flatnonzero(sig_mask):
        if i <= busy or i + 1 >= n:
            continue
        j0 = i + 1; e = O[j0] * (1 - SLIP); st = e * (1 + p['sd'])
        end = min(j0 + maxh - 1, n - 1); ex = None; why = 'time'
        for j in range(j0, end + 1):
            if H[j] >= st:
                ex = max(O[j], st) * (1 + SSLIP); why = 'stop'; break
            if p['x'] == 'X2' and flush[j] and j > j0:
                ex = C[j] * (1 + SLIP); why = 'flush'; break
        if ex is None:
            j = end; ex = C[j] * (1 + SLIP)
        fund = 0.0
        if len(fts):
            a, b = np.searchsorted(fts, T[j0], 'right'), np.searchsorted(fts, T[j], 'right')
            fund = -frate[a:b].sum()            # 空单：资金费为正时收钱
        ret = -(ex / e - 1) - 2 * FEE - fund
        out.append((coin, int(T[i]), ret, why, j - j0 + 1))
        busy = j
    return out


def load_set(root, coins):
    data.ROOT = root
    for c in coins:
        try:
            yield c, prep(data.load(c))
        except Exception as ex:
            print('跳过', c, ex)


if __name__ == '__main__':
    old = [c for c in open('/home/user/ext/oos/flush_old_coins.txt').read().split()]
    new = data.coins()
    res = {k: [] for k in range(len(GRID))}; rnd = {k: [] for k in range(len(GRID))}
    rng = np.random.default_rng(7)
    for root, coins in (('/home/user/ext/oos/f5', old), ('/home/user/ext/long', new)):
        for c, df in load_set(root, coins):
            for k, p in enumerate(GRID):
                m = entries(df, p)
                tr = run(df, m, p, c)
                res[k] += tr
                if tr:                          # 随机做空：同一个币、资金费 > 0 的时候随机挑时间，同样止损和平仓
                    cand = np.flatnonzero((df.fund > 0).fillna(False).values[:-600])
                    if len(cand):
                        rm = np.zeros(len(df), bool); rm[rng.choice(cand, size=min(len(cand), 3 * len(tr)), replace=False)] = True
                        rnd[k] += run(df, rm, p, c)
            print(root.split('/')[-1], c, flush=True)
    pf = report.pf
    rows = []
    for k, p in enumerate(GRID):
        T = pd.DataFrame(res[k], columns=['coin', 't', 'ret', 'why', 'bars'])
        R = pd.DataFrame(rnd[k], columns=['coin', 't', 'ret', 'why', 'bars'])
        if not len(T):
            continue
        segs = {}
        for nm, a, b in SEG:
            ta, tb = pd.Timestamp(a).value // 10**6, pd.Timestamp(b).value // 10**6
            X = T[(T.t >= ta) & (T.t < tb)]
            segs[nm] = (len(X), round(pf(X.ret), 2) if len(X) >= 10 else None)
        good = [v[1] for v in segs.values() if v[1] is not None]
        cs = T.groupby('coin').ret.sum().sort_values(ascending=False)
        top5 = round(pf(T[~T.coin.isin(cs.index[:5])].ret), 2)
        ok = (pf(T.ret) >= 1.15 and sum(v >= 1 for v in good) >= 4 and pf(T.ret) > pf(R.ret) and top5 > 1.05)
        rows.append({'参数': json.dumps(p), '笔数': len(T), 'PF': round(pf(T.ret), 2), '随机PF': round(pf(R.ret), 2) if len(R) else None,
                     '去前5币': top5, '分段中位': round(float(np.median(good)), 2) if good else None,
                     **{nm: f'{v[0]}/{v[1]}' for nm, v in segs.items()},
                     '清洗平仓占比': round((T.why == 'flush').mean(), 2), '过关': '✔' if ok else ''})
        T.to_parquet(f'/home/user/ext/long/lab/results/s28_{k}.parquet')
    D = pd.DataFrame(rows).sort_values('分段中位', ascending=False)
    txt = __doc__ + '\n\n' + D.to_markdown(index=False)
    open('/home/user/ext/long/lab/results/s28_short4y.md', 'w').write(txt)
    print(D.to_string(index=False))
