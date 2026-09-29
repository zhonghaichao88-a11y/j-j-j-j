"""Additional plot functions: plotchar, fill, bgcolor, barcolor.

Registered via the builtin registry so they take precedence over the
core runtime's fallback.  These are lightweight — they record plot
state but do not render.
"""
from __future__ import annotations
import math
import numpy as np
from .registry import register
from ..constants import truth, PineError


def _get_arg(runtime, node, i, idx, key=None, default=None):
    if key is not None and key in node[3]:
        return runtime.evaluate(node[3][key], i)
    if idx < len(node[2]):
        return runtime.evaluate(node[2][idx], i)
    return default


@register('plotchar')
def plotchar(runtime, node, i):
    series = _get_arg(runtime, node, i, 0, 'series')
    title = str(_get_arg(runtime, node, i, 1, 'title',
                         runtime.assignment or 'plotchar'))
    char = str(_get_arg(runtime, node, i, 2, 'char', '●'))
    ident = str(runtime.line) + ':' + title
    rec = runtime.plots.setdefault(
        ident, dict(title=title, kind='plotchar',
                    color=str(_get_arg(runtime, node, i, 3, 'color',
                                        'color.teal')),
                    char=char,
                    values=[None] * runtime.n))
    offset = _get_arg(runtime, node, i, 4, 'offset', 0)
    if (isinstance(offset, bool)
            or not isinstance(offset, (int, float, np.number))
            or not math.isfinite(offset) or int(offset) != offset
            or abs(offset) > 5000):
        raise PineError('绘图offset必须为-5000至5000整数')
    target = i + int(offset)
    if 0 <= target < runtime.n:
        rec['values'][target] = (
            char if truth(series) else None)
    return None


@register('fill')
def fill(runtime, node, i):
    # fill(plot1, plot2, color=..., title=...)
    # plot1/plot2 are typically plot IDs returned by plot() calls.
    # In our runtime, plot() returns None, so fill is a no-op that
    # records the fill request for potential later rendering.
    color = _get_arg(runtime, node, i, 2, 'color', 'color.gray')
    title = str(_get_arg(runtime, node, i, 3, 'title', 'fill'))
    ident = 'fill:' + str(runtime.line) + ':' + title
    runtime.plots.setdefault(
        ident, dict(title=title, kind='fill',
                    color=str(color), values=[None] * runtime.n,
                    fill_between=True))
    return None


@register('bgcolor')
def bgcolor(runtime, node, i):
    color = _get_arg(runtime, node, i, 0, 'color')
    title = str(_get_arg(runtime, node, i, 1, 'title', 'bgcolor'))
    ident = 'bgcolor:' + str(runtime.line) + ':' + title
    rec = runtime.plots.setdefault(
        ident, dict(title=title, kind='bgcolor',
                    values=[None] * runtime.n))
    offset = _get_arg(runtime, node, i, 2, 'offset', 0)
    if (isinstance(offset, (int, float, np.number))
            and math.isfinite(offset) and int(offset) == offset
            and 0 <= i + int(offset) < runtime.n):
        rec['values'][i + int(offset)] = str(color) if color else None
    return None


@register('barcolor')
def barcolor(runtime, node, i):
    color = _get_arg(runtime, node, i, 0, 'color')
    title = str(_get_arg(runtime, node, i, 1, 'title', 'barcolor'))
    ident = 'barcolor:' + str(runtime.line) + ':' + title
    rec = runtime.plots.setdefault(
        ident, dict(title=title, kind='barcolor',
                    values=[None] * runtime.n))
    offset = _get_arg(runtime, node, i, 2, 'offset', 0)
    if (isinstance(offset, (int, float, np.number))
            and math.isfinite(offset) and int(offset) == offset
            and 0 <= i + int(offset) < runtime.n):
        rec['values'][i + int(offset)] = str(color) if color else None
    return None
