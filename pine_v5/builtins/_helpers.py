"""Shared helpers for registered builtin handlers (not auto-imported).

Handler signature: def handler(runtime, node, i) -> value
  node = ('call', name, args_list, kwargs_dict)
"""
from __future__ import annotations
import math

from ..constants import PineError

_ABSENT = object()


def isna(v):
    """Pine na check: None or non-finite number."""
    return v is None or (isinstance(v, (int, float)) and not math.isfinite(v))


def na():
    return float('nan')


def num(v):
    """Coerce to finite float or raise PineError. na propagates as nan."""
    if isna(v):
        return float('nan')
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise PineError('需要数值参数，得到：' + repr(v))
    return float(v)


def arg(runtime, node, i, idx, key=_ABSENT, default=_ABSENT):
    """Resolve positional arg #idx or keyword arg `key`; fallback to default."""
    if key is not _ABSENT and key in node[3]:
        return runtime.evaluate(node[3][key], i)
    if idx < len(node[2]):
        return runtime.evaluate(node[2][idx], i)
    if default is _ABSENT:
        raise PineError('缺少必需参数 %s #%d' % (node[1], idx + 1))
    return default


def arg_int(runtime, node, i, idx, key=_ABSENT, default=_ABSENT,
            lo=0, hi=5000):
    v = arg(runtime, node, i, idx, key, default)
    if isna(v):
        return float('nan')
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise PineError('整数参数无效：' + repr(v))
    v = float(v)
    if not math.isfinite(v) or int(v) != v:
        raise PineError('整数参数无效：' + repr(v))
    if not (lo <= v <= hi):
        raise PineError('参数 %s 越界 [%d,%d]：%s' % (node[1], lo, hi, v))
    return int(v)


def window_values(runtime, node, length, i):
    """Convenience: runtime.window but returns nan-window-safe list."""
    return runtime.window(node, length, i)
