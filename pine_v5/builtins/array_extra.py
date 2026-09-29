"""Pine v5 `array` namespace extras: insert/fill/join/sort/slice/first/last."""
from __future__ import annotations
import math

from .registry import register
from ._helpers import PineError, isna, na, arg, arg_int
from ..constants import CONSTANTS

# Sort-order constants are plain name references in Pine; make them resolve to
# their literal token strings (mutating the shared registry at import time,
# without touching constants.py itself).
CONSTANTS.setdefault('order.ascending', 'order.ascending')
CONSTANTS.setdefault('order.descending', 'order.descending')


def _get_array(runtime, node, i):
    v = runtime.evaluate(node[2][0], i)
    if not isinstance(v, list):
        raise PineError(node[1] + ' 需要数组ID作为第一个参数')
    return v


@register('array.insert')
def array_insert(runtime, node, i):
    arr = _get_array(runtime, node, i)
    idx = arg_int(runtime, node, i, 1, lo=0, hi=len(arr))
    if len(arr) >= 100000:
        raise PineError('数组容量上限为100000')
    value = runtime.evaluate(node[2][2], i)
    arr.insert(idx, value)
    return None


@register('array.fill')
def array_fill(runtime, node, i):
    arr = _get_array(runtime, node, i)
    value = runtime.evaluate(node[2][1], i)
    start = arg_int(runtime, node, i, 2, 'start', 0, lo=0, hi=len(arr))
    end = arg_int(runtime, node, i, 3, 'end', len(arr), lo=0, hi=len(arr))
    if start > end:
        raise PineError('array.fill: start 不能大于 end')
    for j in range(start, end):
        arr[j] = value
    return None


@register('array.join')
def array_join(runtime, node, i):
    arr = _get_array(runtime, node, i)
    sep = arg(runtime, node, i, 1, 'separator', '')
    if not isinstance(sep, str):
        raise PineError('array.join separator 必须是字符串')
    parts = []
    for v in arr:
        if isna(v):
            parts.append('na')
        elif isinstance(v, bool):
            parts.append('true' if v else 'false')
        elif isinstance(v, float) and v.is_integer():
            parts.append(str(int(v)))
        else:
            parts.append(str(v))
    return sep.join(parts)


@register('array.sort')
def array_sort(runtime, node, i):
    arr = _get_array(runtime, node, i)
    order = arg(runtime, node, i, 1, 'order', 'order.ascending')
    # na-aware comparison key: nans go last
    def key(v):
        return (0, v) if isinstance(v, (int, float)) and math.isfinite(v) \
            and not isinstance(v, bool) else (1, 0)
    arr.sort(key=key, reverse=(order == 'order.descending'))
    return None


@register('array.slice')
def array_slice(runtime, node, i):
    arr = _get_array(runtime, node, i)
    start = arg_int(runtime, node, i, 1, 'start', 0, lo=0, hi=len(arr))
    end = arg_int(runtime, node, i, 2, 'end', len(arr), lo=0, hi=len(arr))
    if start > end:
        raise PineError('array.slice: start 不能大于 end')
    return list(arr[start:end])


@register('array.first')
def array_first(runtime, node, i):
    arr = _get_array(runtime, node, i)
    if not arr:
        raise PineError('array.first 不能作用于空数组')
    return arr[0]


@register('array.last')
def array_last(runtime, node, i):
    arr = _get_array(runtime, node, i)
    if not arr:
        raise PineError('array.last 不能作用于空数组')
    return arr[-1]
