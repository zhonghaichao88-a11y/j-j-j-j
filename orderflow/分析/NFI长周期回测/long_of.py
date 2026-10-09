"""订单流程序里的打法，用豆包长数据（币安 5 分钟K线 2020-01 ~ 2026-09，约 250 个币）逐年重测。
  扫止损：和 sweep_redo.py 一样的信号（5 分钟、3 根K线内碰到挂单价才成交），做多做空分开，几种出场 × 大盘 × 币范围
  大跌抄底（vn_dip）：1 小时收盘时 4 小时跌超 4 个标准差 或 1 小时跌超 5 个标准差，下一根开盘进，止盈 1 倍 / 止损 3 倍 1 小时 ATR，最多 48h
  NFI 头部币急跌 nfi_5m / 15 分钟急跌 nfi_15m：dip_sig_long.py 算好的（137 个币，2020-04 起）
  合起来：三个做多打法一起（程序里勾这三个），同币不重叠
每年单独算（每年都从 100U 开始，每笔 10% 权益、最多 10 单），看每年都赚不赚。
大盘牛熊：BTC 昨天收盘在 200 天均线上方算牛（2019 年用币安现货日线补均线）。
清洗接盘、多头摊平做空要持仓量 / 多空比，币安只从 2021-12 开始有，之前已经测过，这里不测。"""
import os, sys, glob, io, zipfile, numpy as np, pandas as pd
from multiprocessing import Pool
from numba import njit
sys.path.insert(0, '/home/user/j-j-j-j/orderflow/分析/策略实验室/扫止损重做'); sys.path.insert(0, '/home/user/j-j-j-j/orderflow/分析/策略实验室/NFI信号对比')
sys.argv = sys.argv[:1] + sys.argv[1:]
HERE = os.path.dirname(os.path.abspath(__file__)); N = '/home/user/ext/nfisig'; BNK = '/home/user/ext/bnk'
SW = f'{N}/long_sweep.parquet'; VN = f'{N}/long_vn.parquet'
TAKER, SLIP, STOP_SLIP, MAKER = 0.0005, 0.0002, 0.0005, 0.0002


@njit(cache=True)
def atr_exit(o, h, l, c, idx, atr, tpx, slx, hold):
    n = len(o); res = np.full(len(idx), np.nan); dur = np.full(len(idx), -1, np.int64)
    for q in range(len(idx)):
        i = idx[q] + 1; a = atr[idx[q]]
        if i >= n - 1 or not (a > 0): continue
        e = o[i] * (1 + SLIP); tg = e + tpx * a; st = e - slx * a; ex = np.nan; fee = TAKER
        for j in range(i, min(n, i + hold)):
            if l[j] <= st:
                ex = min(o[j], st) * (1 - STOP_SLIP); fee += TAKER; dur[q] = j - i + 1; break
            if h[j] >= tg:
                ex = tg; fee += MAKER; dur[q] = j - i + 1; break
        if np.isnan(ex):
            if i + hold > n: continue
            ex = c[i + hold - 1] * (1 - SLIP); fee += TAKER; dur[q] = hold
        res[q] = ex / e - 1 - fee
    return res, dur


