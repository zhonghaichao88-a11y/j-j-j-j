"""方案三实盘代码 vs 回测代码：回测用过的全部币，逐小时比对进场/出场信号（全历史），并按实盘取数方式（最近1000根1h、400根日线）每50小时抽查一次 evaluate。"""
import sys, glob, os, json, numpy as np, pandas as pd
from multiprocessing import Pool
sys.path.insert(0, '/home/user/j-j-j-j'); sys.path.insert(0, '/home/user/ext')
import alpha_v7_scheme3 as S3, adx_bt as A
D = '/home/user/okx_data'; H = S3.H; DAY = S3.D

def npz(p):
    z = np.load(p); return {k: np.asarray(z[k]) for k in ('ts', 'open', 'high', 'low', 'close')}
def daily(f):
    s = pd.DataFrame(f); s['d'] = s.ts // DAY * DAY
    g = s.groupby('d').agg(close=('close', 'last'), n=('close', 'size')); g = g[g.n >= 20]
    return dict(ts=g.index.values.astype(np.int64), close=g.close.values)
def ft_btc():
    d = pd.read_feather('/home/user/ext/ft/data/okx/futures/BTC_USDT_USDT-1d-futures.feather')
    ts = ((d.date - pd.Timestamp(0, tz='UTC')) // pd.Timedelta('1ms')).astype('int64').values
    return dict(ts=ts, close=d.close.values)

SETS = {'币安独有416': (sorted(glob.glob(f'{D}/*_bn1h.npz')), 'bnf'),
        '币安更早年份137': (sorted(glob.glob(f'{D}/*_bnold1h.npz')), 'old'),
        'OKX181': (sorted(p for p in glob.glob(f'{D}/*_1h.npz') if '_bn' not in p), 'ft')}
BTC = {}
def btc_for(kind):
    if kind not in BTC:
        BTC[kind] = (daily({k: v for k, v in np.load(f'{D}/bnf_BTC-USDT-SWAP.npz').items() if k in ('ts', 'close')}) if kind == 'bnf'
                     else daily(npz(f'{D}/BTCUSDT_bnold1h.npz')) if kind == 'old' else ft_btc())
    return BTC[kind]

def one(job):
    name, path, kind = job
    f = npz(path); o = np.argsort(f['ts']); f = {k: v[o] for k, v in f.items()}
    ts = f['ts']; btc = btc_for(kind)
    if len(ts) < S3.MIN_BARS + 10: return name, path, 0, 0, 0, 0, 0, []
    # 1) 全历史逐根：进场/出场信号（多、空镜像）
    e1, x1 = A.signals(f); e2, x2 = S3.signals(f)
    se1, sx1 = A.signals(A.mirror(f)); se2, sx2 = S3.signals(S3.mirror(f))
    bad_sig = int((e1 != e2).sum() + (x1 != x2).sum() + (se1 != se2).sum() + (sx1 != sx2).sum())
    # 2) BTC 大盘状态：回测(全历史EMA) vs 实盘函数(最近400根日线)，按每根1h收盘时刻
    up = A.btc_up(btc, ts + H); dn = A.btc_down(btc, ts + H)
    days = np.unique((ts + H) // DAY * DAY); cache = {}
    bts = btc['ts']
    for d0 in days:
        m = bts + DAY <= d0; w = {k: v[m][-400:] for k, v in btc.items()}
        cache[d0] = S3.btc_state(w, d0)
    mine = np.array([cache[(t + H) // DAY * DAY] for t in ts])
    ref_state = np.where(up, 1, np.where(dn, -1, 0))
    bad_btc = int((mine != ref_state).sum())
    ref = np.where(e1 & up, 1, np.where(se1 & dn, -1, 0))
    # 3) 实盘取数方式抽查：最近1000根1h + 400根日线，调用 evaluate
    bad_ev = 0; n_ev = 0; ex = []
    for i in range(S3.MIN_BARS + 1000, len(ts), 50):
        w = {k: v[i - 999:i + 1] for k, v in f.items()}
        if w['ts'][-1] - w['ts'][0] != 999 * H: continue          # 实盘遇到缺口会拒绝，不比
        m = bts + DAY <= ts[i] + H; bd = {k: v[m][-400:] for k, v in btc.items()}
        ev = S3.evaluate(w, bd, int(ts[i]) + H); n_ev += 1
        if ev['side'] != ref[i]: bad_ev += 1; ex.append((int(ts[i]), ev['side'], int(ref[i])))
    return name, os.path.basename(path), len(ts), bad_sig, bad_btc, n_ev, bad_ev, ex[:3]

if __name__ == '__main__':
    jobs = [(n, p, k) for n, (ps, k) in SETS.items() for p in ps]
    rows = []
    with Pool(4) as pool:
        for r in pool.imap_unordered(one, jobs, chunksize=4):
            rows.append(r)
            if r[3] or r[4] or r[6]: print('不一致', r, flush=True)
    R = pd.DataFrame(rows, columns=['数据', '币', 'K线', '信号不一致', 'BTC状态不一致', '抽查次数', '抽查不一致', '例子'])
    R.to_csv('/home/user/j-j-j-j/backtest/okx_replay/开源库对比/scheme3_verify_all.csv', index=False)
    g = R.groupby('数据').agg(币数=('币', 'size'), K线=('K线', 'sum'), 信号不一致=('信号不一致', 'sum'),
                              BTC状态不一致=('BTC状态不一致', 'sum'), 抽查次数=('抽查次数', 'sum'), 抽查不一致=('抽查不一致', 'sum'),
                              有问题的币=('抽查不一致', lambda s: int((s > 0).sum())))
    print(g.to_string()); print('DONE')
