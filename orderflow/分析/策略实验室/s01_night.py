import numpy as np, sim, feat
NAME = '夜间时段做多'
RULES = '出处：Quantpedia 比特币时段研究（UTC 22:00 买入、24:00 卖出）。UTC h 点整市价做多，拿 hold 根（5 分钟）后平；止损 15% 只防极端。'
GRID = [{'h': 22, 'hold': 24, 'only': 'BTC'}, {'h': 22, 'hold': 24, 'only': 'BTCETH'}, {'h': 22, 'hold': 24, 'only': ''}, {'h': 21, 'hold': 36, 'only': ''}]


def prep(df):
    return df


def trades(df, p):
    nm = df.attrs['name']
    if (p['only'] == 'BTC' and nm != 'BTC') or (p['only'] == 'BTCETH' and nm not in ('BTC', 'ETH')):
        return []
    sig = np.flatnonzero(feat.mod(df) == p['h'] * 60 - 5)
    return sim.run(df, sig, 1, 0.15, hold=p['hold'], entry='market')
