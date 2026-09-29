"""Pine v5 builtin function registry — extensible dispatch table.

New builtins are registered via @register('namespace.func'). The Runtime.call
method checks this registry first, then falls back to core implementations.
"""
from __future__ import annotations

# name -> callable(runtime, node, i) -> value
REGISTRY = {}


def register(name):
    """Decorator to register a builtin function handler."""
    def decorator(fn):
        REGISTRY[name] = fn
        return fn
    return decorator


def get(name):
    return REGISTRY.get(name)


def registered_names():
    return set(REGISTRY.keys())
