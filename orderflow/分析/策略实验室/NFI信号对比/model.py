"""用 NFI 的全部指标（5 分钟 ~ 日线 210 个 + 81 个进场条件有没有触发）训练"挑进场点"的模型，多空分开。

样本：每个币每个整点（xx:55 那根 5 分钟K线收盘）一个，下一根开盘市价进场。
结果：做多/做空，4 种出场（止盈/止损/最多拿）：0=2%/5%/24h 1=3%/8%/48h 2=5%/11%/72h 3=1.5%/3%/12h，扣手续费滑点。
数据三段：2022-23（23 币）、2024-10~2025-09（35 币）、2025-10~2026-09（约 220 币），全部是欧易数据。

检验（事先定好，不看结果改）：
  轮流拿两段训练，第三段从没见过的数据考试（3 次）；另外按时间顺序：2022-23 训练考 2024-25，前两段训练考 2025-26。
  模型分数取训练集里最高的 q%（q = 1/2/5/10）当门槛，原样用到考试段。
  考试段按程序规则跑：同一个币有仓位不开，组合每笔 10% 权益、最多同时 10 单、复利 100U 起。
  过关：5 次考试每次都 PF ≥ 1.3、比随机进场 PF 高 ≥ 0.2、胜率 ≥ 60%、组合赚钱，且前后两半都 PF ≥ 1。
  过关的里面挑"每天单数 × 平均每笔赚"最高的。"""
import os, sys, glob, heapq, numpy as np, pandas as pd, lightgbm as lgb
HERE = os.path.dirname(os.path.abspath(__file__))
SEGS = {'2022-23': 'F_old', '2024-25': 'F_mid', '2025-26': 'F_new'}
EXN = {0: '止盈2% 止损5% 24h', 1: '止盈3% 止损8% 48h', 2: '止盈5% 止损11% 72h', 3: '止盈1.5% 止损3% 12h'}
FOLDS = [(['2024-25', '2025-26'], '2022-23'), (['2022-23', '2025-26'], '2024-25'), (['2022-23', '2024-25'], '2025-26'),
         (['2022-23'], '2024-25'), (['2022-23', '2024-25'], '2025-26')]
QS = [0.005, 0.01, 0.02, 0.05, 0.10]
OBJ = os.environ.get('OBJ', 'regression')            # regression = 学每笔赚多少；binary = 学赚还是亏
MAXTRAIN = int(os.environ.get('MAXTRAIN', 1_500_000))  # 训练样本太多时随机抽这么多（内存）
PARAMS = dict(objective='regression', learning_rate=0.03, num_leaves=15, min_data_in_leaf=400, feature_fraction=0.5,
              bagging_fraction=0.7, bagging_freq=1, lambda_l2=10.0, verbose=-1, num_threads=4, seed=1)


def pf(x):
    x = np.asarray(x); n = -x[x < 0].sum(); return x[x > 0].sum() / n if n > 0 else 9.99


def load():
    parts = []
    for seg, d in SEGS.items():
        for f in sorted(glob.glob(f'/home/user/ext/nfisig/{d}/f/*.parquet')):
            x = pd.read_parquet(f); x['coin'] = os.path.basename(f)[:-8]; x['seg'] = seg; parts.append(x)
    D = pd.concat(parts, ignore_index=True)
    tags = D.tag.fillna('').str.split()
    D['n_long_cond'] = tags.apply(lambda t: sum(1 for x in t if x.isdigit() and int(x) < 500 and x not in ('121',))).astype(np.int16)
    D['n_short_cond'] = tags.apply(lambda t: sum(1 for x in t if x.isdigit() and int(x) >= 500 and x not in ('603',))).astype(np.int16)
    common = tags.explode().value_counts()
    for t in common.index[(common > 2000) & (common < len(D) * 0.5)]:
        D[f'cond_{t}'] = tags.apply(lambda s, t=t: t in s).astype(np.int8)
    D['t'] = pd.to_datetime(D.date, utc=True).dt.tz_localize(None).values.astype('datetime64[ms]').astype(np.int64)
    # 大盘：同一时刻 BTC 的涨跌、全部币里在涨的比例；这个币在同一时刻所有币里的排名（0~1）
    btc = D[D.coin == 'BTC'].drop_duplicates(['seg', 't']).set_index(['seg', 't'])[['ret_1h', 'ret_4h', 'ret_24h', 'RSI_14']]
    btc.columns = ['btc_' + c for c in btc.columns]
    D = D.join(btc, on=['seg', 't'])
    g = D.groupby(['seg', 't'])
    for c in ('ret_1h', 'ret_4h', 'ret_24h'):
        D[f'breadth_{c}'] = g[c].transform(lambda s: (s > 0).mean()).astype(np.float32)
        D[f'rank_{c}'] = g[c].rank(pct=True).astype(np.float32)
        D[f'vs_btc_{c}'] = (D[c] - D[f'btc_{c}']).astype(np.float32)
    D['rank_vol_rel_1h'] = g['vol_rel_1h'].rank(pct=True).astype(np.float32)
    D['rank_RSI_14'] = g['RSI_14'].rank(pct=True).astype(np.float32)
    return D


