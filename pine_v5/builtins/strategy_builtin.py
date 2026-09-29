"""Registered ``strategy.*`` builtins — wire Pine calls into the Broker.

These override the stub core implementations in ``runtime.py``.  Existing
call-sites (``strategy.entry(id, direction)``, ``strategy.close(id)``,
``strategy.exit(id, from_entry=..., stop=..., limit=...)``) keep working; the
new optional parameters (qty / qty_percent / profit / loss / trail_*) are
accepted by extending ``ALLOWED_KW`` and ``CONSTANTS`` at import time.
"""
from __future__ import annotations

from ..constants import ALLOWED_KW, CONSTANTS, PineError
from .registry import register


# ── Extend the parser's allowed-kwarg / constant tables at import time ────
ALLOWED_KW.setdefault('strategy.entry', set()).update(
    {'qty', 'limit', 'stop', 'comment'})
ALLOWED_KW.setdefault('strategy.exit', set()).update(
    {'qty', 'qty_percent', 'profit', 'loss', 'trail_points', 'trail_offset',
     'comment'})
ALLOWED_KW.setdefault('strategy.close', set()).update(
    {'qty', 'qty_percent', 'comment'})
ALLOWED_KW.setdefault('strategy.close_all', set()).update({'comment'})
ALLOWED_KW.setdefault('strategy.order', set()).update(
    {'qty', 'limit', 'stop', 'when'})
# NB: commission_value / commission_type / slippage are intentionally NOT added
# here — the existing V7 test suite asserts they are rejected at compile time.
# They are configurable at runtime on ``runtime.broker`` instead.
ALLOWED_KW.setdefault('strategy', set()).update(
    {'initial_capital', 'pyramiding', 'process_orders_on_close',
     'calc_on_every_tick'})

# Built-in strategy variables — added to CONSTANTS so the parser accepts them
# as identifiers; Runtime._eval_name intercepts them before the CONSTANTS
# lookup and delegates to the broker.
for _name in ('strategy.position_size', 'strategy.position_avg_price',
              'strategy.opentrades', 'strategy.closedtrades',
              'strategy.equity', 'strategy.gross_loss', 'strategy.gross_profit',
              'strategy.net_loss', 'strategy.net_profit',
              'strategy.wintrades', 'strategy.losstrades',
              'strategy.eventrades'):
    CONSTANTS.setdefault(_name, 0.0)


def _args(node):
    return node[2]


def _kw(node):
    return node[3]


def _get(runtime, node, i, idx, key, default=None):
    args = _args(node)
    kw = _kw(node)
    if key in kw:
        return runtime.evaluate(kw[key], i)
    if idx < len(args):
        return runtime.evaluate(args[idx], i)
    return default


@register('strategy')
def strategy_decl(runtime, node, i):
    args = _args(node)
    kw = _kw(node)
    # title = positional[0] (already consumed by core; we just configure)
    for flag in ('calc_on_every_tick', 'process_orders_on_close'):
        if flag in kw and runtime.evaluate(kw[flag], i):
            from ..constants import PineError
            raise PineError(flag + ' 当前收盘模式不支持')
    runtime.broker.configure(
        pyramiding=runtime.evaluate(kw['pyramiding'], i) if 'pyramiding' in kw else 0,
        initial_capital=runtime.evaluate(kw['initial_capital'], i) if 'initial_capital' in kw else None,
        commission_type=runtime.evaluate(kw['commission_type'], i) if 'commission_type' in kw else None,
        commission_value=runtime.evaluate(kw['commission_value'], i) if 'commission_value' in kw else None,
        slippage=runtime.evaluate(kw['slippage'], i) if 'slippage' in kw else None,
    )
    return None


@register('strategy.entry')
def strategy_entry(runtime, node, i):
    ident = _get(runtime, node, i, 0, 'id')
    direction = _get(runtime, node, i, 1, 'direction')
    qty = _get(runtime, node, i, 2, 'qty')
    limit = _get(runtime, node, i, 3, 'limit')
    stop = _get(runtime, node, i, 4, 'stop')
    when = _get(runtime, node, i, 5, 'when', True)
    comment = _get(runtime, node, i, 6, 'comment', '')
    return runtime.broker.handle_entry(
        str(ident), direction, qty=qty, limit=limit, stop=stop,
        when=when, comment=str(comment or ''), bar=i)


@register('strategy.order')
def strategy_order(runtime, node, i):
    ident = _get(runtime, node, i, 0, 'id')
    direction = _get(runtime, node, i, 1, 'direction')
    qty = _get(runtime, node, i, 2, 'qty')
    limit = _get(runtime, node, i, 3, 'limit')
    stop = _get(runtime, node, i, 4, 'stop')
    when = _get(runtime, node, i, 5, 'when', True)
    return runtime.broker.handle_order(
        str(ident), direction, qty=qty, limit=limit, stop=stop,
        when=when, bar=i)


@register('strategy.exit')
def strategy_exit(runtime, node, i):
    ident = _get(runtime, node, i, 0, 'id')
    from_entry = _get(runtime, node, i, 1, 'from_entry')
    qty = _get(runtime, node, i, 2, 'qty')
    qty_percent = _get(runtime, node, i, 3, 'qty_percent')
    limit = _get(runtime, node, i, 4, 'limit')
    stop = _get(runtime, node, i, 5, 'stop')
    profit = _get(runtime, node, i, 6, 'profit')
    loss = _get(runtime, node, i, 7, 'loss')
    trail_points = _get(runtime, node, i, 8, 'trail_points')
    trail_offset = _get(runtime, node, i, 9, 'trail_offset')
    when = _get(runtime, node, i, 10, 'when', True)
    comment = _get(runtime, node, i, 11, 'comment', '')
    return runtime.broker.handle_exit(
        str(ident), str(from_entry), qty=qty, qty_percent=qty_percent,
        limit=limit, stop=stop, profit=profit, loss=loss,
        trail_points=trail_points, trail_offset=trail_offset,
        when=when, comment=str(comment or ''), bar=i)


@register('strategy.close')
def strategy_close(runtime, node, i):
    ident = _get(runtime, node, i, 0, 'id')
    qty = _get(runtime, node, i, 1, 'qty')
    qty_percent = _get(runtime, node, i, 2, 'qty_percent')
    when = _get(runtime, node, i, 3, 'when', True)
    comment = _get(runtime, node, i, 4, 'comment', '')
    return runtime.broker.handle_close(
        str(ident), qty=qty, qty_percent=qty_percent, when=when,
        comment=str(comment or ''), bar=i)


@register('strategy.close_all')
def strategy_close_all(runtime, node, i):
    kw = _kw(node)
    when = runtime.evaluate(kw['when'], i) if 'when' in kw else True
    comment = runtime.evaluate(kw['comment'], i) if 'comment' in kw else ''
    return runtime.broker.handle_close_all(when=when, comment=str(comment or ''),
                                           bar=i)
