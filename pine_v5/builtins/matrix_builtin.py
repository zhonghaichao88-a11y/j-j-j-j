"""Pine v5 `matrix` namespace — implemented as Python list-of-lists."""
from __future__ import annotations
import math

import numpy as np

from .registry import register
from ._helpers import PineError, isna, na, arg, arg_int


def _get_matrix(runtime, node, i, idx=0):
    v = runtime.evaluate(node[2][idx], i)
    if not isinstance(v, list) or not v or not isinstance(v[0], list):
        raise PineError(node[1] + ' 需要矩阵ID作为参数')
    return v


def _new_matrix(runtime, node, i, default):
    rows = arg_int(runtime, node, i, 0, 'rows', lo=0, hi=10000)
    cols = arg_int(runtime, node, i, 1, 'columns', lo=0, hi=10000)
    initial = arg(runtime, node, i, 2, 'initial_value', default)
    return [[initial for _ in range(cols)] for _ in range(rows)]


register('matrix.new')(
    lambda rt, nd, i: _new_matrix(rt, nd, i, float('nan')))
register('matrix.new_float')(
    lambda rt, nd, i: _new_matrix(rt, nd, i, float('nan')))
register('matrix.new_int')(
    lambda rt, nd, i: _new_matrix(rt, nd, i, 0))
register('matrix.new_bool')(
    lambda rt, nd, i: _new_matrix(rt, nd, i, False))
register('matrix.new_string')(
    lambda rt, nd, i: _new_matrix(rt, nd, i, ''))
register('matrix.new_color')(
    lambda rt, nd, i: _new_matrix(rt, nd, i, 'color.none'))


@register('matrix.get')
def matrix_get(runtime, node, i):
    m = _get_matrix(runtime, node, i)
    row = arg_int(runtime, node, i, 1, lo=0, hi=len(m) - 1)
    col = arg_int(runtime, node, i, 2, lo=0, hi=len(m[0]) - 1)
    return m[row][col]


@register('matrix.set')
def matrix_set(runtime, node, i):
    m = _get_matrix(runtime, node, i)
    row = arg_int(runtime, node, i, 1, lo=0, hi=len(m) - 1)
    col = arg_int(runtime, node, i, 2, lo=0, hi=len(m[0]) - 1)
    m[row][col] = runtime.evaluate(node[2][3], i)
    return None


@register('matrix.rows')
def matrix_rows(runtime, node, i):
    return len(_get_matrix(runtime, node, i))


@register('matrix.cols')
def matrix_cols(runtime, node, i):
    return len(_get_matrix(runtime, node, i)[0])


@register('matrix.fill')
def matrix_fill(runtime, node, i):
    m = _get_matrix(runtime, node, i)
    value = runtime.evaluate(node[2][1], i)
    for row in m:
        for j in range(len(row)):
            row[j] = value
    return None


@register('matrix.copy')
def matrix_copy(runtime, node, i):
    m = _get_matrix(runtime, node, i)
    return [list(r) for r in m]


@register('matrix.reshape')
def matrix_reshape(runtime, node, i):
    m = _get_matrix(runtime, node, i)
    rows = arg_int(runtime, node, i, 1, lo=1, hi=10000)
    cols = arg_int(runtime, node, i, 2, lo=1, hi=10000)
    flat = [v for row in m for v in row]
    if rows * cols != len(flat):
        raise PineError('matrix.reshape: 元素总数必须保持不变')
    return [flat[r * cols:(r + 1) * cols] for r in range(rows)]


@register('matrix.transpose')
def matrix_transpose(runtime, node, i):
    m = _get_matrix(runtime, node, i)
    return [list(col) for col in zip(*m)]


def _binary_elementwise(runtime, node, i, op):
    a = _get_matrix(runtime, node, i, 0)
    b = _get_matrix(runtime, node, i, 1)
    if len(a) != len(b) or len(a[0]) != len(b[0]):
        raise PineError('矩阵维度不一致')
    out = []
    for ra, rb in zip(a, b):
        out.append([op(x, y) for x, y in zip(ra, rb)])
    return out


@register('matrix.add')
def matrix_add(runtime, node, i):
    return _binary_elementwise(runtime, node, i, lambda x, y: x + y)


@register('matrix.sub')
def matrix_sub(runtime, node, i):
    return _binary_elementwise(runtime, node, i, lambda x, y: x - y)


@register('matrix.mul')
def matrix_mul(runtime, node, i):
    return _binary_elementwise(runtime, node, i, lambda x, y: x * y)


def _reduce(runtime, node, i, fn):
    m = _get_matrix(runtime, node, i)
    vals = [v for row in m for v in row
            if isinstance(v, (int, float)) and math.isfinite(v)]
    if not vals:
        return na()
    return fn(vals)


register('matrix.sum')(lambda rt, nd, i: _reduce(rt, nd, i, sum))
register('matrix.avg')(lambda rt, nd, i: _reduce(rt, nd, i, lambda v: float(np.mean(v))))
register('matrix.max')(lambda rt, nd, i: _reduce(rt, nd, i, max))
register('matrix.min')(lambda rt, nd, i: _reduce(rt, nd, i, min))


@register('matrix.clear')
def matrix_clear(runtime, node, i):
    m = _get_matrix(runtime, node, i)
    for row in m:
        for j in range(len(row)):
            row[j] = float('nan')
    return None
