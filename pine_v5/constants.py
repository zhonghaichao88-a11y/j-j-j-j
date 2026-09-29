"""Pine v5 compatibility layer — shared constants, registries, and helpers."""
from __future__ import annotations
import re, math

class PineError(ValueError):
    pass

class _LoopBreak(Exception):
    pass

class _LoopContinue(Exception):
    pass

# ── Tokenizer ──────────────────────────────────────────────────────────────
TOK = re.compile(
    r'\s*(?:(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+)'
    r'|("(?:\\.|[^"\\])*"|\x27(?:\\.|[^\x27\\])*\x27)'
    r'|(#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?)'
    r'|([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)'
    r'|(>=|<=|==|!=|:=|[+*/%<>=?:,()\[\]-]))'
)

# ── Function registries ────────────────────────────────────────────────────
CALLS = {
    'indicator', 'study', 'strategy',
    'input', 'input.int', 'input.float', 'input.bool', 'input.string',
    'input.source', 'input.timeframe', 'input.symbol',
    'input.color', 'input.time', 'input.session',
    'plot', 'plotshape', 'plotchar', 'plotcandle', 'plotbar',
    'hline', 'fill', 'bgcolor', 'barcolor',
    'alertcondition', 'alert',
    'strategy.entry', 'strategy.close', 'strategy.close_all', 'strategy.exit',
    'request.security',
    # ta.*
    'ta.sma', 'ta.ema', 'ta.rma', 'ta.wma', 'ta.hma', 'ta.rsi', 'ta.atr',
    'ta.tr', 'ta.highest', 'ta.lowest', 'ta.stdev', 'ta.sum', 'ta.crossover',
    'ta.crossunder', 'ta.cross', 'ta.change', 'ta.roc', 'ta.macd', 'ta.bb',
    'ta.avg', 'ta.cum', 'ta.rising', 'ta.falling', 'ta.median', 'ta.mode',
    'ta.variance', 'ta.dev', 'ta.correlation', 'ta.covariance', 'ta.vwma',
    'ta.pivothigh', 'ta.pivotlow', 'ta.dmi', 'ta.supertrend',
    'ta.barssince', 'ta.valuewhen', 'ta.stoch', 'ta.linreg',
    # array.*
    'array.new', 'array.new_float', 'array.new_int', 'array.new_bool',
    'array.new_string', 'array.new_color', 'array.from', 'array.size',
    'array.get', 'array.set', 'array.push', 'array.pop', 'array.shift',
    'array.unshift', 'array.clear', 'array.sum', 'array.avg', 'array.max',
    'array.min', 'array.indexof', 'array.includes', 'array.remove',
    'array.copy', 'array.concat', 'array.reverse',
    # math.* + globals
    'math.abs', 'math.max', 'math.min', 'math.avg', 'math.sum', 'math.sign',
    'math.sqrt', 'math.pow', 'math.log', 'math.log10', 'math.exp', 'math.tanh',
    'math.round', 'math.floor', 'math.ceil',
    'nz', 'na', 'abs', 'max', 'min', 'sqrt', 'round',
    'sma', 'ema', 'rma', 'rsi', 'atr', 'highest', 'lowest',
    'crossover', 'crossunder', 'change',
}