def vn_job(c):
    k = pd.read_parquet(f'{BNK}/{c}.parquet', columns=['ts', 'open', 'high', 'low', 'close']).sort_values('ts').drop_duplicates('ts').reset_index(drop=True)
    if len(k) < 6000: return None
    ts = k.ts.values.astype(np.int64); o, h, l, cl = (k[x].values.astype(float) for x in ('open', 'high', 'low', 'close'))
    hr = ts // 3_600_000
    g = pd.DataFrame({'hr': hr, 'h': h, 'l': l, 'c': cl}).groupby('hr').agg(h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), n=('c', 'size'))
    g = g.reindex(np.arange(g.index.min(), g.index.max() + 1))          # 缺的小时留空，不让涨跌幅跨过缺口
    kc = g.c.values; r1 = np.r_[np.nan, kc[1:] / kc[:-1] - 1]
    vol1 = pd.Series(r1).rolling(168, min_periods=48).std().values
    vn1 = r1 / vol1; vn4 = (pd.Series(kc).pct_change(4, fill_method=None).values) / (vol1 * 2)
    import talib
    atr = talib.ATR(g.h.values, g.l.values, kc, 14)
    # 1 小时K线收盘（分钟 55 那根 5 分钟）判断
    last = (ts % 3_600_000) == 55 * 60000
    pos = hr - g.index.min()
    trig = last & ((vn4[pos] < -4) | (vn1[pos] < -5))
    idx = np.where(np.nan_to_num(trig, nan=0).astype(bool))[0].astype(np.int64)
    idx = idx[idx < len(ts) - 2]
    if not len(idx): return None
    a5 = np.full(len(ts), np.nan); a5[idx] = atr[pos[idx]]
    r, d = atr_exit(o, h, l, cl, idx, a5, 1.0, 3.0, 48 * 12)
    return pd.DataFrame({'coin': c, 't': ts[idx + 1], 'y': r, 'd': d})


def sw_job(c):
    import sweep_redo
    return sweep_redo.job(('/home/user/ext/bnkroot', c))


def coins():
    import json
    CAT = json.load(open('/home/user/ext/long/lab/okx_category.json')); ST = {'USDC', 'BUSD', 'TUSD', 'FDUSD', 'USDP', 'DAI', 'USDE'}
    out = []
    for f in sorted(os.listdir(BNK)):
        if not f.endswith('.parquet'): continue
        c = f[:-8]; b = c[4:] if c.startswith('1000') and len(c) > 4 else c
        if b in ST or CAT.get(b, '1') != '1' or CAT.get(c, '1') != '1': continue
        out.append(c)
    return out


def regime():
    P = []
    for f in sorted(glob.glob('/home/user/ext/btcspot/*.zip')):
        z = zipfile.ZipFile(f); t = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])), header=None)
        P.append(pd.DataFrame({'day': pd.to_datetime(t[0], unit='ms').dt.floor('D'), 'c': t[4].astype(float)}))
    s = pd.concat(P)
    k = pd.read_parquet(f'{BNK}/BTC.parquet', columns=['ts', 'close']); k['day'] = pd.to_datetime(k.ts, unit='ms').dt.floor('D')
    p = k.groupby('day').close.last().rename('c').reset_index()
    d = pd.concat([s[s.day < p.day.min()], p]).drop_duplicates('day').sort_values('day').set_index('day').c
    bull = (d > d.rolling(200).mean()).shift(1)                       # 昨天收盘在 200 天均线上方
    return pd.DataFrame({'day': bull.index.values.astype('datetime64[ms]').astype(np.int64), 'bull': bull.values})


YEARS = [2020, 2021, 2022, 2023, 2024, 2025, 2026]


def yr(t):
    return pd.to_datetime(t, unit='ms').year.values


def table(name, D, y='y', d='d', note=''):
    from itemsets import evaluate
    D = D[D[y].notna() & (D[d] >= 0)].sort_values('t').reset_index(drop=True)
    cid = D.coin.astype('category').cat.codes.values.astype(np.int64); nc = int(cid.max()) + 1 if len(D) else 1
    yy = yr(D.t.values); rows = []
    for Y in YEARS + ['全部']:
        ix = np.arange(len(D)) if Y == '全部' else np.where(yy == Y)[0]
        if len(ix) == 0: rows.append(f'  {Y}: 没有单'); continue
        days = max(1, (D.t.values[ix].max() - D.t.values[ix].min()) / 86_400_000) if Y == '全部' else (273 if Y == 2020 else 272 if Y == 2026 else 365)
        n, win, gp, gl, p1, l1, p2, l2, eq, dd = evaluate(D.t.values[ix], cid[ix], D[y].values[ix].astype(float), D[d].values[ix].astype(np.int64), nc)
        pf = gp / gl if gl > 0 else 99
        rows.append(f'  {Y}: {n}笔 每天{n / days:.2f} 胜{win / max(n, 1):.0%} PF{pf:.2f} 100U→{eq:.0f} 回撤{-dd * 100:.0f}% 币{D.coin.values[ix].astype(str).tolist().__len__() and len(set(D.coin.values[ix]))}')
    return f'【{name}】{note}\n' + '\n'.join(rows)


