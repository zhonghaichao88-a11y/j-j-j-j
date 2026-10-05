"""NFI 进场条件"同时满足"的组合：1 个到 6 个一组，做多做空分开（只组合同方向的条件）。
只看历史上真一起出现过的组合：三段数据每段都至少 30 次才往下加条件（少于 30 次的再加条件只会更少）。
每个组合 × 大盘（不过滤 / BTC 24 小时涨 / 跌）× 4 种出场，所有币，同币不重叠，组合回测（10% 仓位、最多 10 单）。
过关同前：三段每段 ≥30 笔、PF ≥ 1.3、胜率 ≥ 60%、组合赚钱、前后两半 PF ≥ 1。最后把过关的用"任一满足"合成，看单数和收益。"""
import os, sys, glob, numpy as np, pandas as pd
from numba import njit
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model import EXN, SEGS
HERE = os.path.dirname(os.path.abspath(__file__)); DAYS = {'2022-23': 756, '2024-25': 359, '2025-26': 359}
MAXK = int(os.environ.get('MAXK', 6)); MINSEG = 30


@njit(cache=True)
def evaluate(t, coin, y, dur, ncoin):
    """t 已按时间排好。同币有仓位跳过；组合：每笔 10% 当时权益、最多 10 单。返回 笔数,胜,盈,亏,前半盈,前半亏,后半盈,后半亏,终值,最大回撤"""
    busy = np.full(ncoin, -1, np.int64); keep = np.zeros(len(t), np.bool_); n = 0
    for i in range(len(t)):
        if t[i] <= busy[coin[i]] or dur[i] < 0 or np.isnan(y[i]): continue
        keep[i] = True; busy[coin[i]] = t[i] + (dur[i] + 1) * 300000; n += 1
    win = 0; gp = 0.0; gl = 0.0; h = n // 2; c = 0; p1 = 0.0; l1 = 0.0; p2 = 0.0; l2 = 0.0
    eq = 100.0; peak = 100.0; dd = 0.0
    ot = np.zeros(10, np.int64); oa = np.zeros(10); orr = np.zeros(10); oc = np.full(10, -1, np.int64); no = 0
    for i in range(len(t)):
        if not keep[i]: continue
        r = y[i]
        if r > 0: win += 1; gp += r
        else: gl -= r
        if c < h:
            if r > 0: p1 += r
            else: l1 -= r
        else:
            if r > 0: p2 += r
            else: l2 -= r
        c += 1
        # 组合：先把到期的平掉（按到期时间先后）
        while True:
            jm = -1; tm = t[i] + 1
            for j in range(no):
                if ot[j] <= t[i] and ot[j] < tm: tm = ot[j]; jm = j
            if jm < 0: break
            eq += oa[jm] * orr[jm]; peak = max(peak, eq); dd = min(dd, eq / peak - 1)
            ot[jm] = ot[no - 1]; oa[jm] = oa[no - 1]; orr[jm] = orr[no - 1]; oc[jm] = oc[no - 1]; no -= 1
        same = False
        for j in range(no):
            if oc[j] == coin[i]: same = True
        if no < 10 and not same:
            ot[no] = t[i] + (dur[i] + 1) * 300000; oa[no] = eq * 0.1; orr[no] = r; oc[no] = coin[i]; no += 1
    for j in range(no):
        eq += oa[j] * orr[j]
    peak = max(peak, eq); dd = min(dd, eq / peak - 1)
    return n, win, gp, gl, p1, l1, p2, l2, eq, dd


