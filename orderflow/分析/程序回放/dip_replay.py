"""急跌抄底三个打法：把长周期回测算出的信号按程序的方式（收盘后 20 秒 eng.dip_signal）送进程序，
程序自己下单、止盈止损、到时间平仓。只回放有信号的时间段（前后各留几天），结果存 /home/user/ext/replay/<打法>/<币>.parquet。
大跌抄底的 ATR、止盈止损按程序算法：of_nfi.vn_features 只用程序会拉的 2600 根 5 分钟K线；只在 BTC 牛市、只做头部币（程序默认）。"""
import os, sys, glob, time, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, '/home/user/j-j-j-j/orderflow')
from multiprocessing import Pool
OUT = '/home/user/ext/replay'; N = '/home/user/ext/nfisig'
KMAP = {'nfi5': 'nfi_5m', 'nfi15': 'nfi_15m', 'vn': 'vn_dip'}


def signals(c, reg):
    import of_nfi as NF
    from of_engine import NFI_DIP, VN_DIP
    out = {}
    L = [pd.read_parquet(f) for f in glob.glob(f'{N}/LY/*/{c}.parquet')]
    L = pd.concat(L) if L else pd.DataFrame(columns=['rule', 't'])
    k5 = pd.read_parquet(f'/home/user/ext/bnk/{c}.parquet', columns=['ts', 'close']).drop_duplicates('ts').set_index('ts').close
    for key in ('nfi5', 'nfi15'):
        x = L[L.rule == KMAP[key]]
        s = []
        for t in sorted(set(x.t.astype(np.int64))):
            bar_t = t - 300_000; ref = float(k5.get(bar_t, np.nan))
            if np.isnan(ref): continue
            s.append((KMAP[key], bar_t, ref, ref * (1 - NFI_DIP['stop_pct'] / 100), ref * (1 + NFI_DIP['tp_pct'] / 100), 'replay'))
        out[key] = s
    V = pd.read_parquet(f'{N}/long_vn.parquet'); V = V[V.coin == c]
    d5 = pd.read_feather(f'/home/user/ext/ft/data_long/okx/futures/{c}_USDT_USDT-5m-futures.feather')[['date', 'open', 'high', 'low', 'close', 'volume']]
    s = []
    for t in sorted(set(V.t.astype(np.int64))):
        bar_t = t - 300_000
        if reg.get(bar_t - bar_t % 86_400_000) is not True: continue          # 程序默认只在 BTC 牛市开
        cut = pd.Timestamp(bar_t, unit='ms', tz='UTC')
        w = d5[d5.date <= cut].tail(NF.BARS['5m']).reset_index(drop=True)
        f = NF.vn_features(w)
        if not f or not (f['vn1'] < -VN_DIP['th1'] or f['vn4'] < -VN_DIP['th4']) or not f['atr'] > 0:
            s.append(('vn_dip_mismatch', bar_t, 0, 0, 0, '')); continue
        ref = float(w.close.iloc[-1])
        s.append(('vn_dip', bar_t, ref, ref - VN_DIP['sl_atr'] * f['atr'], ref + VN_DIP['tp_atr'] * f['atr'], 'replay'))
    out['vn'] = s
    return out


def windows(ts, pad0=10 * 86_400_000, pad1=3 * 86_400_000):
    w = []
    for t in sorted(ts):
        if w and t - w[-1][1] < pad0 + pad1:
            w[-1][1] = t
        else:
            w.append([t, t])
    return [(a, b + pad1) for a, b in w]


def job(c):
    import warnings; warnings.filterwarnings('ignore')
    import replay as R
    todo = [k for k in KMAP if not os.path.exists(f'{OUT}/{k}/{c}.parquet')]
    if not todo: return c, 'skip'
    reg = R.REG; S = signals(c, reg); df = R.load(c); f = R.features(df); msg = []
    for k in todo:
        sig = [x for x in S[k] if x[0] != 'vn_dip_mismatch']; mism = len(S[k]) - len(sig)
        st = time.perf_counter(); T = []; G = []
        for a, b in windows([x[1] for x in sig]):
            sigs, tr, lg, app = R.replay(c, {"enabled": [KMAP[k]]}, t0=a, t1=b, dips=[x for x in sig if a <= x[1] <= b], feats=f, df=df, regime=reg)
            if len(tr): T.append(tr[tr.k == KMAP[k]])
            G += [{'t': d['t'], 'skip': d.get('skip') or '', 'traded': bool(d.get('traded')), 'price': d['price']} for d in sigs if d['kind'] == KMAP[k]]
        os.makedirs(f'{OUT}/{k}', exist_ok=True)
        pd.DataFrame(G).to_parquet(f'{OUT}/{k}/{c}.sig.parquet')
        (pd.concat(T).drop(columns=['kind']) if T else pd.DataFrame()).to_parquet(f'{OUT}/{k}/{c}.parquet')
        msg.append(f'{k}:{len(sig)}信号/{sum(len(x) for x in T)}笔/程序算不出{mism}/{round(time.perf_counter() - st)}s')
    return c, ' '.join(msg)


def init():
    import replay as R
    R.REG = R.btc_regime()


if __name__ == '__main__':
    top = open(f'{N}/top.txt').read().split()
    cs = [c for c in top if os.path.exists(f'/home/user/ext/ft/data_long/okx/futures/{c}_USDT_USDT-5m-futures.feather')]
    print('币', len(cs), flush=True)
    with Pool(int(os.environ.get('NP', 1)), initializer=init) as p:
        for c, m in p.imap_unordered(job, cs):
            print(time.strftime('%H:%M'), c, m, flush=True)
    print('ALL DONE', flush=True)