ALLOWED_KW = {
    'strategy': {'title', 'shorttitle', 'overlay', 'pyramiding',
                 'process_orders_on_close', 'calc_on_every_tick',
                 'calc_on_order_fills',
                 'initial_capital', 'default_qty_type', 'default_qty_value',
                 'commission_type', 'commission_value', 'slippage',
                 'margin_long', 'margin_short', 'currency', 'format',
                 'precision', 'max_bars_back', 'max_labels_count',
                 'max_lines_count', 'max_boxes_count', 'linktoseries',
                 'scale', 'close_entries_rule', 'risk_free_rate'},
    'indicator': {'title', 'shorttitle', 'overlay', 'precision', 'max_bars_back',
                  'max_labels_count', 'max_lines_count', 'max_boxes_count',
                  'format', 'scale', 'linktoseries', 'timeframe',
                  'timeframe_gaps', 'explicit_plot_zorder', 'behind_chart'},
    'study': {'title', 'shorttitle', 'overlay', 'precision', 'max_bars_back',
              'max_labels_count', 'max_lines_count', 'max_boxes_count',
              'format', 'scale', 'linktoseries', 'resolution',
              'resolution_gaps'},
    'strategy.entry': {'id', 'direction', 'when', 'qty', 'limit', 'stop',
                       'comment', 'alert_message', 'oca_name', 'oca_type',
                       'disable_alert'},
    'strategy.order': {'id', 'direction', 'when', 'qty', 'limit', 'stop',
                       'comment', 'alert_message', 'oca_name', 'oca_type'},
    'strategy.close': {'id', 'when', 'comment', 'alert_message', 'qty',
                       'qty_percent'},
    'strategy.close_all': {'when', 'comment', 'alert_message'},
    'strategy.exit': {'id', 'from_entry', 'stop', 'limit', 'when',
                      'comment', 'alert_message', 'qty', 'qty_percent',
                      'qty_type', 'profit', 'loss', 'trail_points',
                      'trail_offset', 'trail_limit', 'disable_alert',
                      'oca_name', 'oca_type'},
    'plot': {'series', 'title', 'color', 'linewidth', 'style', 'display',
             'offset', 'trackprice', 'histbase', 'transp', 'editable',
             'show_last'},
    'plotshape': {'series', 'title', 'text', 'color', 'textcolor', 'style',
                  'location', 'size', 'offset', 'transp', 'editable',
                  'show_last', 'display'},
    'plotchar': {'series', 'title', 'char', 'color', 'location', 'size',
                 'offset', 'display', 'text', 'transp'},
    'plotcandle': {'open', 'high', 'low', 'close', 'title', 'color',
                   'wickcolor', 'bordercolor', 'display', 'editable'},
    'plotbar': {'open', 'high', 'low', 'close', 'title', 'color',
                'editable', 'display'},
    'hline': {'price', 'title', 'color', 'linestyle', 'linewidth', 'editable'},
    'fill': {'plot1', 'plot2', 'color', 'title', 'transparency', 'transp',
             'fill_gradient', 'display', 'editable', 'fillgaps'},
    'bgcolor': {'color', 'title', 'transparency', 'transp', 'offset',
                'display', 'editable'},
    'barcolor': {'color', 'title', 'offset', 'display', 'editable'},
    'alertcondition': {'condition', 'title', 'message', 'freq'},
    'alert': {'condition', 'title', 'message', 'freq'},
    'request.security': {'symbol', 'timeframe', 'expression', 'gaps',
                         'lookahead'},
    'input': {'defval', 'title', 'minval', 'maxval', 'step', 'options',
              'group', 'tooltip', 'inline', 'type', 'confirm', 'editable'},
}

