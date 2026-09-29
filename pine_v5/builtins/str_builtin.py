"""Pine v5 `str` namespace builtins — registered via @register."""
from __future__ import annotations
import math

from .registry import register
from ._helpers import PineError, isna, na, arg


def _to_str(v):
    if isna(v):
        return 'na'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


@register('str.tostring')
def str_tostring(runtime, node, i):
    value = arg(runtime, node, i, 0)
    fmt = arg(runtime, node, i, 1, 'format', None)
    if isna(value):
        return 'na'
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return _to_str(value)
    value = float(value)
    if not math.isfinite(value):
        return 'na'
    if fmt is None:
        return _to_str(value)
    if not isinstance(fmt, str):
        raise PineError('str.tostring format 必须是字符串')
    # Pine format like "#.##" / "0.00" -> decimal places after dot
    dot = fmt.rfind('.')
    if dot < 0:
        decimals = 0
    else:
        decimals = sum(1 for ch in fmt[dot + 1:] if ch in '#0')
    text = f'{value:.{decimals}f}'
    # strip trailing zeros only when format uses '#' (optional digits)
    if '#' in fmt and dot >= 0:
        if '.' in text:
            text = text.rstrip('0').rstrip('.')
    return text


@register('str.tonumber')
def str_tonumber(runtime, node, i):
    s = arg(runtime, node, i, 0)
    if isna(s) or not isinstance(s, str):
        return na()
    try:
        return float(s.strip())
    except ValueError:
        return na()


@register('str.length')
def str_length(runtime, node, i):
    s = arg(runtime, node, i, 0)
    if isna(s):
        return na()
    if not isinstance(s, str):
        raise PineError('str.length 需要字符串')
    return len(s)


@register('str.substring')
def str_substring(runtime, node, i):
    s = arg(runtime, node, i, 0)
    begin = arg(runtime, node, i, 1)
    end = arg(runtime, node, i, 2)
    if isna(s) or isna(begin) or isna(end):
        return na()
    if not isinstance(s, str) or isinstance(begin, bool) or isinstance(end, bool):
        raise PineError('str.substring 参数类型错误')
    begin, end = int(begin), int(end)
    n = len(s)
    # Pine: beginPos inclusive, endPos inclusive; clamp to valid range
    if begin < 0:
        begin = 0
    if end >= n:
        end = n - 1
    if begin > end or begin >= n or end < 0:
        return ''
    return s[begin:end + 1]


@register('str.contains')
def str_contains(runtime, node, i):
    s = arg(runtime, node, i, 0)
    sub = arg(runtime, node, i, 1)
    if isna(s) or isna(sub):
        return False
    return sub in s


@register('str.startswith')
def str_startswith(runtime, node, i):
    s = arg(runtime, node, i, 0)
    pre = arg(runtime, node, i, 1)
    if isna(s) or isna(pre):
        return False
    return s.startswith(pre)


@register('str.endswith')
def str_endswith(runtime, node, i):
    s = arg(runtime, node, i, 0)
    suf = arg(runtime, node, i, 1)
    if isna(s) or isna(suf):
        return False
    return s.endswith(suf)


@register('str.replace')
def str_replace(runtime, node, i):
    s = arg(runtime, node, i, 0)
    target = arg(runtime, node, i, 1)
    repl = arg(runtime, node, i, 2)
    if isna(s) or isna(target) or isna(repl):
        return na()
    return s.replace(target, repl)


@register('str.lower')
def str_lower(runtime, node, i):
    s = arg(runtime, node, i, 0)
    if isna(s):
        return na()
    return s.lower()


@register('str.upper')
def str_upper(runtime, node, i):
    s = arg(runtime, node, i, 0)
    if isna(s):
        return na()
    return s.upper()


@register('str.split')
def str_split(runtime, node, i):
    s = arg(runtime, node, i, 0)
    sep = arg(runtime, node, i, 1)
    if isna(s) or isna(sep):
        return na()
    return s.split(sep)


@register('str.concat')
def str_concat(runtime, node, i):
    a = arg(runtime, node, i, 0)
    b = arg(runtime, node, i, 1)
    return _to_str(a) + _to_str(b)


@register('str.format')
def str_format(runtime, node, i):
    template = arg(runtime, node, i, 0)
    if isna(template) or not isinstance(template, str):
        return na()
    out = template
    for idx, arg_node in enumerate(node[2][1:]):
        v = runtime.evaluate(arg_node, i)
        out = out.replace('{%d}' % idx, _to_str(v))
    return out
