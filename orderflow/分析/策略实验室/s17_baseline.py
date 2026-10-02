import numpy as np, sim
from s16_bearshort import prep  # noqa: F401
NAME = '对照：熊市日子每周一 0 点做空，拿一周'
GRID = [{'hold': 288 * 7, 'sd': sd} for sd in (0.15, 0.30, 0.6)]


def trades(df, p):
    t = df.index.values
    m = (t % (7 * 86_400_000) == 4 * 86_400_000) & df.bear.values      # 1970-01-01 是周四，+4 天 = 周一
    return sim.run(df, np.flatnonzero(m), -1, p['sd'], hold=p['hold'], entry='market')
