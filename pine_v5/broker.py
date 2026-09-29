"""Strategy Broker — conservative bar-level order-matching simulator.

Design goals
------------
* No fake intrabar fills.  Signals produced on bar *i* are turned into orders
  that fill on a later bar using OHLC estimates.
* Same-bar conflict resolution is conservative: exits processed before entries,
  stops preferred over limits.
* Positions are tracked per entry id (Pine-style "entry slots").  ``pyramiding``
  controls how many same-direction entries may stack.
* Partial exits support both absolute ``qty`` and ``qty_percent``.
* OCA: when the stop or limit attached to an entry fills, the other is cancelled.
* Trailing stop adjusts the protective level as price moves favourably.
* Commissions / slippage are applied to fills.

The broker does *not* invent prices it cannot observe: a market order fills at
the next bar's open; a stop/limit order fills at its trigger price (conservative
assumption).  When both stop and limit are touched inside one bar the stop is
assumed to fill first.
"""
from __future__ import annotations

import math

from .constants import PineError, truth


def _finite_num(v):
    return (isinstance(v, (int, float))
            and not isinstance(v, bool)
            and math.isfinite(float(v)))


class Broker:
    def __init__(self, runtime):
        self.runtime = runtime
        # id -> position dict {side, qty, entry_price, entry_bar,
        #                      entry_fill, opens: int}
        self.positions = {}
        # pending orders (entry market/limit/stop) and protective exit orders
        self.pending_entries = []   # list of order dicts
        self.exit_orders = {}       # exit_id -> order dict (attached to entry)
        self.closed_trades = []     # realised trade records
        self.equity_curve = []
        # configuration
        self.initial_capital = 100000.0
        self.commission_rate = 0.0
        self.commission_type = 'percent'   # percent | cash
        self.slippage_ticks = 0
        self.tick_size = 0.01
        self.pyramiding_limit = 0
        # realised PnL accumulators
        self._realised_pnl = 0.0
        self.gross_profit = 0.0
        self.gross_loss = 0.0
        self.win_trades = 0
        self.loss_trades = 0
        self.event_trades = 0
        # pyramiding counter per (entry_id, side)
        self._pyramid_count = {}

    # ── configuration ────────────────────────────────────────────────────
    def configure(self, **kw):
        if 'pyramiding' in kw and kw['pyramiding'] is not None:
            v = kw['pyramiding']
            if not _finite_num(v) or int(v) < 0:
                raise PineError('pyramiding 必须为非负整数')
            self.pyramiding_limit = int(v)
        if 'initial_capital' in kw and kw['initial_capital'] is not None:
            v = kw['initial_capital']
            if not _finite_num(v) or v <= 0:
                raise PineError('initial_capital 必须为正数')
            self.initial_capital = float(v)
        if 'commission_type' in kw and kw['commission_type'] is not None:
            self.commission_type = str(kw['commission_type'])
        if 'commission_value' in kw and kw['commission_value'] is not None:
            v = kw['commission_value']
            if _finite_num(v) and v >= 0:
                self.commission_rate = float(v)
        if 'slippage' in kw and kw['slippage'] is not None:
            v = kw['slippage']
            if _finite_num(v) and v >= 0:
                self.slippage_ticks = int(v)
        return None

    # ── helpers ───────────────────────────────────────────────────────────
    @property
    def _total_qty(self):
        return sum(p['qty'] for p in self.positions.values())

    def _net_side_qty(self):
        """Signed total position size (sum across entry slots)."""
        total = 0.0
        for p in self.positions.values():
            total += p['side'] * p['qty']
        return total

    def _avg_price(self):
        if not self.positions:
            return 0.0
        # weighted average across slots (only meaningful when all same side)
        gross = sum(p['qty'] * p['entry_price'] for p in self.positions.values())
        qty = sum(p['qty'] for p in self.positions.values())
        return gross / qty if qty else 0.0

    def _apply_slippage(self, price, side):
        # side = 1 -> we buy (pay up); side = -1 -> we sell (receive less)
        slip = self.slippage_ticks * self.tick_size
        return price + side * slip

    def _commission(self, fill_price, qty):
        if self.commission_type == 'percent':
            return fill_price * qty * self.commission_rate / 100.0
        if self.commission_type == 'cash':
            return self.commission_rate * qty
        return 0.0

    # ── order entry points (called from builtins) ────────────────────────
    def handle_entry(self, ident, direction, qty=None, limit=None, stop=None,
                     when=True, comment='', bar=None):
        if not truth(when):
            return None
        if direction not in (1, -1):
            raise PineError('strategy.entry方向无效')
        if bar is None:
            bar = self.runtime.i
        # normalise numeric params
        lim = float(limit) if _finite_num(limit) else None
        stp = float(stop) if _finite_num(stop) else None
        q = float(qty) if _finite_num(qty) else None
        if q is not None and q <= 0:
            raise PineError('qty 必须为正数')
        if limit is not None and (lim is None or lim <= 0):
            raise PineError('limit 必须为有限正数')
        if stop is not None and (stp is None or stp <= 0):
            raise PineError('stop 必须为有限正数')
        if lim is not None and stp is not None:
            raise PineError('暂不支持stop-limit组合订单')
        # A repeated order ID updates the pending order; it must not queue duplicates.
        self.pending_entries = [o for o in self.pending_entries
                                if not (o['kind'] == 'entry' and o['id'] == ident)]
        count = sum(self._pyramid_count.get(k, 1) for k, p in self.positions.items()
                    if p['side'] == direction)
        if count >= max(1, self.pyramiding_limit):
            return None
        # emit signal event (matches legacy event contract)
        self.runtime.events.append(dict(
            kind='entry', side=int(direction), id=ident, bar=bar,
            known_at=int(self.runtime.f['ts'][bar]),
            comment=str(comment), qty=q, limit=lim, stop=stp))
        order = dict(kind='entry', id=ident, side=int(direction), qty=q,
                     limit=lim, stop=stp, placed_bar=bar,
                     oca_key=ident)
        self.pending_entries.append(order)
        return None

    def handle_order(self, ident, direction, qty=None, limit=None, stop=None,
                     when=True, bar=None):
        raise PineError('strategy.order净额订单语义尚未实现，请使用strategy.entry或仅预览其他脚本')

    def handle_exit(self, ident, from_entry, qty=None, qty_percent=None,
                    limit=None, stop=None, profit=None, loss=None,
                    trail_points=None, trail_offset=None, when=True,
                    comment='', bar=None):
        if not truth(when):
            return None
        if bar is None:
            bar = self.runtime.i
        if not ident or not from_entry:
            raise PineError('strategy.exit必须指定id和from_entry')
        pos = self.positions.get(from_entry)
        # Build protective levels.  profit/loss are expressed in ticks and
        # reference the position entry price once the position exists.  We
        # store them as tick-based and materialise absolute prices on the bar
        # the position opens (lazy evaluation at fill time).
        lim = float(limit) if _finite_num(limit) else None
        stp = float(stop) if _finite_num(stop) else None
        pf = float(profit) if _finite_num(profit) else None
        ls = float(loss) if _finite_num(loss) else None
        tp = float(trail_points) if _finite_num(trail_points) else None
        to = float(trail_offset) if _finite_num(trail_offset) else None
        q = float(qty) if _finite_num(qty) else None
        qp = float(qty_percent) if _finite_num(qty_percent) else None
        for name, value in (('qty', q), ('qty_percent', qp)):
            if value is not None and (value <= 0 or name == 'qty_percent' and value > 100):
                raise PineError(name + ' 超出有效范围')
        if tp is not None or to is not None:
            if tp is None or to is None or tp < 0 or to <= 0:
                raise PineError('追踪止损必须同时指定非负trail_points和正trail_offset')
        if all(v is None for v in (lim, stp, pf, ls, tp)):
            # Explicit na levels are valid while a dynamic exit is not ready.
            if any(v is not None for v in (limit, stop, profit, loss, trail_points)):
                return None
            raise PineError('strategy.exit缺少有效退出价格')
        prev = self.exit_orders.get(ident)
        order = dict(
            kind='exit', id=ident, from_entry=from_entry, qty=q, qty_percent=qp,
            limit=lim, stop=stp, profit_ticks=pf, loss_ticks=ls,
            trail_points=tp, trail_offset=to,
            placed_bar=bar, comment=str(comment),
            # preserve trailing runtime state across repeated declarations
            cur_stop=(prev['cur_stop'] if prev and tp is not None else None),
            cur_limit=None,
            best=(prev['best'] if prev else None),
            activated=(prev['activated'] if prev else False))
        # If a position is already open, materialise tick-based levels now.
        pos = self.positions.get(from_entry)
        if pos is not None:
            self._materialise_exit_levels(order, pos)
        self.exit_orders[ident] = order
        return None

    def _materialise_exit_levels(self, order, pos):
        """Translate profit/loss ticks into absolute prices for an open pos."""
        side = pos['side']
        ep = pos['entry_price']
        if order['profit_ticks'] is not None and order['limit'] is None:
            order['limit'] = ep + side * order['profit_ticks'] * self.tick_size
        if order['loss_ticks'] is not None and order['stop'] is None:
            order['stop'] = ep - side * order['loss_ticks'] * self.tick_size
        # initialise trailing state
        if order['trail_points'] is not None and order['best'] is None:
            order['best'] = ep
        order['cur_limit'] = order['limit']

    def handle_close(self, ident, qty=None, qty_percent=None, when=True,
                     comment='', bar=None):
        if not truth(when):
            return None
        if bar is None:
            bar = self.runtime.i
        pos = self.positions.get(ident)
        side = pos['side'] if pos else self.runtime.entries.get(ident)
        q = float(qty) if _finite_num(qty) else None
        qp = float(qty_percent) if _finite_num(qty_percent) else None
        if q is not None and q <= 0 or qp is not None and not 0 < qp <= 100:
            raise PineError('部分平仓数量必须为正，比例不得超过100')
        # Preserve quantity metadata for every downstream consumer.
        self.runtime.events.append(dict(
            kind='close', side=side, id=ident, bar=bar,
            known_at=int(self.runtime.f['ts'][bar]),
            comment=str(comment), qty=q, qty_percent=qp))
        q = float(qty) if _finite_num(qty) else None
        qp = float(qty_percent) if _finite_num(qty_percent) else None
        self.pending_entries.append(dict(
            kind='close', id=ident, qty=q, qty_percent=qp, placed_bar=bar,
            comment=str(comment)))
        return None

    def handle_close_all(self, when=True, comment='', bar=None):
        if not truth(when):
            return None
        if bar is None:
            bar = self.runtime.i
        self.runtime.events.append(dict(
            kind='close_all', bar=bar,
            known_at=int(self.runtime.f['ts'][bar]),
            comment=str(comment)))
        for ident, pos in list(self.positions.items()):
            self.pending_entries.append(dict(
                kind='close', id=ident, qty=None, qty_percent=100.0,
                placed_bar=bar, comment=str(comment)))
        return None

    # ── per-bar processing (called at start of bar i) ────────────────────
    def process_bar(self, i):
        f = self.runtime.f
        o = float(f['open'][i]); h = float(f['high'][i])
        low = float(f['low'][i]); c = float(f['close'][i])
        # 1) fill pending entry / close orders placed on earlier bars
        still = []
        for order in self.pending_entries:
            if order['placed_bar'] >= i:
                still.append(order)
                continue
            filled = self._fill_entry_order(order, i, o, h, low, c)
            if not filled:
                still.append(order)
        self.pending_entries = still
        # 2) process protective exit orders against this bar (conservative:
        #    stop before limit when both are touched)
        self._process_exits(i, o, h, low, c)
        # 3) record equity
        self._record_equity(i, c)

    def _fill_entry_order(self, order, i, o, h, low, c):
        kind = order['kind']
        ident = order['id']
        if kind == 'close':
            pos = self.positions.get(ident)
            if pos is None or pos['qty'] <= 0:
                # nothing to close (position may not have opened yet)
                return True
            fill_price = self._apply_slippage(o, -pos['side'])
            qty = self._resolve_exit_qty(pos, order.get('qty'),
                                          order.get('qty_percent'))
            if qty <= 0:
                return True
            self._close_position(ident, pos, qty, fill_price, 'market_close', i,
                                 record_event=False)
            return True
        # entry order
        side = order['side']
        count = sum(self._pyramid_count.get(k, 1) for k, p in self.positions.items() if p['side'] == side)
        if count >= max(1, self.pyramiding_limit):
            return True
        limit = order.get('limit')
        stop = order.get('stop')
        if limit is not None and stop is not None:
            # stop-limit order: wait for stop trigger then rest a limit
            raise PineError('暂不支持stop-limit组合订单')
        if limit is None and stop is None:
            # market order -> fill at open of this bar
            fill = self._apply_slippage(o, side)
            self._open_position(ident, side, order, fill, i)
            return True
        # limit order: long buy-limit fills when low <= limit;
        # short sell-limit fills when high >= limit
        if limit is not None:
            if (side == 1 and low <= limit) or (side == -1 and h >= limit):
                fill = min(o, limit) if side == 1 else max(o, limit)
                self._open_position(ident, side, order, fill, i)
                return True
            return False
        # stop order: long buy-stop fills when high >= stop;
        # short sell-stop fills when low <= stop
        if stop is not None:
            if (side == 1 and h >= stop) or (side == -1 and low <= stop):
                fill = self._apply_slippage(max(o, stop) if side == 1 else min(o, stop), side)
                self._open_position(ident, side, order, fill, i)
                return True
            return False
        return False

    def _open_position(self, ident, side, order, fill_price, i):
        qty = order.get('qty')
        if qty is None or qty <= 0:
            # default: one contract (a full-notional default is noted as a
            # known limitation; explicit qty is the supported path)
            qty = 1.0
        for old_id, old_pos in list(self.positions.items()):
            if old_pos['side'] != side:
                self._close_position(old_id, old_pos, old_pos['qty'], fill_price, 'reverse', i, record_event=False)
        pos = self.positions.get(ident)
        if pos is None:
            self.positions[ident] = dict(
                side=side, qty=float(qty), entry_price=float(fill_price),
                entry_bar=i, entry_fill=float(fill_price))
            self._pyramid_count[ident] = 1
        else:
            if pos['side'] == side:
                # pyramiding add — average up/down
                total = pos['qty'] + qty
                pos['entry_price'] = (
                    pos['qty'] * pos['entry_price'] + qty * fill_price) / total
                pos['qty'] = total
                self._pyramid_count[ident] = self._pyramid_count.get(ident, 1) + 1
            else:
                # opposite side: close existing then reverse
                self._close_position(ident, pos, pos['qty'], fill_price,
                                     'reverse', i, record_event=False)
                self.positions[ident] = dict(
                    side=side, qty=float(qty), entry_price=float(fill_price),
                    entry_bar=i, entry_fill=float(fill_price))
                self._pyramid_count[ident] = 1
        # materialise any exit orders already attached to this entry
        for ex in self.exit_orders.values():
            if ex['from_entry'] == ident and ex['cur_stop'] is None:
                self._materialise_exit_levels(ex, self.positions[ident])

    def _resolve_exit_qty(self, pos, qty, qty_percent):
        if qty_percent is not None:
            return min(pos['qty'], pos['qty'] * max(0.0, min(100.0, qty_percent)) / 100.0)
        if qty is not None:
            return min(qty, pos['qty'])
        return pos['qty']

    def _close_position(self, ident, pos, qty, fill_price, fill_type, i,
                        record_event=True):
        side = pos['side']
        entry = pos['entry_price']
        pnl = side * (fill_price - entry) * qty
        comm = self._commission(fill_price, qty) + self._commission(entry, qty)
        pnl -= comm
        pos['qty'] -= qty
        self._realised_pnl += pnl
        if pnl > 0:
            self.gross_profit += pnl
            self.win_trades += 1
        elif pnl < 0:
            self.gross_loss += -pnl
            self.loss_trades += 1
        else:
            self.event_trades += 1
        self.closed_trades.append(dict(
            id=ident, side=side, qty=qty, entry_price=entry,
            exit_price=fill_price, pnl=pnl, entry_bar=pos['entry_bar'],
            exit_bar=i, reason=fill_type))
        # Only protective bracket fills (stop/limit/trailing) emit an event
        # with fill/fill_type.  Plain strategy.close/close_all already emit a
        # signal event at the signal bar; emitting a second fill event there
        # would double-count.
        if record_event:
            self.runtime.events.append(dict(
                kind='close', side=side, id=ident, fill=float(fill_price),
                fill_type=fill_type, qty=float(qty), bar=i,
                known_at=int(self.runtime.f['ts'][i]), pnl=pnl))
        if pos['qty'] <= 1e-9:
            self.positions.pop(ident, None)
            # cancel any exit orders attached to this slot
            for ex_id in [eid for eid, e in self.exit_orders.items()
                          if e['from_entry'] == ident]:
                self.exit_orders.pop(ex_id, None)

    def _process_exits(self, i, o, h, low, c):
        for ex_id, order in list(self.exit_orders.items()):
            ident = order['from_entry']
            pos = self.positions.get(ident)
            if pos is None:
                # position not open yet; wait
                continue
            if order['placed_bar'] >= i:
                continue
            side = pos['side']
            # Check the stop/limit levels established at the END of the
            # previous bar (conservative: no same-bar look-ahead).
            stop = order['cur_stop'] if order['cur_stop'] is not None else order['stop']
            limit = order['cur_limit'] if order['cur_limit'] is not None else order['limit']
            hit_stop = (stop is not None and (
                (side == 1 and low <= stop) or (side == -1 and h >= stop)))
            hit_limit = (limit is not None and (
                (side == 1 and h >= limit) or (side == -1 and low <= limit)))
            if not (hit_stop or hit_limit):
                # trailing stop trails *after* this bar's range is observed,
                # becoming active on the following bar.
                if order['trail_points'] is not None:
                    self._update_trailing(order, pos, h, low, c)
                continue
            # conservative: stop first
            if hit_stop:
                fill = min(o, stop) if side == 1 else max(o, stop)
                reason = 'stop'
            else:
                fill = max(o, limit) if side == 1 else min(o, limit)
                reason = 'limit'
            fill = self._apply_slippage(fill, -side)
            qty = self._resolve_exit_qty(pos, order['qty'], order['qty_percent'])
            self._close_position(ident, pos, qty, fill, reason, i)
            # OCA: cancel sibling (the other bracket leg)
            self.exit_orders.pop(ex_id, None)

    def _update_trailing(self, order, pos, h, low, c):
        side = pos['side']
        activation = pos['entry_price'] + side * order['trail_points'] * self.tick_size
        extreme = h if side == 1 else low
        if not order['activated']:
            if side * (extreme - activation) < 0:
                return
            order['activated'] = True
            order['best'] = extreme
        else:
            order['best'] = max(order['best'], h) if side == 1 else min(order['best'], low)
        new_stop = order['best'] - side * order['trail_offset'] * self.tick_size
        cur = order['cur_stop'] if order['cur_stop'] is not None else order['stop']
        if cur is None or side * (new_stop - cur) > 0:
            order['cur_stop'] = new_stop

    def _record_equity(self, i, close):
        unrealised = 0.0
        for pos in self.positions.values():
            unrealised += pos['side'] * (close - pos['entry_price']) * pos['qty']
        equity = self.initial_capital + self._realised_pnl + unrealised
        self.equity_curve.append(float(equity))

    # ── built-in variable resolution ────────────────────────────────────
    def var(self, name):
        net = self._net_side_qty()
        open_count = len(self.positions)
        closed_count = len(self.closed_trades)
        if name == 'strategy.position_size':
            return net
        if name == 'strategy.position_avg_price':
            return self._avg_price() if self.positions else 0.0
        if name == 'strategy.opentrades':
            return open_count
        if name == 'strategy.closedtrades':
            return closed_count
        if name == 'strategy.equity':
            if self.equity_curve:
                return self.equity_curve[-1]
            return self.initial_capital + self._realised_pnl
        if name == 'strategy.gross_loss':
            return self.gross_loss
        if name == 'strategy.gross_profit':
            return self.gross_profit
        if name == 'strategy.net_loss':
            return self.gross_loss
        if name == 'strategy.net_profit':
            return self.gross_profit - self.gross_loss
        if name == 'strategy.wintrades':
            return self.win_trades
        if name == 'strategy.losstrades':
            return self.loss_trades
        if name == 'strategy.eventrades':
            return self.event_trades
        raise PineError('未知策略变量 ' + name)
