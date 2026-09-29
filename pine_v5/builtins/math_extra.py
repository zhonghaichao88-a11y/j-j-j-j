"""Pine v5 `math` namespace extras: trig, hyperbolic, mod, random."""
from __future__ import annotations
import math
import random as _random

from .registry import register
from ._helpers import PineError, isna, na, num, arg


def _wrap(fn):
    def handler(runtime, node, i):
        vals = [num(runtime.evaluate(a, i)) for a in node[2]]
        if any(isna(v) for v in vals):
            return na()
        try:
            return float(fn(*vals))
        except (ValueError, OverflowError, ZeroDivisionError):
            return na()
    return handler


register('math.sin')(_wrap(math.sin))
register('math.cos')(_wrap(math.cos))
register('math.tan')(_wrap(math.tan))
register('math.asin')(_wrap(math.asin))
register('math.acos')(_wrap(math.acos))
register('math.atan')(_wrap(math.atan))
register('math.sinh')(_wrap(math.sinh))
register('math.cosh')(_wrap(math.cosh))
register('math.atan2')(_wrap(math.atan2))


@register('math.mod')
def math_mod(runtime, node, i):
    x = num(runtime.evaluate(node[2][0], i))
    y = num(runtime.evaluate(node[2][1], i))
    if isna(x) or isna(y):
        return na()
    if y == 0:
        return na()
    return x % y


@register('math.random')
def math_random(runtime, node, i):
    lo = num(arg(runtime, node, i, 0, 'min', 0.0))
    hi = num(arg(runtime, node, i, 1, 'max', 1.0))
    if isna(lo) or isna(hi):
        return na()
    # Deterministic pseudo-randomness: seed derived from bar_index so the
    # same script on the same data is fully reproducible.
    rng = _random.Random((int(i) * 2654435761) & 0xFFFFFFFF)
    return lo + rng.random() * (hi - lo)
