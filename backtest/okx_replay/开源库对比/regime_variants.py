"""4 个网友策略的两种改法（规则事先定好，不调参数）：
  <名字>_BTC ：原策略只做多 + 大盘过滤（BTC 日线收盘在 EMA50 上方才开多；用前一根已收盘的日线）。
  <名字>_LS  ：同上做多；另外 BTC 在 EMA50 下方时做空——空单信号 = 把价格上下翻转（1/价格）后原策略的做多信号（镜像），
                出场同理镜像。止盈止损、周期都沿用原策略。
"""
import importlib.util, os, sys
import numpy as np
from pandas import DataFrame
from freqtrade.strategy import merge_informative_pair

HERE = os.path.dirname(os.path.abspath(__file__))
BASES = ['ADXMomentum', 'AverageStrategy', 'MultiMa', 'FSupertrendStrategy']


def _load(name):
    spec = importlib.util.spec_from_file_location(f'_base_{name}', os.path.join(HERE, f'{name}.py'))
    mod = importlib.util.module_from_spec(spec); sys.modules[spec.name] = mod; spec.loader.exec_module(mod)
    return getattr(mod, name)


def _ema(x, n):
    return x.ewm(span=n, adjust=False).mean()


def _mirror(df: DataFrame) -> DataFrame:
    m = df.copy()
    m['open'], m['close'] = 1 / df['open'], 1 / df['close']
    m['high'], m['low'] = 1 / df['low'], 1 / df['high']
    return m


def make(base_cls, short):
    class Variant(base_cls):
        can_short = short
        use_exit_signal = True

        def informative_pairs(self):
            stake = self.config.get('stake_currency', 'USDT')
            btc = f'BTC/{stake}:{stake}' if self.config.get('trading_mode') == 'futures' else f'BTC/{stake}'
            return [(btc, '1d')]

        def _btc(self, dataframe, metadata):
            pair = self.informative_pairs()[0][0]
            inf = self.dp.get_pair_dataframe(pair=pair, timeframe='1d')
            inf = inf[['date', 'close']].copy(); inf['up'] = (inf['close'] > _ema(inf['close'], 50)).astype(int)
            inf['ok'] = (np.arange(len(inf)) >= 50).astype(int)
            out = merge_informative_pair(dataframe, inf[['date', 'up', 'ok']], self.timeframe, '1d', ffill=True)
            return out

        def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
            d = base_cls.populate_indicators(self, dataframe.copy(), metadata)
            if short:
                m = base_cls.populate_indicators(self, _mirror(dataframe), metadata)
                m = base_cls.populate_entry_trend(self, m, metadata)
                m = base_cls.populate_exit_trend(self, m, metadata)
                d['_m_enter'] = m.get('enter_long', 0); d['_m_exit'] = m.get('exit_long', 0)
            return self._btc(d, metadata)

        def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
            dataframe = base_cls.populate_entry_trend(self, dataframe, metadata)
            up = (dataframe['up_1d'] == 1) & (dataframe['ok_1d'] == 1)
            dn = (dataframe['up_1d'] == 0) & (dataframe['ok_1d'] == 1)
            dataframe['enter_long'] = np.where(up & (dataframe.get('enter_long', 0) == 1), 1, 0)
            dataframe['enter_short'] = np.where(dn & (dataframe['_m_enter'] == 1), 1, 0) if short else 0
            return dataframe

        def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
            dataframe = base_cls.populate_exit_trend(self, dataframe, metadata)
            if 'exit_long' not in dataframe: dataframe['exit_long'] = 0
            dataframe['exit_short'] = np.where(dataframe['_m_exit'] == 1, 1, 0) if short else 0
            return dataframe

    Variant.__name__ = Variant.__qualname__ = f'{base_cls.__name__}_{"LS" if short else "BTC"}'
    return Variant


_B = {b: _load(b) for b in BASES}


class ADXMomentum_BTC(make(_B['ADXMomentum'], False)):
    pass


class ADXMomentum_LS(make(_B['ADXMomentum'], True)):
    pass


class AverageStrategy_BTC(make(_B['AverageStrategy'], False)):
    pass


class AverageStrategy_LS(make(_B['AverageStrategy'], True)):
    pass


class MultiMa_BTC(make(_B['MultiMa'], False)):
    pass


class MultiMa_LS(make(_B['MultiMa'], True)):
    pass


class FSupertrendStrategy_BTC(make(_B['FSupertrendStrategy'], False)):
    pass


class FSupertrendStrategy_LS(make(_B['FSupertrendStrategy'], True)):
    pass
