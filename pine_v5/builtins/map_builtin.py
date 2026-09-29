"""Pine v5 `map` namespace — backed by a Python dict."""
from __future__ import annotations

from .registry import register
from ._helpers import PineError, isna, na, arg


def _get_map(runtime, node, i):
    v = runtime.evaluate(node[2][0], i)
    if not isinstance(v, dict):
        raise PineError(node[1] + ' 需要map ID作为参数')
    return v


@register('map.new')
def map_new(runtime, node, i):
    # key_type / value_type are static type hints; runtime dict is dynamic
    return {}


@register('map.put')
def map_put(runtime, node, i):
    m = _get_map(runtime, node, i)
    key = runtime.evaluate(node[2][1], i)
    value = runtime.evaluate(node[2][2], i)
    m[key] = value
    return None


@register('map.get')
def map_get(runtime, node, i):
    m = _get_map(runtime, node, i)
    key = runtime.evaluate(node[2][1], i)
    return m.get(key, na())


@register('map.remove')
def map_remove(runtime, node, i):
    m = _get_map(runtime, node, i)
    key = runtime.evaluate(node[2][1], i)
    return m.pop(key, na())


@register('map.contains')
def map_contains(runtime, node, i):
    m = _get_map(runtime, node, i)
    key = runtime.evaluate(node[2][1], i)
    return key in m


@register('map.size')
def map_size(runtime, node, i):
    return len(_get_map(runtime, node, i))


@register('map.keys')
def map_keys(runtime, node, i):
    return list(_get_map(runtime, node, i).keys())


@register('map.values')
def map_values(runtime, node, i):
    return list(_get_map(runtime, node, i).values())


@register('map.clear')
def map_clear(runtime, node, i):
    _get_map(runtime, node, i).clear()
    return None


@register('map.copy')
def map_copy(runtime, node, i):
    return dict(_get_map(runtime, node, i))
