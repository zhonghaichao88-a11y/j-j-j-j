"""Pine v5 runtime — tree-walking interpreter with per-bar execution."""
from __future__ import annotations
import math
import numpy as np
from .constants import (
    PineError, _LoopBreak, _LoopContinue, CONSTANTS, BASES, ALIASES,
    normalize_timeframe, timeframe_milliseconds, truth, _pine_na_like,
)
from .parser import walk
from .builtins.registry import REGISTRY
from .broker import Broker
from .plot_objects import PlotStore

# strategy.* built-in variables delegated to the broker (resolved before the
# static CONSTANTS lookup so they stay dynamic per bar).
_BROKER_VARS = {
    'strategy.position_size', 'strategy.position_avg_price',
    'strategy.opentrades', 'strategy.closedtrades', 'strategy.equity',
    'strategy.gross_loss', 'strategy.gross_profit', 'strategy.net_loss',
    'strategy.net_profit', 'strategy.wintrades', 'strategy.losstrades',
    'strategy.eventrades',
}


class Runtime:
    def __init__(self, program, frame, inputs=None, timeframe='5m',
                 symbol='', frames=None):
        self.program = program
        self.f = frame
        self.n = len(frame['close'])
        self.inputs = inputs or {}
        self.timeframe = normalize_timeframe(timeframe)
        self.symbol = str(symbol or '')
        self.frames = {normalize_timeframe(k): v
                       for k, v in (frames or {}).items()}
        self.frames[self.timeframe] = frame
        # Backward compatibility: older callers supply frames without a
        # millisecond timestamp array. Derive one from 'time' if present,
        # otherwise from bar_index * timeframe, so the broker never sees a
        # missing 'ts'.
        if 'ts' not in frame:
            if 'time' in frame:
                frame['ts'] = np.asarray(frame['time'], dtype=np.int64)
            else:
                step = int(timeframe_milliseconds(self.timeframe))
                frame['ts'] = (np.arange(self.n, dtype=np.int64) * step)
        self.history = {}
        self.memo = {}
        self.env = {}
        self.i = 0
        self.plots = {}
        self.events = []
        self.entries = {}
        self.exit_orders = {}
        self.ops = 0
        self.assignment = ''
        self.line = 0
        self.synthetic = {}
        self.fn_depth = 0
        self.broker = Broker(self)
        self.plot_store = PlotStore(self)
        from .api import validate_inputs
        validate_inputs(program, self.inputs)
        self.input_specs = {}
        if self.n > 5000:
            raise PineError('最多5000根K线')

    # ── Expression evaluation ────────────────────────────────────────────
    def evaluate(self, node, i=None):
        self.ops += 1
        if self.ops > 2500000:
            raise PineError('计算预算超限，请精简脚本')
        i = self.i if i is None else i
        if i < 0:
            return float('nan')
        key = (id(node), i)
        if key in self.memo:
            return self.memo[key]
        kind = node[0]
        if kind == 'const':
            v = node[1]
        elif kind == 'name':
            v = self._eval_name(node[1], i)
        elif kind == 'history':
            offset = self.evaluate(node[2], i)
            if isinstance(offset, (int, float)) and math.isfinite(offset):
                # A finite offset must be a valid non-negative integer; a
                # negative or out-of-range value would read the future.
                if int(offset) != offset or not 0 <= offset <= 5000:
                    raise PineError('历史索引必须是0至5000整数，不能访问未来')
                v = self.evaluate(node[1], i - int(offset))
            else:
                # Dynamic offset that is still na (e.g. highestbars during
                # warm-up) yields na, matching Pine rather than erroring.
                v = float('nan')
        elif kind == 'list':
            v = [self.evaluate(x, i) for x in node[1]]
        elif kind == 'unary':
            x = self.evaluate(node[2], i)
            v = (not truth(x) if node[1] == 'not'
                 else -x if node[1] == '-' else x)
        elif kind == 'binary':
            op = node[1]
            a = self.evaluate(node[2], i)
            if op in ('and', 'or'):
                # Pine streams both operands every bar (no short-circuit) so
                # stateful ta.* calls inside either side accumulate correctly.
                b = self.evaluate(node[3], i)
                v = (truth(a) and truth(b)) if op == 'and' else (truth(a) or truth(b))
            else:
                b = self.evaluate(node[3], i)
                try:
                    v = {'+': lambda: a + b, '-': lambda: a - b,
                         '*': lambda: a * b, '/': lambda: a / b,
                         '%': lambda: a % b, '>': lambda: a > b,
                         '<': lambda: a < b, '>=': lambda: a >= b,
                         '<=': lambda: a <= b, '==': lambda: a == b,
                         '!=': lambda: a != b}[op]()
                except ZeroDivisionError:
                    v = float('nan')
        elif kind == 'ternary':
            # Pine evaluates both branches every bar (data stream), then
            # selects. Stateful indicators inside either side must run on
            # every bar so their history state stays continuous.
            cond = self.evaluate(node[1], i)
            yes = self.evaluate(node[2], i)
            no = self.evaluate(node[3], i)
            v = yes if truth(cond) else no
        elif kind == 'call':
            v = self.call(node, i)
        elif kind == 'field':
            obj = self.evaluate(node[1], i)
            field = node[2]
            if isinstance(obj, dict):
                if field not in obj:
                    raise PineError(
                        'UDT字段不存在 ' + field
                        + '；可用：' + ','.join(
                            k for k in obj if not k.startswith('__')))
                return obj[field]
            raise PineError('字段访问仅支持UDT实例')
        elif kind == 'udt_new':
            tname, args, kw = node[1], node[2], node[3]
            spec = self.program.get('types', {}).get(tname)
            if spec is None:
                # imported module UDT
                for mod in self.program.get('imports', {}).values():
                    spec = mod.get('types', {}).get(tname)
                    if spec:
                        break
            if spec is None:
                raise PineError('未知UDT类型 ' + tname)
            positional = [self.evaluate(a, i) for a in args]
            named = {k: self.evaluate(v, i) for k, v in kw.items()}
            return spec.new_instance(positional, named)
        else:
            raise PineError('内部表达式类型错误')
        # Mutable variable reads cannot be cached inside the current bar.
        if kind not in ('name', 'history'):
            self.memo[key] = v
        return v

    def _eval_name(self, name, i):
        if name in _BROKER_VARS:
            return self.broker.var(name)
        if name == 'timenow':
            import time as _time
            return int(_time.time() * 1000)
        if name in ('year', 'month', 'dayofmonth', 'dayofweek',
                    'hour', 'minute', 'second'):
            import datetime as _dt
            ts = self.f['ts'][i] if 'ts' in self.f else self.f['time'][i]
            if ts is None:
                return float('nan')
            dt = _dt.datetime.fromtimestamp(ts / 1000, tz=_dt.timezone.utc)
            return {'year': dt.year, 'month': dt.month,
                    'dayofmonth': dt.day, 'dayofweek': dt.weekday() + 2,
                    'hour': dt.hour, 'minute': dt.minute,
                    'second': dt.second}[name]
        if name == 'syminfo.mintick':
            return 0.01
        if name == 'syminfo.prefix':
            return 'BINANCE'
        if name in ('accdist', 'pvt'):
            # Built-in cumulative series
            high = self.f['high'][i]
            low = self.f['low'][i]
            close = self.f['close'][i]
            vol = self.f['volume'][i]
            key = ('builtin_' + name, name)
            state = self.synthetic.setdefault(key, {'cum': 0.0})
            if name == 'accdist':
                rng = high - low
                mfm = 0.0 if rng == 0 else ((close - low) - (high - close)) / rng
                state['cum'] += mfm * vol
            else:  # pvt
                if i > 0 and close != 0:
                    prev = self.f['close'][i - 1]
                    state['cum'] += (close - prev) / prev * vol
            return state['cum']
        if name in CONSTANTS:
            return CONSTANTS[name]
        if name == 'timeframe.period':
            return self.timeframe
        if name == 'timeframe.multiplier':
            match = __import__('re').match(r'^(\d+)', self.timeframe)
            return int(match.group(1)) if match else 1
        if name == 'timeframe.isintraday':
            return timeframe_milliseconds(self.timeframe) < 86400000
        if name == 'timeframe.isdaily':
            return timeframe_milliseconds(self.timeframe) >= 86400000
        if name == 'syminfo.tickerid':
            return self.symbol
        if name == 'syminfo.ticker':
            return self.symbol.split(':')[-1]
        if name.startswith('syminfo.'):
            raise PineError('当前数据源未提供 ' + name)
        if (name.startswith('timeframe.')
                and name not in ('timeframe.period', 'timeframe.multiplier',
                                 'timeframe.isintraday', 'timeframe.isdaily')):
            raise PineError('当前兼容层不支持 ' + name)
        if name in ('open', 'high', 'low', 'close', 'volume'):
            return float(self.f[name][i])
        if name == 'time':
            return int(self.f['ts'][i])
        if name == 'bar_index':
            return i
        if name == 'hl2':
            return (self.f['high'][i] + self.f['low'][i]) / 2
        if name == 'hlc3':
            return (self.f['high'][i] + self.f['low'][i]
                    + self.f['close'][i]) / 3
        if name == 'ohlc4':
            return sum(self.f[k][i]
                       for k in ('open', 'high', 'low', 'close')) / 4
        if name.startswith(('color.', 'plot.', 'shape.', 'location.',
                            'size.', 'display.', 'hline.', 'input.',
                            'barmerge.', 'format.', 'alert.', 'scale.',
                            'style.', 'order.', 'xloc.', 'yloc.',
                            'extend.', 'line.', 'label.', 'box.',
                            'table.', 'session.', 'currency.',
                            'direction.', 'strategy.', 'barstate.',
                            'ticker.', 'polyline.', 'linefill.')):
            return name
        return (self.env.get(name, float('nan'))
                if i == self.i
                else (self.history.get(name, [])[i]
                      if i < len(self.history.get(name, []))
                      else float('nan')))

    # ── request.security ─────────────────────────────────────────────────
    def request_security(self, node, i):
        key = ('security_result', id(node))
        cached = self.synthetic.get(key)
        name, args, kw = node[1:]
        symbol = self.evaluate(args[0], i)
        timeframe = self.evaluate(args[1], i)
        if not isinstance(timeframe, str):
            raise PineError('request.security周期必须是固定文本周期')
        requested_tf = normalize_timeframe(timeframe)
        requested_ms = timeframe_milliseconds(requested_tf)
        if (symbol and self.symbol
                and __import__('re').sub(r'[^A-Za-z0-9]', '', str(symbol)).upper()
                != __import__('re').sub(r'[^A-Za-z0-9]', '', self.symbol).upper()):
            raise PineError('request.security只读取当前图表合约；其他合约数据未提供')
        if cached:
            if (cached['timeframe'] != requested_tf
                    or cached['symbol'] != str(symbol)):
                raise PineError('request.security的合约与周期必须保持不变')
            return cached['values'][i]
        expression = args[2]
        gaps = self.evaluate(args[3], i) if len(args) > 3 else 'off'
        lookahead = self.evaluate(args[4], i) if len(args) > 4 else 'off'
        if lookahead != 'off':
            raise PineError('request.security仅支持lookahead_off，避免未来数据')
        if gaps not in ('off', 'on'):
            raise PineError('request.security gaps 参数无效')
        if any(x[0] == 'call' and x[1] == 'request.security'
               for x in walk(expression)):
            raise PineError('暂不支持嵌套request.security')
        source_frame = self.frames.get(requested_tf)
        if source_frame is None:
            chart_ms = timeframe_milliseconds(self.timeframe)
            if requested_ms < chart_ms:
                raise PineError(
                    '低于图表周期的request.security需要显式提供该周期K线')
            if requested_ms % chart_ms:
                raise PineError('request.security周期须为图表周期的整数倍')
            source_frame = self._aggregate_frame(self.f, requested_ms)
        if not all(k in source_frame
                   for k in ('ts', 'open', 'high', 'low', 'close', 'volume')):
            raise PineError('request.security周期数据字段不完整')
        source_frame = {k: np.asarray(v) for k, v in source_frame.items()}
        if len(source_frame['close']) > 5000:
            source_frame = {k: v[-5000:] for k, v in source_frame.items()}
        input_names = set(self.program.get('input_names', []))
        valid_names = BASES | set(CONSTANTS) | input_names
        valid_names.update(
            f"pinefn_{fn}_{param}"
            for fn, spec in self.program.get('functions', {}).items()
            for param in spec['params'])
        referenced = {x[1] for x in walk(expression) if x[0] == 'name'}
        expanded = set(referenced)
        for call in (x for x in walk(expression)
                     if x[0] == 'call'
                     and x[1] in self.program.get('functions', {})):
            expanded.update(
                x[1] for x in walk(self.program['functions'][call[1]]['expr'])
                if x[0] == 'name')
        unknown = {
            x for x in expanded
            if x not in valid_names
            and not x.startswith(('color.', 'plot.', 'shape.', 'location.',
                                  'size.', 'display.', 'hline.', 'input.',
                                  'barmerge.', 'syminfo.', 'timeframe.',
                                  'format.', 'alert.', 'scale.', 'style.',
                                  'order.', 'xloc.', 'yloc.', 'extend.',
                                  'line.', 'label.', 'box.', 'table.',
                                  'session.', 'currency.', 'direction.',
                                  'strategy.', 'barstate.', 'ticker.',
                                  'math.', 'ta.', 'str.', 'array.',
                                  'matrix.', 'map.', 'polyline.',
                                  'linefill.'))}
        if unknown:
            raise PineError(
                'request.security表达式引用了非输入的图表周期变量：'
                + ','.join(sorted(unknown)))
        child = Runtime(
            {'nodes': [], 'functions': self.program.get('functions', {}),
             'input_names': []},
            source_frame, {}, requested_tf,
            str(symbol or self.symbol), {})
        child.env = {n: self.env[n] for n in input_names if n in self.env}
        values = []
        for j in range(len(source_frame['close'])):
            child.i = j
            values.append(child.evaluate(expression, j))
        base_ms = timeframe_milliseconds(self.timeframe)
        base_close = np.asarray(self.f['ts'], dtype=np.int64) + base_ms
        requested_close = (np.asarray(source_frame['ts'], dtype=np.int64)
                           + requested_ms)
        merged = []
        last = float('nan')
        source_index = -1
        for close_time in base_close:
            previous = source_index
            while (source_index + 1 < len(requested_close)
                   and requested_close[source_index + 1] <= close_time):
                source_index += 1
            updated = source_index > previous
            if source_index >= 0:
                value = values[source_index]
                if gaps == 'off':
                    last = value
                merged.append(value if gaps == 'off'
                              else value if updated
                              else _pine_na_like(value))
            else:
                merged.append(last if gaps == 'off' else float('nan'))
        cached = {'timeframe': requested_tf, 'symbol': str(symbol),
                  'values': merged}
        self.synthetic[key] = cached
        return merged[i]

    @staticmethod
    def _aggregate_frame(frame, target_ms):
        ts = np.asarray(frame['ts'], dtype=np.int64)
        buckets = (ts // target_ms) * target_ms
        unique = np.unique(buckets)
        result = {k: [] for k in ('ts', 'open', 'high', 'low', 'close',
                                  'volume')}
        for bucket in unique:
            ids = np.flatnonzero(buckets == bucket)
            result['ts'].append(int(bucket))
            result['open'].append(float(frame['open'][ids[0]]))
            result['high'].append(float(np.max(np.asarray(frame['high'])[ids])))
            result['low'].append(float(np.min(np.asarray(frame['low'])[ids])))
            result['close'].append(float(frame['close'][ids[-1]]))
            result['volume'].append(
                float(np.sum(np.asarray(frame['volume'])[ids])))
        return {k: np.asarray(v) for k, v in result.items()}

    def window(self, node, n, i):
        if int(n) != n or not 1 <= n <= 5000:
            raise PineError('周期必须是1至5000整数')
        if i + 1 < n:
            return []
        return [self.evaluate(node, j) for j in range(i - int(n) + 1, i + 1)]

    # ── Function call dispatch ───────────────────────────────────────────
    def call(self, node, i):
        name, args, kw = node[1:]
        name = ALIASES.get(name, name)
        # Check extensible registry first (new builtins added by sub-agents)
        handler = REGISTRY.get(name)
        if handler is not None:
            return handler(self, node, i)
        get = lambda n, default=None: self.evaluate(args[n], i) if n < len(args) else default
        kval = lambda k, default=None: self.evaluate(kw[k], i) if k in kw else default

        if name == 'request.security':
            return self.request_security(node, i)
        # Imported local-library functions: module.func(...)
        if '.' in name:
            mod, fn = name.split('.', 1)
            modspec = self.program.get('imports', {}).get(mod)
            if modspec is not None and fn in modspec.get('functions', {}):
                return self._call_user_function(
                    fn, args, node, i, spec=modspec['functions'][fn])
        if name in self.program.get('functions', {}):
            return self._call_user_function(name, args, node, i)
        if name.startswith('array.'):
            return self.array_call(name, args, i)
        if name in ('indicator', 'study', 'strategy'):
            if (name == 'strategy'
                    and (kval('pyramiding', 0) != 0
                         or truth(kval('calc_on_every_tick', False))
                         or truth(kval('process_orders_on_close', False)))):
                raise PineError(
                    'V7只支持单持仓、收盘信号、下一报价执行；'
                    '不支持金字塔/逐tick/收盘撮合')
            return None
        if name.startswith('input'):
            return self._input_call(name, args, kw, get, kval, i)
        if name in ('plot', 'plotshape', 'hline'):
            return self._plot_call(name, args, kw, get, kval, i)
        if name == 'strategy.exit':
            return self._strategy_exit(args, kw, get, kval, i)
        if name in ('strategy.entry', 'strategy.close', 'strategy.close_all'):
            return self._strategy_order(name, args, kw, get, kval, i)
        if name == 'alertcondition':
            if truth(kval('condition', get(0))):
                self.events.append(dict(
                    kind='alert', title=str(kval('title', get(1, '提醒'))),
                    message=str(kval('message', get(2, ''))), bar=i,
                    known_at=int(self.f['ts'][i])))
            return None
        # ta.* indicators
        if name == 'ta.barssince':
            previous = self.memo.get((id(node), i - 1), float('nan'))
            return 0 if truth(get(0)) else previous + 1
        if name == 'ta.valuewhen':
            occurrence = get(2)
            if (isinstance(occurrence, bool)
                    or not math.isfinite(occurrence)
                    or int(occurrence) != occurrence
                    or not 0 <= occurrence <= 5000):
                raise PineError('occurrence必须为0至5000整数')
            matches = self.synthetic.setdefault(('matches', id(node)), [])
            if truth(get(0)):
                matches.append(get(1))
            return (matches[-1 - int(occurrence)]
                    if len(matches) > occurrence else float('nan'))
        if name == 'ta.stoch':
            highs = self.window(args[1], get(3), i)
            lows = self.window(args[2], get(3), i)
            if not highs:
                return float('nan')
            high = max(highs)
            low = min(lows)
            return 100 * (get(0) - low) / (high - low) if high != low else float('nan')
        if name == 'ta.linreg':
            values = self.window(args[0], get(1), i)
            offset = get(2)
            if not math.isfinite(offset) or int(offset) != offset:
                raise PineError('linreg offset必须为整数')
            if len(values) < 2:
                return float('nan')
            slope, intercept = np.polyfit(np.arange(len(values)), values, 1)
            return float(intercept + slope * (len(values) - 1 - offset))
        if name in ('ta.pivothigh', 'ta.pivotlow'):
            source = (('name', 'high' if name.endswith('high') else 'low')
                      if len(args) == 2 else args[0])
            left = get(0) if len(args) == 2 else get(1)
            right = get(1) if len(args) == 2 else get(2)
            if any(isinstance(x, bool) or not isinstance(x, (int, float))
                   or int(x) != x or x < 1 or x > 5000
                   for x in (left, right)):
                raise PineError('pivot左右确认长度须为1至5000整数')
            pivot = i - int(right)
            values = [self.evaluate(source, j)
                      for j in range(pivot - int(left),
                                     pivot + int(right) + 1)]
            if pivot - int(left) < 0:
                return float('nan')
            candidate = values[int(left)]
            extreme = (max(values) if name.endswith('high') else min(values))
            return (candidate if candidate == extreme
                    and values.count(extreme) == 1 else float('nan'))
        if name == 'ta.cum':
            previous = self.memo.get((id(node), i - 1), 0.)
            return previous + get(0)
        if name in ('ta.dmi', 'ta.supertrend'):
            state = self.synthetic.setdefault(('state', id(node)), {})
            for j in range(max(state, default=-1) + 1, i + 1):
                state[j] = self._trend_state(node, j)
            return state[i]
        if name in ('ta.correlation', 'ta.covariance'):
            n = get(2)
            a = self.window(args[0], n, i)
            b = self.window(args[1], n, i)
            if not a or not b:
                return float('nan')
            return float(
                np.corrcoef(a, b)[0, 1] if name.endswith('correlation')
                else np.cov(a, b, ddof=0)[0, 1])
        if name in ('ta.avg', 'math.avg', 'math.sum'):
            values = [self.evaluate(a, i) for a in args]
            if not values:
                raise PineError(name + '至少需要一个参数')
            return sum(values) / len(values) if name.endswith('avg') else sum(values)
        if name == 'math.sign':
            x = get(0)
            return 0 if x == 0 else 1 if x > 0 else -1
        if name == 'na':
            x = get(0)
            return (x is None
                    or isinstance(x, (int, float)) and not math.isfinite(x))
        if name == 'nz':
            x = get(0)
            return (get(1, 0) if x is None
                    or isinstance(x, (int, float)) and not math.isfinite(x)
                    else x)
        if name.startswith('math.') or name in ('abs', 'max', 'min', 'sqrt', 'round'):
            fn = {'abs': abs, 'max': max, 'min': min, 'sqrt': math.sqrt,
                  'pow': pow, 'log': math.log, 'log10': math.log10,
                  'exp': math.exp, 'tanh': math.tanh,
                  'round': lambda x: math.floor(x + .5),
                  'floor': math.floor, 'ceil': math.ceil}[name.split('.')[-1]]
            try:
                return fn(*[self.evaluate(a, i) for a in args])
            except (ValueError, OverflowError):
                return float('nan')
        if name in ('ta.crossover', 'ta.crossunder', 'ta.cross'):
            if len(args) != 2:
                raise PineError('交叉函数需要2个参数')
            a, b = get(0), get(1)
            pa = self.evaluate(args[0], i - 1)
            pb = self.evaluate(args[1], i - 1)
            up = pa <= pb and a > b
            down = pa >= pb and a < b
            return (up if name.endswith('crossover')
                    else down if name.endswith('crossunder')
                    else up or down)
        if name in ('ta.change', 'ta.roc'):
            n = get(1, 1)
            if (not isinstance(n, (int, float))
                    or not math.isfinite(n) or int(n) != n
                    or not 1 <= n <= 5000):
                raise PineError('变化周期必须为正整数，禁止未来数据')
            prev = self.evaluate(args[0], i - int(n))
            return (get(0) - prev if name == 'ta.change'
                    else (get(0) / prev - 1) * 100 if prev else float('nan'))
        if name in ('ta.tr', 'ta.atr'):
            h, l = float(self.f['high'][i]), float(self.f['low'][i])
            c = (float(self.f['close'][i - 1]) if i
                 else float(self.f['close'][i]))
            tr = max(h - l, abs(h - c), abs(l - c))
            if name == 'ta.tr':
                return tr if i or truth(get(0)) else float('nan')
            n = get(0, 14)
            if (not isinstance(n, (int, float))
                    or not math.isfinite(n) or int(n) != n
                    or not 1 <= n <= 5000):
                raise PineError('ATR周期须为1至5000整数')
            prev = self.memo.get((id(node), i - 1), float('nan'))
            if i + 1 < n:
                return float('nan')
            if not math.isfinite(prev):
                trs = []
                for j in range(i - int(n) + 1, i + 1):
                    hh, ll = self.f['high'][j], self.f['low'][j]
                    cc = self.f['close'][j - 1] if j else self.f['close'][j]
                    trs.append(max(hh - ll, abs(hh - cc), abs(ll - cc)))
                return float(np.mean(trs))
            return (prev * (n - 1) + tr) / n
        if name in ('ta.macd', 'ta.bb', 'ta.hma'):
            key = id(node)
            if key not in self.synthetic:
                def call(fn, *a):
                    return ('call', fn, list(a), {})
                if name == 'ta.macd':
                    if len(args) != 4:
                        raise PineError('MACD需要source/fast/slow/signal')
                    fast = call('ta.ema', args[0], args[1])
                    slow = call('ta.ema', args[0], args[2])
                    line = ('binary', '-', fast, slow)
                    signal = call('ta.ema', line, args[3])
                    result = ('list', [line, signal,
                                       ('binary', '-', line, signal)])
                elif name == 'ta.bb':
                    if len(args) != 3:
                        raise PineError('BB需要source/length/mult')
                    basis = call('ta.sma', args[0], args[1])
                    dev = ('binary', '*',
                           call('ta.stdev', args[0], args[1]), args[2])
                    result = ('list', [basis,
                                       ('binary', '+', basis, dev),
                                       ('binary', '-', basis, dev)])
                else:
                    if len(args) != 2:
                        raise PineError('HMA需要source/length')
                    length = get(1)
                    if (int(length) != length
                            or not 2 <= length <= 5000):
                        raise PineError('HMA周期须为2至5000整数')
                    half = call('ta.wma', args[0],
                                ('const', float(int(length) // 2)))
                    full = call('ta.wma', args[0], args[1])
                    diff = ('binary', '-',
                            ('binary', '*', ('const', 2.), half), full)
                    result = call('ta.wma', diff,
                                  ('const', float(int(math.sqrt(length)))))
                self.synthetic[key] = result
            return self.evaluate(self.synthetic[key], i)
        # Window-based indicators
        source = args[0] if args else ('name', 'close')
        n = get(1, 14)
        if name in ('ta.highest', 'ta.lowest') and len(args) == 1:
            n = get(0)
            source = ('name', 'high' if name.endswith('highest') else 'low')
        if (not isinstance(n, (int, float))
                or not math.isfinite(n) or n != int(n)
                or not 1 <= n <= 5000):
            raise PineError('指标周期无效')
        values = self.window(source, n, i)
        x = self.evaluate(source, i)
        if name in ('ta.ema', 'ta.rma'):
            previous = self.memo.get((id(node), i - 1), float('nan'))
            if (not isinstance(x, (int, float, np.number))
                    or not math.isfinite(x)):
                return previous
            if not math.isfinite(previous):
                return (x if name == 'ta.ema'
                        else float(np.mean(values)) if values else float('nan'))
            alpha = 2 / (n + 1) if name == 'ta.ema' else 1 / n
            return previous + alpha * (x - previous)
        if name == 'ta.rsi':
            if i < n:
                return float('nan')
            prevkey = ('rsi', id(node), i - 1)
            prev = self.memo.get(prevkey)
            change = x - self.evaluate(source, i - 1)
            if prev:
                up = (prev[0] * (n - 1) + max(change, 0)) / n
                down = (prev[1] * (n - 1) + max(-change, 0)) / n
            else:
                differences = [
                    self.evaluate(source, j) - self.evaluate(source, j - 1)
                    for j in range(i - int(n) + 1, i + 1)]
                up = float(np.mean([max(d, 0) for d in differences]))
                down = float(np.mean([max(-d, 0) for d in differences]))
            self.memo[('rsi', id(node), i)] = (up, down)
            return 100. if down == 0 else 100 * up / (up + down)
        if name in ('ta.rising', 'ta.falling'):
            prev = self.window(source, n + 1, i)
            if len(prev) < n + 1:
                return False
            return (all(prev[-1] > x for x in prev[:-1])
                    if name.endswith('rising')
                    else all(prev[-1] < x for x in prev[:-1]))
        if not values:
            return float('nan')
        if name == 'ta.cum':
            return float(np.sum(values))
        if name == 'ta.vwma':
            volumes = self.window(('name', 'volume'), n, i)
            denominator = sum(volumes)
            return (float(np.dot(values, volumes) / denominator)
                    if denominator else float('nan'))
        if name in ('ta.median', 'ta.mode'):
            return (float(np.median(values)) if name.endswith('median')
                    else float(max(set(values), key=values.count)))
        if name == 'ta.variance':
            return float(np.var(values, ddof=0 if truth(get(2, True)) else 1))
        if name == 'ta.dev':
            return float(np.mean(np.abs(np.asarray(values) - np.mean(values))))
        if name == 'ta.sma':
            return float(np.mean(values))
        if name == 'ta.stdev':
            return float(np.std(values, ddof=0 if truth(get(2, True)) else 1))
        if name == 'ta.sum':
            return sum(values)
        if name == 'ta.highest':
            return max(values)
        if name == 'ta.lowest':
            return min(values)
        if name == 'ta.wma':
            return float(np.average(
                values, weights=np.arange(1, len(values) + 1)))
        if name == 'ta.hma':
            half = ('call', 'ta.wma',
                    [source, ('const', float(max(1, int(n) // 2)))], {})
            full = ('call', 'ta.wma',
                    [source, ('const', float(int(n)))], {})
            diff = ('binary', '-',
                    ('binary', '*', ('const', 2.), half), full)
            return self.evaluate(
                ('call', 'ta.wma',
                 [diff, ('const', float(max(1, int(math.sqrt(n)))))], {}), i)
        raise PineError('函数尚未实现：' + name)

    def _call_user_function(self, name, args, node, i, spec=None):
        if spec is None:
            spec = self.program['functions'][name]
        defaults = spec.get('defaults', {})
        if len(args) > len(spec['params']):
            raise PineError(name + '函数实参数量过多')
        if len(args) < len(spec['params']):
            missing = spec['params'][len(args):]
            if any(p not in defaults for p in missing):
                raise PineError(name + '函数缺少实参且该形参无默认值')
        if self.fn_depth >= 32:
            raise PineError('用户函数调用深度超过32，可能存在递归循环')
        call_id = id(node)
        key = ('userfn', call_id, name)
        entry = self.synthetic.get(key)
        if entry is None:
            replacements = {}
            for idx, p in enumerate(spec['params']):
                replacements[f'pinefn_{name}_{p}'] = (
                    args[idx] if idx < len(args) else defaults[p])

            def replace(tree):
                if isinstance(tree, tuple):
                    if (len(tree) > 1 and tree[0] == 'name'
                            and tree[1] in replacements):
                        return replacements[tree[1]]
                    return tuple(
                        replace(x) if isinstance(x, (tuple, list, dict))
                        else x for x in tree)
                if isinstance(tree, list):
                    return [replace(x) for x in tree]
                if isinstance(tree, dict):
                    return {k: replace(v) for k, v in tree.items()}
                return tree
            if 'expr' in spec:
                entry = ('expr', replace(spec['expr']))
            else:
                entry = ('block', [replace(s) for s in spec['body']])
            self.synthetic[key] = entry
        self.fn_depth += 1
        old_env = dict(self.env)
        old_line = self.line
        old_assignment = self.assignment
        try:
            for idx, p in enumerate(spec['params']):
                valnode = args[idx] if idx < len(args) else defaults[p]
                self.env[f'pinefn_{name}_{p}'] = self.evaluate(valnode, i)
            if entry[0] == 'expr':
                return self.evaluate(entry[1], i)
            # Multi-statement body.
            block = entry[1]
            fnvars = spec.get('fnvar_names', [])
            for fn in fnvars:
                pk = ('fnvar', call_id, fn)
                if pk in self.synthetic:
                    self.env[fn] = self.synthetic[pk]
            result = float('nan')
            for stmt in block:
                kind = stmt['kind']
                if kind == 'expression':
                    result = self.evaluate(stmt['expr'], i)
                elif kind == 'assign':
                    names = stmt['names']
                    if stmt['persistent'] and all(n in self.env for n in names):
                        continue
                    value = self.evaluate(stmt['expr'], i)
                    values = value if len(names) > 1 else [value]
                    if len(values) != len(names):
                        raise PineError('元组长度不一致')
                    for nm, v in zip(names, values):
                        if nm != '_':
                            self.env[nm] = v
                else:
                    # if / for / while / break / continue
                    try:
                        self.execute([stmt])
                    except (PineError, TypeError, ValueError, KeyError,
                            IndexError) as exc:
                        raise PineError(
                            f'第{stmt["line"]}行：{exc}') from exc
            for fn in fnvars:
                self.synthetic[('fnvar', call_id, fn)] = self.env.get(fn)
            return result
        finally:
            self.fn_depth -= 1
            self.env.clear()
            self.env.update(old_env)
            self.line = old_line
            self.assignment = old_assignment

    def _input_call(self, name, args, kw, get, kval, i):
        value = self.inputs.get(self.assignment, kval('defval', get(0)))
        if name == 'input.source' and self.assignment in self.inputs:
            if value not in BASES - {'time', 'bar_index'}:
                raise PineError('source输入必须为支持的价格或成交量序列名')
            value = self.evaluate(('name', value), i)
        if name == 'input.int' and (
                not isinstance(value, (int, float)) or int(value) != value):
            raise PineError('整数输入无效')
        if name == 'input.bool' and not isinstance(value, bool):
            raise PineError('布尔输入必须为true/false')
        if name == 'input.string' and not isinstance(value, str):
            raise PineError('文本输入无效')
        if name in ('input.int', 'input.float'):
            if (isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)):
                raise PineError('数值输入无效')
            if (value < kval('minval', -math.inf)
                    or value > kval('maxval', math.inf)):
                raise PineError('输入超出minval/maxval')
        options = kval('options')
        if options is not None and value not in options:
            raise PineError('输入不在options选项中')
        kind = name.split('.')[-1]
        if kind == 'input':
            kind = ('bool' if isinstance(value, bool)
                    else 'string' if isinstance(value, str) else 'float')
        default = args[0] if args else kw.get('defval')
        display = self.inputs.get(
            self.assignment,
            default[1] if kind == 'source' and default
            and default[0] == 'name' else value)
        self.input_specs[self.assignment] = dict(
            name=self.assignment, type=kind,
            title=str(kval('title', get(1, self.assignment))),
            value=display, minval=kval('minval'), maxval=kval('maxval'),
            step=kval('step'), options=options)
        return value

    def _plot_call(self, name, args, kw, get, kval, i):
        value = kval('series', kval('price', get(0)))
        title = str(kval('title', get(1, self.assignment or name)))
        ident = str(self.line) + ':' + title
        rec = self.plots.setdefault(
            ident, dict(title=title, kind=name,
                        color=str(kval('color', 'color.teal')),
                        values=[None] * self.n,
                        text=str(kval('text', '信号'))))
        offset = kval('offset', 0)
        if (isinstance(offset, bool)
                or not isinstance(offset, (int, float, np.number))
                or not math.isfinite(offset) or int(offset) != offset
                or abs(offset) > 5000):
            raise PineError('绘图offset必须为-5000至5000整数')
        target = i + int(offset)
        if 0 <= target < self.n:
            rec['values'][target] = (
                bool(truth(value)) if name == 'plotshape'
                else float(value)
                if isinstance(value, (int, float, np.number))
                and math.isfinite(value) else None)
        return None

    def _strategy_exit(self, args, kw, get, kval, i):
        ident = str(kval('id', get(0, '')))
        from_entry = str(kval('from_entry', get(1, '')))
        if not ident or not from_entry:
            raise PineError('strategy.exit必须指定id和from_entry')
        order = self.exit_orders.get(ident)
        if order and order['from_entry'] != from_entry:
            raise PineError('strategy.exit同一id不能改绑其他entry')
        if not truth(kval('when', True)):
            return None
        stop = kval('stop')
        limit = kval('limit')
        if stop is None and limit is None:
            raise PineError(
                'strategy.exit当前仅支持stop/limit绝对价格；'
                'profit/loss/trailing止盈止损暂不支持')
        levels = {'stop': None, 'limit': None}
        for key, val in (('stop', stop), ('limit', limit)):
            if val is None:
                continue
            if (isinstance(val, bool)
                    or not isinstance(val, (int, float, np.number))):
                raise PineError('strategy.exit的stop/limit必须是有限正价格')
            if not math.isfinite(val):
                continue
            if val <= 0:
                raise PineError('strategy.exit的stop/limit必须是有限正价格')
            levels[key] = float(val)
        if levels['stop'] is None and levels['limit'] is None:
            return None
        levels.update(from_entry=from_entry, bar=i)
        self.exit_orders[ident] = levels
        return None

    def _strategy_order(self, name, args, kw, get, kval, i):
        if not truth(kval('when', True)):
            return None
        if name == 'strategy.close_all':
            self.events.append(dict(
                kind='close_all', bar=i,
                known_at=int(self.f['ts'][i]),
                comment=str(kval('comment', get(0, '')))))
            return None
        ident = str(kval('id', get(0, '')))
        if not ident:
            raise PineError('策略订单必须有id')
        if name.endswith('entry'):
            direction = kval('direction', get(1))
            if direction not in (1, -1):
                raise PineError('strategy.entry方向无效')
            self.entries[ident] = int(direction)
            self.events.append(dict(
                kind='entry', side=int(direction), id=ident, bar=i,
                known_at=int(self.f['ts'][i])))
        else:
            self.events.append(dict(
                kind='close', side=self.entries.get(ident),
                id=ident, bar=i, known_at=int(self.f['ts'][i])))
        return None

    # ── Array operations ─────────────────────────────────────────────────
    def array_call(self, name, args, i):
        values = [self.evaluate(x, i) for x in args]
        if name in ('array.new', 'array.new_float', 'array.new_int',
                    'array.new_bool', 'array.new_string', 'array.new_color'):
            if len(values) > 2:
                raise PineError(name + '最多接受size和initial_value')
            size = values[0] if values else 0
            if (isinstance(size, bool)
                    or not isinstance(size, (int, float))
                    or not math.isfinite(size) or int(size) != size
                    or not 0 <= size <= 10000):
                raise PineError('数组长度须为0至10000的整数')
            defaults = {'array.new_bool': False, 'array.new_string': '',
                        'array.new_color': 'color.none'}
            initial = values[1] if len(values) > 1 else defaults.get(
                name, float('nan'))
            return [initial for _ in range(int(size))]
        if name == 'array.from':
            return list(values)
        if name == 'array.concat':
            if (len(values) != 2
                    or not isinstance(values[0], list)
                    or not isinstance(values[1], list)):
                raise PineError('array.concat需要两个数组')
            if len(values[0]) + len(values[1]) > 100000:
                raise PineError('数组容量上限为100000')
            values[0].extend(values[1])
            return values[0]
        if not values or not isinstance(values[0], list):
            raise PineError(name + '需要数组ID')
        array = values[0]
        if name == 'array.size':
            return len(array)
        if name == 'array.clear':
            array.clear()
            return None
        if name == 'array.copy':
            return list(array)
        if name == 'array.reverse':
            array.reverse()
            return None
        if name == 'array.push':
            if len(values) != 2:
                raise PineError('array.push需要一个元素')
            if len(array) >= 100000:
                raise PineError('数组容量上限为100000')
            array.append(values[1])
            return None
        if name == 'array.unshift':
            if len(values) != 2:
                raise PineError('array.unshift需要一个元素')
            if len(array) >= 100000:
                raise PineError('数组容量上限为100000')
            array.insert(0, values[1])
            return None
        if name in ('array.pop', 'array.shift'):
            if not array:
                raise PineError(name + '不能从空数组取值')
            return array.pop() if name == 'array.pop' else array.pop(0)
        if name in ('array.sum', 'array.avg', 'array.max', 'array.min'):
            if not array:
                return float('nan')
            if name == 'array.sum':
                return sum(array)
            if name == 'array.avg':
                return float(np.mean(array))
            return max(array) if name == 'array.max' else min(array)
        if name == 'array.includes':
            return len(values) == 2 and values[1] in array
        if name == 'array.indexof':
            return (array.index(values[1])
                    if len(values) == 2 and values[1] in array else -1)
        if name in ('array.get', 'array.set', 'array.remove'):
            if (len(values) < 2 or isinstance(values[1], bool)
                    or not isinstance(values[1], (int, float))
                    or int(values[1]) != values[1]):
                raise PineError(name + '索引须为整数')
            index = int(values[1])
            if not 0 <= index < len(array):
                raise PineError(name + '索引越界')
            if name == 'array.get':
                return array[index]
            if name == 'array.remove':
                return array.pop(index)
            if len(values) != 3:
                raise PineError('array.set需要索引和值')
            array[index] = values[2]
            return None
        raise PineError('函数尚未实现：' + name)

    # ── Exit order processing ────────────────────────────────────────────
    def process_exit_orders(self, i):
        for ident, order in list(self.exit_orders.items()):
            if order['bar'] >= i:
                continue
            side = self.entries.get(order['from_entry'])
            if side not in (1, -1):
                continue
            stop = order['stop']
            limit = order['limit']
            hit_stop = (stop is not None
                        and ((side == 1
                              and float(self.f['low'][i]) <= stop)
                             or (side == -1
                                 and float(self.f['high'][i]) >= stop)))
            hit_limit = (limit is not None
                         and ((side == 1
                               and float(self.f['high'][i]) >= limit)
                              or (side == -1
                                  and float(self.f['low'][i]) <= limit)))
            if hit_stop or hit_limit:
                reason = 'stop' if hit_stop else 'limit'
                self.events.append(dict(
                    kind='close', side=side, id=order['from_entry'],
                    exit_id=ident,
                    fill=stop if hit_stop else limit,
                    fill_type=reason, bar=i,
                    known_at=int(self.f['ts'][i])))
                self.exit_orders.pop(ident, None)

    # ── DMI / Supertrend stateful computation ────────────────────────────
    def _trend_state(self, node, i):
        name, args, kw = node[1:]
        get = lambda j, default=None: self.evaluate(args[j], i) if j < len(args) else default
        history = self.synthetic.setdefault(('trend', id(node)), [])
        if name == 'ta.supertrend':
            factor, length = get(0), get(1)
            if (isinstance(length, bool)
                    or not isinstance(length, (int, float))
                    or int(length) != length or length < 1
                    or length > 5000):
                raise PineError('Supertrend ATR周期须为1至5000整数')
            atr_node = self.synthetic.setdefault(
                ('supertrend_atr', id(node)),
                ('call', 'ta.atr', [('const', float(length))], {}))
            atr = self.evaluate(atr_node, i)
            mid = (self.f['high'][i] + self.f['low'][i]) / 2
            if not math.isfinite(atr):
                history.append(None)
                return (float('nan'), float('nan'))
            upper = mid + factor * atr
            lower = mid - factor * atr
            previous = next((x for x in reversed(history) if x is not None), None)
            if previous:
                pu, pl, ps, pd = previous
                upper = (upper if upper < pu
                         or self.f['close'][i - 1] > pu else pu)
                lower = (lower if lower > pl
                         or self.f['close'][i - 1] < pl else pl)
                direction = (1 if pd < 0 and self.f['close'][i] < lower
                             else -1 if pd > 0 and self.f['close'][i] > upper
                             else pd)
            else:
                direction = 1
            line = lower if direction < 0 else upper
            result = (float(line), float(direction))
            history.append((float(upper), float(lower), *result))
            return result
        # ta.dmi
        di_length, smooth_length = get(0), get(1)
        if any(isinstance(x, bool) or not isinstance(x, (int, float))
               or int(x) != x or x < 1 or x > 5000
               for x in (di_length, smooth_length)):
            raise PineError('DMI周期须为1至5000整数')
        di_length, smooth_length = int(di_length), int(smooth_length)
        calc = self.synthetic.setdefault(
            ('dmi_calc', id(node)),
            dict(di=di_length, smooth=smooth_length, tr=[], plus=[],
                 minus=[], dx=[], smoothed=None, adx=None))
        if calc['di'] != di_length or calc['smooth'] != smooth_length:
            raise PineError('DMI周期须在运行期间保持不变')
        h, l = float(self.f['high'][i]), float(self.f['low'][i])
        c = float(self.f['close'][i - 1]) if i else float(self.f['close'][i])
        tr = max(h - l, abs(h - c), abs(l - c))
        up = h - float(self.f['high'][i - 1]) if i else 0.
        down = float(self.f['low'][i - 1]) - l if i else 0.
        plus = up if up > down and up > 0 else 0.
        minus = down if down > up and down > 0 else 0.
        calc['tr'].append(tr)
        calc['plus'].append(plus)
        calc['minus'].append(minus)
        if i + 1 < di_length:
            return (float('nan'),) * 3
        if i + 1 == di_length:
            smoothed = (sum(calc['tr']), sum(calc['plus']), sum(calc['minus']))
        else:
            pt, pp, pm = calc['smoothed']
            smoothed = (pt - pt / di_length + tr,
                        pp - pp / di_length + plus,
                        pm - pm / di_length + minus)
        calc['smoothed'] = smoothed
        st, sp, sm = smoothed
        p = 100 * sp / st if st else 0.
        m = 100 * sm / st if st else 0.
        dx = 100 * abs(p - m) / (p + m) if p + m else 0.
        calc['dx'].append(dx)
        if len(calc['dx']) == smooth_length:
            calc['adx'] = float(np.mean(calc['dx']))
        elif len(calc['dx']) > smooth_length:
            calc['adx'] = (calc['adx'] * (smooth_length - 1) + dx) / smooth_length
        return (float(p), float(m),
                float(calc['adx']) if calc['adx'] is not None else float('nan'))

    # ── Statement execution ──────────────────────────────────────────────
    def execute(self, nodes):
        for node in nodes:
            self.line = node['line']
            self.assignment = node.get('input_name', node.get('names', [''])[0])
            try:
                if node['kind'] == 'if':
                    self.execute(node['body']
                                 if truth(self.evaluate(node['expr']))
                                 else node['else'])
                elif node['kind'] == 'for':
                    start, end, step = self.evaluate(node['expr'])
                    if (any(isinstance(x, bool)
                            or not isinstance(x, (int, float, np.number))
                            or not math.isfinite(x) or int(x) != x
                            for x in (start, end, step)) or step == 0):
                        raise PineError(
                            'for循环起止和step须为有限整数，step不能为0')
                    start, end, step = int(start), int(end), int(step)
                    if (abs((end - start) // step
                            if (end - start) * step >= 0 else 0) > 10000):
                        raise PineError('for循环超过10000次安全上限')
                    old = self.env.get(node['name'], None)
                    had = node['name'] in self.env
                    try:
                        stop = end + 1 if step > 0 else end - 1
                        for loop_value in range(start, stop, step):
                            self.env[node['name']] = loop_value
                            self.memo.clear()
                            try:
                                self.execute(node['body'])
                            except _LoopContinue:
                                continue
                            except _LoopBreak:
                                break
                    finally:
                        if had:
                            self.env[node['name']] = old
                        else:
                            self.env.pop(node['name'], None)
                elif node['kind'] == 'while':
                    iterations = 0
                    while truth(self.evaluate(node['expr'])):
                        iterations += 1
                        if iterations > 10000:
                            raise PineError(
                                'while循环超过10000次安全上限')
                        self.memo.clear()
                        try:
                            self.execute(node['body'])
                        except _LoopContinue:
                            continue
                        except _LoopBreak:
                            break
                elif node['kind'] == 'switch':
                    selector = (self.evaluate(node['expr'])
                                if node['expr'] is not None else None)
                    matched = False
                    for case in node['cases']:
                        cond_ok = (selector == self.evaluate(case['cond'])
                                   if selector is not None
                                   else truth(self.evaluate(case['cond'])))
                        if cond_ok:
                            self.execute(case['body'])
                            matched = True
                            break
                    if not matched:
                        self.execute(node['default'])
                elif node['kind'] == 'break':
                    raise _LoopBreak()
                elif node['kind'] == 'continue':
                    raise _LoopContinue()
                elif node['kind'] == 'assign':
                    names = node['names']
                    if node['persistent'] and all(n in self.env for n in names):
                        continue
                    value = self.evaluate(node['expr'])
                    values = value if len(names) > 1 else [value]
                    if len(values) != len(names):
                        raise PineError('元组长度不一致')
                    for name, v in zip(names, values):
                        if name != '_':
                            self.env[name] = v
                else:
                    self.evaluate(node['expr'])
            except (PineError, TypeError, ValueError, KeyError, IndexError) as exc:
                raise PineError(f'第{node["line"]}行：{exc}') from exc

    def run(self):
        for i in range(self.n):
            self.i = i
            self.broker.process_bar(i)
            self.execute(self.program['nodes'])
            for name, value in self.env.items():
                arr = self.history.setdefault(name, [float('nan')] * i)
                arr.append(value)
        return dict(
            plots=list(self.plots.values()),
            events=self.events,
            inputs=self.inputs,
            input_specs=list(self.input_specs.values()),
            kind=self.program['kind'],
            sha256=self.program['sha256'],
            bars=self.n,
            broker=dict(
                positions=self.broker.positions,
                closed_trades=self.broker.closed_trades,
                equity_curve=self.broker.equity_curve,
                open_trades=len(self.broker.positions),
                closed_trades_count=len(self.broker.closed_trades),
                gross_profit=self.broker.gross_profit,
                gross_loss=self.broker.gross_loss,
                net_profit=self.broker.gross_profit - self.broker.gross_loss,
                win_trades=self.broker.win_trades,
                loss_trades=self.broker.loss_trades,
                event_trades=self.broker.event_trades,
            ),
            draw_objects=self.draw_objects,
            draw_events=self.draw_events,
            execution='已收盘K线；Broker保守撮合(次bar开盘/触发价)，对象状态可回放')
