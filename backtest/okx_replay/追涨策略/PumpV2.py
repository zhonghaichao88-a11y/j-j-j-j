# 三个追涨策略统一换出场：止损25%，止盈10%；_T 版再加移动止盈（赚到5%后，从最高点回落3%就走）。
# 不用策略原来的出场信号和超时。
from VolumeBreakoutStrategy import VolumeBreakoutStrategy
from PumpDetector import PumpDetector
from VolumeSpikeTrend import VolumeSpikeTrend

class _A:
    minimal_roi = {"0": 0.10}
    stoploss = -0.25
    trailing_stop = False
    use_exit_signal = False

class _T(_A):
    trailing_stop = True
    trailing_stop_positive = 0.03
    trailing_stop_positive_offset = 0.05
    trailing_only_offset_is_reached = True

class VB_A(_A, VolumeBreakoutStrategy): pass
class VB_T(_T, VolumeBreakoutStrategy): pass
class PD_A(_A, PumpDetector): pass
class PD_T(_T, PumpDetector): pass
class VS_A(_A, VolumeSpikeTrend): pass
class VS_T(_T, VolumeSpikeTrend): pass