def no_overlap(X, dcol):
    """同一个币有仓位就不开：按时间走，平仓前的信号跳过"""
    keep = []
    for c, g in X.sort_values('t').groupby('coin', sort=False):
        busy = -1
        for t, du, ix in zip(g.t.values, g[dcol].values, g.index.values):
            if t <= busy or du < 0:
                continue
            keep.append(ix); busy = t + (int(du) + 1) * 300_000
    return X.loc[keep]


def port(X, ycol, dcol, cap=10, size=0.10, start=100.0):
    X = X.sort_values('t'); eq, op, busy, curve = start, [], set(), []
    for t, c, r, du in zip(X.t.values, X.coin.values, X[ycol].values, X[dcol].values):
        while op and op[0][0] <= t:
            t1, c1, amt, r1 = heapq.heappop(op); eq += amt * r1; busy.discard(c1); curve.append(eq)
        if c in busy or len(op) >= cap:
            continue
        busy.add(c); heapq.heappush(op, (t + (int(du) + 1) * 300_000, c, eq * size, r))
    while op:
        t1, c1, amt, r1 = heapq.heappop(op); eq += amt * r1; curve.append(eq)
    cv = np.array(curve) if curve else np.array([start])
    return round(float(eq), 1), round(float((cv / np.maximum.accumulate(cv) - 1).min() * 100), 1)


def main():
    D = load()
    import re
    feats = [c for c in D.columns if c not in ('date', 'i', 'tag', 'coin', 'seg', 't', 'hourly') and not re.fullmatch(r'[yd][LS]\d+', c)]
    days = {s: (D[D.seg == s].t.max() - D[D.seg == s].t.min()) / 86_400_000 for s in SEGS}
    ncoin = {s: D[D.seg == s].coin.nunique() for s in SEGS}
    print(f'样本 {len(D)}，特征 {len(feats)}，' + '，'.join(f'{s} {ncoin[s]} 币 {days[s]:.0f} 天' for s in SEGS), flush=True)
    rows = []
    for side in ('L', 'S'):
        for k in EXN:
            y, dc = f'y{side}{k}', f'd{side}{k}'
            for fi, (tr, te) in enumerate(FOLDS):
                A = D[D.seg.isin(tr) & D[y].notna()]; B = D[(D.seg == te) & D[y].notna()].copy()
                if len(A) > MAXTRAIN:
                    A = A.sample(MAXTRAIN, random_state=fi)
                tgt = (A[y] > 0).astype(np.float32) if OBJ == 'binary' else A[y].clip(-0.12, 0.06)
                m = lgb.train({**PARAMS, 'objective': OBJ}, lgb.Dataset(A[feats].astype(np.float32), tgt), num_boost_round=int(os.environ.get('ROUNDS', 300)))
                pa = m.predict(A[feats].astype(np.float32)); B['p'] = m.predict(B[feats].astype(np.float32))
                rnd = no_overlap(B.sample(frac=0.05, random_state=fi), dc)
                for q in QS:
                    th = np.quantile(pa, 1 - q)
                    X = no_overlap(B[B.p >= th], dc)
                    if len(X) < 10:
                        rows.append(dict(方向=side, 出场=k, q=q, 考=te, 训练='+'.join(tr), 笔=len(X))); continue
                    h = len(X) // 2; Xs = X.sort_values('t')
                    fin, dd = port(X, y, dc)
                    rows.append(dict(方向=side, 出场=k, q=q, 考=te, 训练='+'.join(tr), 笔=len(X),
                                     每天=round(len(X) / days[te], 2), 每天每50币=round(len(X) / days[te] / ncoin[te] * 50, 2),
                                     胜=round((X[y] > 0).mean(), 3), PF=round(pf(X[y]), 2), 平均=round(X[y].mean() * 100, 3),
                                     随机PF=round(pf(rnd[y]), 2), 前半=round(pf(Xs[y][:h]), 2), 后半=round(pf(Xs[y][h:]), 2),
                                     赚钱币=round((X.groupby('coin')[y].sum() > 0).mean(), 2), 组合=fin, 回撤=dd))
                print(side, EXN[k], '考', te, '训练', '+'.join(tr), [(r['q'], r.get('PF'), r.get('笔')) for r in rows[-len(QS):]], flush=True)
    R = pd.DataFrame(rows); R.to_csv(HERE + f'/model_结果_{OBJ}.csv', index=False)
    out = []
    for (side, k, q), g in R.groupby(['方向', '出场', 'q']):
        ok = len(g) == len(FOLDS) and g.笔.min() >= 30 and (g.PF >= 1.3).all() and ((g.PF - g.随机PF) >= 0.2).all() and \
            (g.胜 >= 0.6).all() and (g.组合 > 100).all() and (g.前半 >= 1).all() and (g.后半 >= 1).all()
        out.append(dict(方向='做多' if side == 'L' else '做空', 出场=EXN[k], 前百分之=q * 100, 过关=ok,
                        每天每50币=round(g.每天每50币.mean(), 2), 胜=round(g.胜.mean(), 3), 最差PF=g.PF.min(), 平均每笔=round(g.平均.mean(), 3),
                        最差组合=g.组合.min(), 最大回撤=g.回撤.min(), 分数=round(g.每天每50币.mean() * g.平均.mean(), 3)))
    S = pd.DataFrame(out).sort_values(['过关', '分数'], ascending=False); S.to_csv(HERE + f'/model_汇总_{OBJ}.csv', index=False)
    pd.set_option('display.width', 250); print(S.to_string(index=False))


if __name__ == '__main__':
    main()