CONSTANTS = {
    'true': True, 'false': False, 'na': float('nan'),
    'strategy.long': 1, 'strategy.short': -1,
    'barstate.isconfirmed': True,
    'strategy.percent_of_equity': 'percent',
    'strategy.fixed': 'fixed',
    'strategy.commission.percent': 'percent',
    'currency.USD': 'USD',
    'barmerge.gaps_off': 'off', 'barmerge.gaps_on': 'on',
    'barmerge.lookahead_off': 'off', 'barmerge.lookahead_on': 'on',
    # display.* bit flags (rendering targets). We accept and store them;
    # the local interpreter has no multi-pane renderer.
    'display.none': 0, 'display.pane': 1, 'display.price_scale': 2,
    'display.data_window': 4, 'display.status_line': 8, 'display.all': 15,
    # v3 input() type enum values (passed through as strings)
    'integer': 'integer', 'float': 'float', 'bool': 'bool',
    'string': 'string', 'source': 'source',
    # v3 bare color names (v5 uses color.red etc.)
    'red': '#FF0000', 'green': '#008000', 'blue': '#0000FF',
    'yellow': '#FFFF00', 'orange': '#FFA500', 'purple': '#800080',
    'gray': '#808080', 'grey': '#808080', 'white': '#FFFFFF',
    'black': '#000000', 'aqua': '#00FFFF', 'fuchsia': '#FF00FF',
    'silver': '#C0C0C0', 'maroon': '#800000', 'olive': '#808000',
    'lime': '#00FF00', 'teal': '#008080', 'navy': '#000080',
    # v3 bare style names (v5 uses style.line etc.)
    'stepline': 'stepline', 'histogram': 'histogram',
    'circles': 'circles', 'area': 'area',
    'columns': 'columns', 'dotted': 'dotted', 'dashed': 'dashed',
    'arrow_up': 'arrow_up', 'arrow_down': 'arrow_down',
    'triangleup': 'triangleup', 'triangledown': 'triangledown',
    'flag': 'flag', 'circle': 'circle', 'square': 'square',
    'diamond': 'diamond', 'label_up': 'label_up', 'label_down': 'label_down',
    'label_left': 'label_left', 'label_right': 'label_right',
    'label_upper_right': 'label_upper_right', 'label_upper_left': 'label_upper_left',
    'label_lower_right': 'label_lower_right', 'label_lower_left': 'label_lower_left',
    'label_center': 'label_center',
    # built-in variables (parser allow-list; runtime overrides value)
    'timenow': 0,
    # time functions usable as bare variables (current bar's time components)
    'year': 0, 'month': 0, 'dayofmonth': 0, 'dayofweek': 0,
    'hour': 0, 'minute': 0, 'second': 0,
    # dayofweek enum
    'dayofweek.sunday': 1, 'dayofweek.monday': 2, 'dayofweek.tuesday': 3,
    'dayofweek.wednesday': 4, 'dayofweek.thursday': 5,
    'dayofweek.friday': 6, 'dayofweek.saturday': 7,
    'syminfo.mintick': 0.01, 'syminfo.prefix': 'BINANCE',
    # built-in series variables (parser allow-list; runtime computes)
    'accdist': 0, 'pvt': 0,
}

BASES = {'open', 'high', 'low', 'close', 'volume', 'time', 'bar_index',
         'hl2', 'hlc3', 'ohlc4'}

ALIASES = {
    'sma': 'ta.sma', 'ema': 'ta.ema', 'rma': 'ta.rma', 'rsi': 'ta.rsi',
    'atr': 'ta.atr', 'tr': 'ta.tr', 'highest': 'ta.highest',
    'lowest': 'ta.lowest', 'crossover': 'ta.crossover',
    'crossunder': 'ta.crossunder', 'cross': 'ta.cross',
    'change': 'ta.change', 'stdev': 'ta.stdev', 'dev': 'ta.dev',
    'sum': 'ta.sum', 'avg': 'ta.avg', 'median': 'ta.median',
    'mode': 'ta.mode', 'variance': 'ta.variance', 'vwma': 'ta.vwma',
    'cum': 'ta.cum', 'roc': 'ta.roc', 'macd': 'ta.macd',
    'highestbars': 'ta.highestbars', 'lowestbars': 'ta.lowestbars',
    'dmi': 'ta.dmi', 'adx': 'ta.dmi', 'pivothigh': 'ta.pivothigh',
    'pivotlow': 'ta.pivotlow', 'barssince': 'ta.barssince',
    'valuewhen': 'ta.valuewhen', 'stoch': 'ta.stoch',
    'linreg': 'ta.linreg', 'wpr': 'ta.wpr', 'cci': 'ta.cci',
    'mfi': 'ta.mfi', 'obv': 'ta.obv', 'sar': 'ta.sar',
    'supertrend': 'ta.supertrend', 'momentum': 'ta.momentum',
    'slope': 'ta.slope', 'percentrank': 'ta.percentrank',
    'rising': 'ta.rising', 'falling': 'ta.falling',
    'correlation': 'ta.correlation', 'covariance': 'ta.covariance',
    'bb': 'ta.bb', 'kc': 'ta.kc', 'donchian': 'ta.donchian',
    # math bare names
    'pow': 'math.pow', 'asin': 'math.asin', 'acos': 'math.acos',
    'atan': 'math.atan', 'sin': 'math.sin', 'cos': 'math.cos',
    'tan': 'math.tan', 'sinh': 'math.sinh', 'cosh': 'math.cosh',
    'tanh': 'math.tanh', 'exp': 'math.exp', 'log': 'math.log',
    'log10': 'math.log10', 'sqrt': 'math.sqrt', 'floor': 'math.floor',
    'ceil': 'math.ceil', 'round': 'math.round', 'sign': 'math.sign',
    'abs': 'math.abs', 'min': 'math.min', 'max': 'math.max',
    # v3 / misc aliases
    'security': 'request.security', 'mom': 'ta.momentum',
    'heikinashi': 'ticker.heikinashi', 'color': 'color.new',
    'wma': 'ta.wma', 'hma': 'ta.hma', 'alma': 'ta.alma',
    'swma': 'ta.swma', 'accdist': 'ta.accdist', 'pvt': 'ta.pvt',
}

