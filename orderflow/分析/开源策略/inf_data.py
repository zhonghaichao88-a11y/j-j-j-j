"""给策略机回测用的"别的币/别的周期K线"（策略的 informative_pairs / @informative / dp.get_pair_dataframe）。
和 freqtrade 回测一样：每个 (币, 周期) 取 [开始时间 - 预热根数 × 该周期, 结束时间]，缺的K线补齐。"""
import os
import sys
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..', 'stratbot'))
import sb_loader  # noqa: E402

SPOT = '/home/user/ext/ft/data_spot/okx'
FUT = '/home/user/ext/ft/data_long/okx/futures'


def okx_source(t0, t1, startup, mode='spot'):
    """欧易本地 feather K线（freqtrade 回测用的同一份）。返回 source(pair, tf) 函数，结果缓存"""
    cache = {}

    def path(pair, tf):
        base = pair.split('/')[0]
        if mode == 'spot':
            return f'{SPOT}/{base}_USDT-{tf}.feather'
        return f'{FUT}/{base}_USDT_USDT-{tf}-futures.feather'

    def src(pair, tf):
        k = (pair.split('/')[0], tf)
        if k not in cache:
            f = path(pair, tf)
            if tf not in sb_loader.TF_MIN or not os.path.exists(f):
                cache[k] = None
            else:
                d = pd.read_feather(f)
                w0 = t0 - pd.Timedelta(minutes=sb_loader.TF_MIN[tf] * startup)
                d = d[(d.date >= w0) & (d.date <= t1)]
                cache[k] = sb_loader.fill_missing(d.reset_index(drop=True), sb_loader.TF_MIN[tf]) if len(d) else None
        return cache[k]
    return src
