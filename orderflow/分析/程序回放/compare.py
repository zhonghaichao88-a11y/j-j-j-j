"""程序回放的成交 vs 回测的成交，逐笔对照；再用程序自己的成交算成绩（三段 + 分年）。
用法：python compare.py trapC   （trapC / trapB / trapA / trapO / flush / squeeze / momo）"""
import os, sys, glob, numpy as np, pandas as pd
sys.path.insert(0, '/home/user/j-j-j-j/orderflow/分析/策略实验室/NFI信号对比'); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from itemsets import evaluate
R = '/home/user/ext/replay'; N = '/home/user/ext/nfisig'
SEG = [('2021-12~2023-12', '2021-12-01', '2024-01-01'), ('2024-01~2025-03', '2024-01-01', '2025-04-01'), ('2025-04~2026-09', '2025-04-01', '2026-10-01')]
ms = lambda s: pd.Timestamp(s).value // 10**6


def program(k):
    P = []
    for f in glob.glob(f'{R}/{k}/*.parquet'):
        if f.endswith('.sig.parquet'): continue
        x = pd.read_parquet(f)
        if len(x): x['coin'] = os.path.basename(f)[:-8]; P.append(x)
    P = pd.concat(P, ignore_index=True) if P else pd.DataFrame()
    if len(P):
        P['t_in'] = P.t_open // 300_000 * 300_000; P['t_out'] = P.t_close // 300_000 * 300_000
    done = sorted(os.path.basename(f)[:-8] for f in glob.glob(f'{R}/{k}/*.parquet') if not f.endswith('.sig.parquet'))
    return P, done


def backtest(k):
    if k in ('trapC', 'trapB'):
        B = pd.read_parquet(f'{N}/trap_bc.parquet'); B = B[B['mode'] == {'trapC': 'C 离低点>4%', 'trapB': 'B SAR在上方'}[k]]
    elif k in ('trapA', 'trapO'):
        B = pd.read_parquet(f'{N}/trap_tune2.parquet')
        B = B[(B.bounce == 0.02) & (B.wait == 12) & (B.dmin == 0.02) & (B.exit == '原版 止损5%')] if k == 'trapA' else \
            B[(B.bounce == 0) & (B.dmin == 0) & (B.exit == '原版 止损5%')]
    elif k == 'flush':
        B = pd.read_parquet(f'{N}/flush_redo.parquet'); B = B[(B.sig == '原版') & (B.exit == '原版12h')]
        Rg = pd.read_parquet(f'{N}/btc_regime.parquet'); Rg['day'] = Rg.day.values.astype('datetime64[ms]').astype(np.int64)
        B = B.assign(day=(B.t // 86_400_000 * 86_400_000).astype(np.int64)).merge(Rg, on='day', how='left')
        B = B[(B.bull == True) & (B.oi24 <= 0.0167)]
    elif k in ('nfi5', 'nfi15'):
        L = []
        for f in glob.glob(f'{N}/LY/*/*.parquet'):
            x = pd.read_parquet(f)
            if len(x): x['coin'] = os.path.basename(f)[:-8]; L.append(x)
        B = pd.concat(L); B = B[(B.rule == {'nfi5': 'nfi_5m', 'nfi15': 'nfi_15m'}[k]) & (B.d >= 0) & B.y.notna()]
        B = B.assign(t_in=B.t, t_out=B.t + (B.d.astype(np.int64) - 1) * 300_000, ret=B.y.astype(float), t=B.t - 300_000)
    elif k == 'vn':
        B = pd.read_parquet(f'{N}/long_vn.parquet'); B = B[(B.d >= 0) & B.y.notna()]
        reg = {}
        import replay as RP
        reg = RP.btc_regime()
        B = B[[reg.get(int(t - 300_000) - int(t - 300_000) % 86_400_000) is True for t in B.t]]
        B = B.assign(t_in=B.t, t_out=B.t + (B.d.astype(np.int64) - 1) * 300_000, ret=B.y.astype(float), t=B.t - 300_000)
    else:
        return None
    return B[['coin', 't', 't_in', 't_out', 'ret']].drop_duplicates(['coin', 't_in'])


def stats(X, y='ret'):
    X = X.sort_values('t_in'); cid = X.coin.astype('category').cat.codes.values.astype(np.int64)
    d = ((X.t_out - X.t_in) // 300_000 + 1).values.astype(np.int64)
    n, win, gp, gl, p1, l1, p2, l2, eq, dd = evaluate(X.t_in.values.astype(np.int64), cid, X[y].values.astype(float), d, int(cid.max()) + 1 if len(X) else 1)
    return f"{n}笔 胜{win / max(n, 1):.0%} PF{gp / gl if gl > 0 else 99:.2f} 每笔均{(gp - gl) / max(n, 1) * 100:+.2f}% 100U→{eq:.0f} 回撤{-dd * 100:.0f}%"


if __name__ == '__main__':
    k = sys.argv[1]
    P, done = program(k)
    out = [f'【{k}】已回放 {len(done)} 个币，程序成交 {len(P)} 笔']
    B = backtest(k)
    if B is not None and len(P):
        t0 = ms('2020-01-01') if k in ('nfi5', 'nfi15', 'vn') else ms('2021-12-01')
        B = B[B.coin.isin(done) & (B.t >= t0)]
        P2 = P[P.t_in >= t0]
        M = P2.merge(B, on=['coin', 't_in'], how='outer', suffixes=('_p', '_b'), indicator=True)
        both = M[M._merge == 'both']
        out.append(f'逐笔对照（同币同一根K线成交）：两边都有 {len(both)}，只有程序 {(M._merge == "left_only").sum()}，只有回测 {(M._merge == "right_only").sum()}')
        if len(both):
            same_out = (both.t_out_p == both.t_out_b).mean()
            out.append(f'  两边都有的：平仓在同一根K线 {same_out:.0%}；每笔收益差（程序 - 回测）平均 {(both.ret_p - both.ret_b).mean() * 100:+.3f}%，'
                       f'差超过 0.5% 的 {((both.ret_p - both.ret_b).abs() > 0.005).mean():.0%}')
        M.to_csv(f'{R}/对照_{k}.csv', index=False)
        out.append('回测成绩（同一批币）：' + stats(B))
    if len(P):
        out.append('程序成绩（全部）：' + stats(P2 if B is not None else P))
        for nm, a, b in SEG:
            x = P[(P.t_in >= ms(a)) & (P.t_in < ms(b))]
            if len(x): out.append(f'  {nm}: ' + stats(x))
        for y, g in P.groupby(pd.to_datetime(P.t_in, unit='ms').dt.year):
            out.append(f'  {y}: ' + stats(g))
        out.append('平仓原因：' + str(P.why.str.slice(0, 6).value_counts().to_dict()))
    print('\n'.join(out))