SIGNATURES = {name: ('source', 'length') for name in (
    'ta.sma', 'ta.ema', 'ta.rma', 'ta.wma', 'ta.hma', 'ta.rsi', 'ta.sum',
    'ta.median', 'ta.mode', 'ta.variance', 'ta.dev', 'ta.vwma',
    'ta.rising', 'ta.falling')}
SIGNATURES.update({
    'ta.atr': ('length',),
    'ta.tr': ('handle_na',),
    'ta.stdev': ('source', 'length', 'biased'),
    'ta.crossover': ('source1', 'source2'),
    'ta.crossunder': ('source1', 'source2'),
    'ta.cross': ('source1', 'source2'),
    'ta.change': ('source', 'length'),
    'ta.roc': ('source', 'length'),
    'ta.macd': ('source', 'fastlen', 'slowlen', 'siglen'),
    'ta.bb': ('series', 'length', 'mult'),
    'ta.barssince': ('condition',),
    'ta.valuewhen': ('condition', 'source', 'occurrence'),
    'ta.stoch': ('source', 'peak', 'valley', 'period'),
    'ta.linreg': ('source', 'length', 'offset'),
    'ta.pivothigh': ('source', 'leftbars', 'rightbars'),
    'ta.pivotlow': ('source', 'leftbars', 'rightbars'),
    'ta.correlation': ('source1', 'source2', 'length'),
    'ta.covariance': ('source1', 'source2', 'length'),
    'ta.supertrend': ('factor', 'atrperiod'),
    'ta.dmi': ('diLength', 'adxSmoothing'),
    'request.security': ('symbol', 'timeframe', 'expression', 'gaps', 'lookahead'),
})

# ── Timeframe helpers ──────────────────────────────────────────────────────
def normalize_timeframe(value):
    s = str(value).strip()
    low = s.lower()
    if s.isdigit():
        return s + 'm'
    return {'d': '1d', 'w': '1w', 'm': '1mo', '1d': '1d',
            '1w': '1w', '1m': '1mo'}.get(low, low)

def timeframe_milliseconds(value):
    tf = normalize_timeframe(value)
    m = re.fullmatch(r'(\d+)(s|m|h|d|w|mo)', tf)
    if not m:
        raise PineError('不支持的Pine周期：' + str(value))
    n = int(m.group(1))
    unit = m.group(2)
    scale = {'s': 1000, 'm': 60000, 'h': 3600000, 'd': 86400000,
             'w': 604800000, 'mo': 2592000000}[unit]
    if n < 1 or n > 525600:
        raise PineError('Pine周期超出范围')
    return n * scale

def truth(v):
    if isinstance(v, (int, float)) and not math.isfinite(float(v)):
        return False
    return bool(v)

def _pine_na_like(value):
    if isinstance(value, (list, tuple)):
        return [_pine_na_like(x) for x in value]
    return float('nan')
