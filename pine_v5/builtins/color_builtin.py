"""Pine v5 `color` namespace — colors are strings '#RRGGBB' / '#RRGGBBAA'.

Built-in color constants (color.red, ...) already pass through the runtime as
opaque name tokens; here we map them to concrete hex values so that
color.new / color.rgb / color.tostring can compose them.
"""
from __future__ import annotations

from .registry import register
from ._helpers import PineError, isna, na, num

_NAMED = {
    'color.red': '#FF0000',
    'color.green': '#008000',
    'color.blue': '#0000FF',
    'color.yellow': '#FFFF00',
    'color.orange': '#FFA500',
    'color.purple': '#800080',
    'color.teal': '#008080',
    'color.gray': '#808080',
    'color.grey': '#808080',
    'color.white': '#FFFFFF',
    'color.black': '#000000',
    'color.aqua': '#00FFFF',
    'color.silver': '#C0C0C0',
    'color.maroon': '#800000',
    'color.olive': '#808000',
    'color.lime': '#00FF00',
    'color.navy': '#000080',
    'color.fuchsia': '#FF00FF',
    'color.none': '#00000000',
}


def _normalize(color):
    """Return '#RRGGBB' (6-digit) hex for any supported color input."""
    if not isinstance(color, str):
        raise PineError('颜色必须是字符串')
    if color in _NAMED:
        return _NAMED[color][:7]
    if color.startswith('#') and len(color) in (7, 9):
        return color[:7].upper()
    raise PineError('无法解析的颜色：' + color)


def _alpha_hex(transp):
    """transp 0..100 -> two-digit alpha (00 opaque .. ff transparent)."""
    if transp is None or isna(transp):
        transp = 0
    transp = num(transp)
    if not 0 <= transp <= 100:
        raise PineError('透明度 transp 必须在 0..100 之间')
    alpha = round(255 * (100 - transp) / 100.0)
    return format(alpha, '02X')


@register('color.new')
def color_new(runtime, node, i):
    base = runtime.evaluate(node[2][0], i)
    transp = runtime.evaluate(node[2][1], i) if len(node[2]) > 1 else 0
    return _normalize(base) + _alpha_hex(transp)


@register('color.rgb')
def color_rgb(runtime, node, i):
    r = num(runtime.evaluate(node[2][0], i))
    g = num(runtime.evaluate(node[2][1], i))
    b = num(runtime.evaluate(node[2][2], i))
    transp = runtime.evaluate(node[2][3], i) if len(node[2]) > 3 else 0
    for v in (r, g, b):
        if isna(v):
            return na()
    rgb = '#' + ''.join(format(max(0, min(255, int(round(v)))), '02X')
                        for v in (r, g, b))
    return rgb + _alpha_hex(transp)


@register('color.from_gradient')
def color_from_gradient(runtime, node, i):
    """Simplified gradient: returns the first color stop.

    (colors, positions) are expected as arrays; a real multi-stop gradient
    interpolator is out of scope for this compatibility layer.
    """
    colors = runtime.evaluate(node[2][0], i)
    if isinstance(colors, list) and colors:
        return colors[0]
    return 'color.none'


@register('color.tostring')
def color_tostring(runtime, node, i):
    c = runtime.evaluate(node[2][0], i)
    if not isinstance(c, str):
        return 'na'
    return _NAMED.get(c, c)