def main():
    P = []
    for seg, d in SEGS.items():
        for f in glob.glob(f'/home/user/ext/nfisig/{d}/f/*.parquet'):
            x = pd.read_parquet(f, columns=['date', 'tag', 'ret_24h'] + [f'{a}{s}{k}' for a in 'yd' for s in 'LS' for k in EXN])
            x['coin'] = os.path.basename(f)[:-8]; x['seg'] = seg; P.append(x)
    D = pd.concat(P, ignore_index=True)
    if os.environ.get('TOPONLY'):
        TOP = set(open('/home/user/ext/nfisig/top.txt').read().split()); D = D[D.coin.isin(TOP)]
    D['t'] = pd.to_datetime(D.date, utc=True).dt.tz_localize(None).values.astype('datetime64[ms]').astype(np.int64)
    btc = D[D.coin == 'BTC'].drop_duplicates(['seg', 't']).set_index(['seg', 't']).ret_24h.rename('btc24')
    D = D.join(btc, on=['seg', 't']); D['btc24'] = D.groupby('seg').btc24.transform(lambda s: s.ffill())
    D = D.sort_values(['seg', 't']).reset_index(drop=True)
    D['cid'] = D.coin.astype('category').cat.codes.astype(np.int64); ncoin = int(D.cid.max()) + 1
    tags = D.tag.fillna('').str.split()
    cnt = tags.explode().value_counts(); conds = [c for c in cnt.index if isinstance(c, str) and c.isdigit() and c not in ('121', '603') and cnt[c] >= MINSEG * 3]
    M = {c: tags.apply(lambda s, c=c: c in s).values for c in conds}
    seg_ix = {s: (D.seg == s).values for s in SEGS}
    REG = {'不过滤': np.ones(len(D), bool), 'BTC24h涨': (D.btc24 > 0).values, 'BTC24h跌': (D.btc24 < 0).values}
    T, C = D.t.values, D.cid.values
    Y = {(s, k): (D[f'y{s}{k}'].values.astype(np.float64), D[f'd{s}{k}'].values.astype(np.int64)) for s in 'LS' for k in EXN}
    print('样本', len(D), '条件', len(conds), flush=True)

    def segok(m):
        return all((m & seg_ix[s]).sum() >= MINSEG for s in SEGS)
    out = []; level = {}
    for side in 'LS':
        cs = sorted([c for c in conds if (int(c) < 500) == (side == 'L')], key=int)
        level = {(c,): M[c] for c in cs if segok(M[c])}
        allsets = dict(level)
        for kk in range(2, MAXK + 1):
            nxt = {}
            for key, m in level.items():
                for c in cs:
                    if int(c) <= int(key[-1]): continue
                    mm = m & M[c]
                    if segok(mm): nxt[key + (c,)] = mm
            print(side, kk, '个一组：', len(nxt), flush=True)
            if not nxt: break
            allsets.update(nxt); level = nxt
        for key, m in allsets.items():
            for rn, rm in REG.items():
                mm = m & rm
                if not segok(mm): continue
                for k in EXN:
                    y, du = Y[(side, k)]; rs = []; ok = True
                    for s in SEGS:
                        ix = np.where(mm & seg_ix[s])[0]
                        n, win, gp, gl, p1, l1, p2, l2, eq, dd = evaluate(T[ix], C[ix], y[ix], du[ix], ncoin)
                        pf_ = gp / gl if gl > 0 else 9.99; f1 = p1 / l1 if l1 > 0 else 9.99; f2 = p2 / l2 if l2 > 0 else 9.99
                        rs.append((n, win / max(n, 1), pf_, (gp - gl) / max(n, 1) * 100, eq, dd * 100, f1, f2))
                        ok &= n >= MINSEG and pf_ >= 1.3 and win / max(n, 1) >= 0.6 and eq > 100 and f1 >= 1 and f2 >= 1
                    out.append(dict(方向='做多' if side == 'L' else '做空', 条件='+'.join(key), 个数=len(key), 大盘=rn, 出场=EXN[k], k=k, 过关=ok,
                                    每天=round(np.mean([r[0] / DAYS[s] for r, s in zip(rs, SEGS)]), 2), 最差PF=round(min(r[2] for r in rs), 2),
                                    **{s: f'{r[0]}笔 胜{r[1]:.0%} PF{r[2]:.2f} 均{r[3]:+.2f}% 组合{r[4]:.1f} 撤{r[5]:.1f}% 半{r[6]:.1f}/{r[7]:.1f}' for r, s in zip(rs, SEGS)}))
    S = pd.DataFrame(out).sort_values(['过关', '每天'], ascending=False); S.to_csv(HERE + ('/itemsets_头部币_结果.csv' if os.environ.get('TOPONLY') else '/itemsets_结果.csv'), index=False)
    pd.set_option('display.width', 450); pd.set_option('display.max_colwidth', 90)
    for sd in ('做多', '做空'):
        s = S[(S.方向 == sd) & S.过关]
        print(f'\n===== {sd}：试了 {(S.方向 == sd).sum()} 个，过关 {len(s)} 个（按每天单数排）'); print(s.head(30).drop(columns=['k']).to_string(index=False))


if __name__ == '__main__':
    main()
