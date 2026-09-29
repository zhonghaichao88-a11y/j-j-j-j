"""Explicit boundary between Pine simulation and V7's managed signal execution."""
from .constants import PineError


def live_capability(program, tf='5m'):
    reasons = set()
    def visit(obj):
        if isinstance(obj, tuple) and obj:
            if obj[0] == 'call':
                name, args, kw = obj[1:4]
                if name in ('strategy.exit', 'strategy.order'):
                    reasons.add(name + '订单暂未接入V7自动交易；可预览回测')
                if name == 'strategy.entry' and (len(args) > 2 or set(kw) & {'qty', 'limit', 'stop'}):
                    reasons.add('Pine数量/挂单参数暂未接入V7；自动交易仅支持由V7管理仓位的市价信号')
                if name == 'strategy.close' and (len(args) > 1 or set(kw) & {'qty', 'qty_percent'}):
                    reasons.add('Pine部分平仓暂未接入V7；不会把部分退出转换成整仓退出')
                if name == 'strategy' and 'pyramiding' in kw and kw['pyramiding'] != ('const', 0):
                    reasons.add('Pine加仓暂未接入V7自动交易')
            if obj[0] == 'name' and str(obj[1]).startswith('strategy.') and obj[1] not in ('strategy.long', 'strategy.short'):
                reasons.add('脚本依赖Pine模拟持仓/权益，尚未与交易所持仓同步')
        if isinstance(obj, dict):
            for value in obj.values(): visit(value)
        elif isinstance(obj, (list, tuple)):
            for value in obj: visit(value)
    visit(program.get('nodes', [])); visit(program.get('functions', {})); visit(program.get('imports', {}))
    if program.get('kind') != 'strategy': reasons.add('指标脚本仅用于画图')
    return {'supported': not reasons, 'mode': 'v7_market_signals', 'timeframe': tf,
            'reasons': sorted(reasons),
            'description': f'自动交易跟随主级别（当前{tf}）；Pine提供市价方向与整仓退出信号，仓位与保护由V7管理'}


def require_live(program, tf='5m'):
    report = live_capability(program, tf)
    if not report['supported']:
        raise PineError('自动交易不兼容：' + '；'.join(report['reasons']))
    return report


def json_safe(value):
    """Normalize object snapshots at the HTTP boundary, preserving engine types."""
    import math
    if isinstance(value, dict):
        return {(','.join(map(str, key)) if isinstance(key, tuple) else str(key)): json_safe(v)
                for key, v in value.items()}
    if isinstance(value, (list, tuple)): return [json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value): return None
    if hasattr(value, 'item'): return json_safe(value.item())
    return value
