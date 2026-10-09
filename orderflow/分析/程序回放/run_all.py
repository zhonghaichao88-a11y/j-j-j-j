"""全部币 × 每个打法：用程序本身完整回放（有持仓量数据的整段时间），结果存 /home/user/ext/replay/<打法>/<币>.parquet。断了重跑会跳过已完成的。"""
import os, sys, json, time, numpy as np, pandas as pd
from multiprocessing import Pool
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
OUT = '/home/user/ext/replay'
CFGS = {
    'trapC': {"enabled": ["trap_short"], "trap": {"mode": "C"}},
    'flush': {"enabled": ["flush_spot"]},
    'trapB': {"enabled": ["trap_short"], "trap": {"mode": "B"}},
    'trapA': {"enabled": ["trap_short"], "trap": {"mode": "A"}},
    'trapO': {"enabled": ["trap_short"], "trap": {"mode": "orig"}},
    'squeeze': {"enabled": ["squeeze_long"]},
    'momo': {"enabled": ["momo_long"]},
}
KIND = {'trapC': 'trap_short', 'trapB': 'trap_short', 'trapA': 'trap_short', 'trapO': 'trap_short', 'flush': 'flush_spot',
        'squeeze': 'squeeze_long', 'momo': 'momo_long'}


def coins():
    CAT = json.load(open('/home/user/ext/long/lab/okx_category.json'))
    out = set()
    for r in ['/home/user/ext/oos/f5', '/home/user/ext/oos/f6', '/home/user/ext/long']:
        for f in os.listdir(f'{r}/met'):
            if f.endswith('.parquet') and not f.endswith('_funding.parquet'):
                c = f[:-8]
                if os.path.exists(f'/home/user/ext/bnk/{c}.parquet') and CAT.get(c, '1') == '1' and c not in ('USDC',):
                    out.add(c)
    return sorted(out)


def job(c):
    import warnings; warnings.filterwarnings('ignore')
    import replay as R
    todo = [k for k in CFGS if not os.path.exists(f'{OUT}/{k}/{c}.parquet')]
    if not todo: return c, 'skip'
    try:
        df = R.load(c)
    except Exception as e:
        return c, f'ERR load {e}'
    ok = df.oi.notna().values
    if ok.sum() < 20000: 
        for k in todo:
            os.makedirs(f'{OUT}/{k}', exist_ok=True); pd.DataFrame().to_parquet(f'{OUT}/{k}/{c}.parquet')
        return c, 'no oi'
    T = df.index.values
    t0, t1 = int(T[ok.argmax()]), int(T[len(ok) - 1 - ok[::-1].argmax()]) + 300_000
    f = R.features(df); reg = R.REG
    msg = []
    for k in todo:
        st = time.perf_counter()
        sigs, tr, lg, app = R.replay(c, CFGS[k], t0=t0, t1=t1, feats=f, df=df, regime=reg)
        S = pd.DataFrame([{'t': d['t'], 'mode': d.get('mode', ''), 'skip': d.get('skip') or '', 'traded': bool(d.get('traded')),
                           'price': d['price'], 'stop': d.get('stop'), 'target': d.get('target')} for d in sigs if d['kind'] == KIND[k]])
        tr = tr[tr.k == KIND[k]] if len(tr) else tr
        os.makedirs(f'{OUT}/{k}', exist_ok=True)
        S.assign(rec='sig').to_parquet(f'{OUT}/{k}/{c}.sig.parquet')
        (tr.drop(columns=[x for x in ('kind',) if x in tr]) if len(tr) else pd.DataFrame()).to_parquet(f'{OUT}/{k}/{c}.parquet')
        msg.append(f'{k}:{len(tr)}笔/{round(time.perf_counter() - st)}s')
    return c, ' '.join(msg)


def init():
    import replay as R
    R.REG = R.btc_regime()


if __name__ == '__main__':
    cs = coins(); print('币', len(cs), flush=True)
    with Pool(int(os.environ.get('NP', 3)), initializer=init) as p:
        for c, m in p.imap_unordered(job, cs):
            print(time.strftime('%H:%M'), c, m, flush=True)
    print('ALL DONE', flush=True)
