import glob, os, json, numpy as np, pandas as pd, momo1h as M
CAT = json.load(open('/home/user/ext/long/lab/okx_category.json'))
STOCK = {'AAOI','CRCL','GOOGL','INTC','MSTR','NVDA','TSLA','XAG','XAU','AAPL','AMZN','META','MSFT','COIN','HOOD','PLTR','AMD','NFLX','SPY','QQQ','XPT','XPD','CL','NG','HG'}
OURS = set(l.split()[0] for l in open('/home/user/ext/long/syms.txt'))
def load(f):
    z = np.load(f)
    return pd.DataFrame({'ts': z['ts'], 'o': z['open'], 'l': z['low'], 'c': z['close'], 'qv': z['volume'] * z['close']}).drop_duplicates('ts').sort_values('ts').reset_index(drop=True)
sets = {}
for f in glob.glob('okx/*_1h.npz') + glob.glob('okx/okx64/*_1h.npz'):
    c = os.path.basename(f).split('-')[0]
    if c in STOCK or CAT.get(c, '1') != '1' or 'BTC' == c: continue
    sets.setdefault('okx', {})[c] = f
for f in glob.glob('bn/*_bn1h.npz'):
    c = os.path.basename(f).replace('USDT_bn1h.npz', '')
    if c in STOCK: continue
    sets.setdefault('bn', {})[c] = f
for name, title in (('okx', '欧易自己的数据'), ('bn', '币安独有的新币（从没用过）')):
    rows, rows_new = [], []
    for c, f in sets[name].items():
        d = load(f)
        if len(d) < 24 * 10: continue
        r = [(c, t, x) for t, x in M.run(d)]
        rows += r
        if c.replace('1000', '').replace('1000000', '') not in OURS and c not in OURS: rows_new += r
    T = M.summary(title, rows)
    T.to_parquet(f'T_{name}.parquet')
    if name == 'okx':
        M.summary('  其中不在原来 102 个币里的', rows_new)
    print('  时间范围', pd.to_datetime(T.t.min(), unit='ms').date(), '~', pd.to_datetime(T.t.max(), unit='ms').date())
    for y, g in T.groupby(pd.to_datetime(T.t, unit='ms').dt.year):
        pf = g.ret[g.ret > 0].sum() / -g.ret[g.ret < 0].sum()
        print(f'  {y}: {len(g)} 笔 每笔 {g.ret.mean()*100:+.2f}% PF {pf:.2f}')