def main():
    cs = coins()
    if not os.path.exists(VN):
        with Pool(2) as p: R = [x for x in p.map(vn_job, cs, chunksize=4) if x is not None]
        pd.concat(R, ignore_index=True).to_parquet(VN); print('vn done', flush=True)
    if not os.path.exists(SW):
        with Pool(int(os.environ.get('NP', 2))) as p: R = [x for x in p.map(sw_job, cs, chunksize=1) if x is not None]
        pd.concat(R, ignore_index=True).to_parquet(SW); print('sweep done', flush=True)
    TOP = set(open(f'{N}/top.txt').read().split())
    R = regime()
    def add(D):
        D = D.copy(); D['day'] = (D.t // 86_400_000 * 86_400_000).astype(np.int64)
        D = D.merge(R, on='day', how='left'); D['bull'] = D.bull.fillna(False).astype(bool)
        D['top'] = D.coin.isin(TOP); return D
    out = [__doc__]
    V = add(pd.read_parquet(VN))
    out.append('=' * 30 + ' 大跌抄底（vn_dip）')
    out.append(table('程序默认：头部币 + 只在牛市', V[V.top & V.bull]))
    out.append(table('头部币，不分牛熊', V[V.top]))
    out.append(table('全部币 + 只在牛市', V[V.bull]))
    L = []
    for f in glob.glob(f'{N}/LY/*/*.parquet'):
        x = pd.read_parquet(f)
        if len(x): x['coin'] = os.path.basename(f)[:-8]; L.append(x)
    if L:
        F = add(pd.concat(L, ignore_index=True)); F['d'] = F.d.astype(np.int64)
        for r, nm in (('nfi_5m', 'NFI 头部币急跌（5 分钟）'), ('nfi_15m', '头部币 15 分钟急跌')):
            out.append('=' * 30 + ' ' + nm)
            out.append(table('程序默认：头部币', F[(F.rule == r) & F.top]))
            out.append(table('全部币（137 个）', F[F.rule == r]))
        C = pd.concat([F[F.top][['coin', 't', 'y', 'd']], V[V.top & V.bull][['coin', 't', 'y', 'd']]], ignore_index=True)
        C = C[C.t >= pd.Timestamp('2020-04-01').value // 10**6]
        out.append('=' * 30 + ' 三个做多打法一起（程序里勾 nfi_5m + nfi_15m + 大跌抄底，默认设置）')
        out.append(table('合计', C))
    S = add(pd.read_parquet(SW))
    EXN = {7: '原来的5笔补仓', 1: '止盈3% 止损8% 48h', 4: '止盈1倍ATR 止损3倍ATR 48h', 0: '止盈2% 止损5% 24h'}
    out.append('=' * 30 + ' 扫止损')
    for sd, sn in ((1, '做多'), (-1, '做空')):
        for k, en in EXN.items():
            for rn, rm in (('不分大盘', None), ('牛', True), ('熊', False)):
                for un in ('全部币', '头部币'):
                    m = (S.side == sd) & (S.top if un == '头部币' else True)
                    if rm is not None: m &= (S.bull == rm)
                    X = S[m].rename(columns={f'y{k}': 'yy', f'd{k}': 'dd'})
                    out.append(table(f'{sn} {en} {rn} {un}', X[['coin', 't', 'yy', 'dd']], 'yy', 'dd'))
    txt = '\n'.join(out); open(HERE + '/打法长周期_结果.txt', 'w').write(txt); print(txt)


if __name__ == '__main__':
    main()
