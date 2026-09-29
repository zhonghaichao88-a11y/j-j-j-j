"""Closed-bar Pine v4/v5/v6 compatibility subset. No eval/exec/import/network.
Unsupported statements/functions are errors with source line, never silently
reinterpreted. Strategy entries/closes are signals; V7 owns size and native exits.

This module is a backward-compatible facade over the pine_v5 package.
The actual implementation lives in pine_v5/ (lexer, parser, runtime, builtins).
"""
from __future__ import annotations
from pathlib import Path

# Re-export public API from the pine_v5 package for backward compatibility.
from pine_v5 import (
    PineError,
    compile_source,
    Runtime,
    run,
    run_saved as _run_saved,
    validate_inputs,
)
from pine_v5 import api as _api

# Re-export commonly used internals that existing code may reference.
from pine_v5.constants import (
    CALLS, ALLOWED_KW, CONSTANTS, BASES, ALIASES, SIGNATURES,
    normalize_timeframe, timeframe_milliseconds, truth,
)
from pine_v5.lexer import tokenize, logical_lines
from pine_v5.parser import Parser, walk, resolve_expression

# ROOT must live on this module so that existing tests using
# patch('alpha_v7_pine.ROOT', ...) continue to work.  save/load_profile
# wrappers below propagate it into pine_v5.api at call time.
ROOT = Path(__file__).with_name('v7_pine_scripts')


def save(source, inputs=None):
    _api.ROOT = ROOT
    return _api.save(source, inputs)


def load_profile(identifier):
    _api.ROOT = ROOT
    return _api.load_profile(identifier)


def load(identifier):
    return load_profile(identifier)[0]


def run_saved(identifier, frame, timeframe='5m', symbol='', frames=None):
    _api.ROOT = ROOT
    return _api.run_saved(identifier, frame, timeframe, symbol, frames)


__all__ = [
    'PineError', 'compile_source', 'Runtime',
    'run', 'run_saved', 'save', 'load', 'load_profile', 'validate_inputs',
    'ROOT',
    'CALLS', 'ALLOWED_KW', 'CONSTANTS', 'BASES', 'ALIASES', 'SIGNATURES',
    'normalize_timeframe', 'timeframe_milliseconds', 'truth',
    'tokenize', 'logical_lines', 'Parser', 'walk', 'resolve_expression',
]
