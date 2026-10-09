"""扫止损收回（实战打法）：程序本身回放，设置和回测 sweep_redo 里"原来的5笔补仓"一样：
实战打法用 5 分钟K线、旧打法统一补仓开（-2/-4/-6/-8%，1:1:1:1:1，均价止盈 2%，离第一笔 11% 止损，72 小时）。
足迹只有K线近似（买卖量放在收盘价那一格，和回测一样）；程序默认用 1 分钟足迹，那个没有逐笔数据测不了。
抽 COINS 个币（2022-01 ~ 2026-09），结果存 /home/user/ext/replay/sweep/<币>.parquet。"""
import os, sys, time, random, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from multiprocessing import Pool
OUT = '/home/user/ext/replay/sweep'
CFG = {"enabled": ["pb_sweep"], "pb_tf": "5m", "old_dca": {"enabled": 1}}


def job(c):
    import warnings; warnings.filterwarnings('ignore')
    import replay as R
    if os.path.exists(f'{OUT}/{c}.parquet'): return c, 'skip'
    df = R.load(c); f = R.features(df); st = time.perf_counter()
    a, b = pd.Timestamp('2022-01-01').value // 10**6, pd.Timestamp('2026-10-01').value // 10**6
    sigs, tr, lg, app = R.replay(c, CFG, t0=a + 9 * 86_400_000, t1=b, feats=f, df=df, regime=R.REG, vol_at_close=True)
    os.makedirs(OUT, exist_ok=True)
    pd.DataFrame([{'t': d['t'], 'side': d['side'], 'skip': d.get('skip') or '', 'traded': bool(d.get('traded')), 'price': d['price']}
                  for d in sigs if d['kind'] == 'pb_sweep']).to_parquet(f'{OUT}/{c}.sig.parquet')
    (tr[tr.k == 'pb_sweep'] if len(tr) else pd.DataFrame()).to_parquet(f'{OUT}/{c}.parquet')
    return c, f'{len(tr)}笔 {round(time.perf_counter() - st)}s'


def init():
    import replay as R
    R.REG = R.btc_regime()


if __name__ == '__main__':
    old = open('/home/user/ext/oos/flush_old_coins.txt').read().split()
    lg = [f[:-8] for f in os.listdir('/home/user/ext/long/met') if f.endswith('.parquet') and not f.endswith('_funding.parquet')]
    pool = sorted(set(old) | set(lg)); random.seed(7); cs = random.sample([c for c in pool if os.path.exists(f'/home/user/ext/bnk/{c}.parquet')], int(os.environ.get('COINS', 20)))
    print('币', cs, flush=True)
    with Pool(int(os.environ.get('NP', 1)), initializer=init) as p:
        for c, m in p.imap_unordered(job, cs):
            print(time.strftime('%H:%M'), c, m, flush=True)
    print('ALL DONE', flush=True)
