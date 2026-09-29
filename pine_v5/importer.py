"""Pine v8 local-library import support.

Syntax::

    import utils
    import utils as u

Resolution order for a module ``name``:
    1. The directory of the currently compiling script (caller-supplied).
    2. ``v7_pine_scripts/libs/`` next to the project root.

Imported files are compiled in *library mode*: they must only declare
functions and UDTs (no indicator/strategy header required).  TradingView
cloud imports ``import {lib} from "author/name"`` are rejected explicitly.
"""
from __future__ import annotations
from pathlib import Path
from .constants import PineError

# Cache: resolved module name (normalized path) -> module dict.
_MODULE_CACHE = {}
# Set of module names currently being loaded (cycle detection).
_LOADING = set()

# Project-root libs directory: <project>/v7_pine_scripts/libs
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
_LIBS_DIR = _PROJECT_ROOT / 'v7_pine_scripts' / 'libs'


def _candidate_paths(module_name, search_dir):
    name = module_name.strip()
    if not name or '/' in name or '\\' in name or name.startswith('.'):
        raise PineError('非法模块名：' + module_name)
    paths = []
    if search_dir is not None:
        paths.append(Path(search_dir) / (name + '.pine'))
    paths.append(_LIBS_DIR / (name + '.pine'))
    return paths


def resolve_module_path(module_name, search_dir):
    for path in _candidate_paths(module_name, search_dir):
        if path.exists():
            return path.resolve()
    raise PineError(
        '找不到本地库模块 ' + module_name
        + '；已搜索目录：' + ', '.join(str(p.parent) for p in _candidate_paths(module_name, search_dir)))


def load_module(module_name, search_dir):
    """Compile and return a library module dict.

    Returned dict: {'functions': {name: spec}, 'types': {name: UDT},
                    'path': Path}.  Cached by resolved path.
    """
    from .parser import compile_source  # lazy: avoid circular import
    path = resolve_module_path(module_name, search_dir)
    key = str(path)
    if key in _MODULE_CACHE:
        return _MODULE_CACHE[key]
    if key in _LOADING:
        raise PineError('检测到循环导入：' + module_name)
    _LOADING.add(key)
    try:
        source = path.read_text(encoding='utf-8')
        program = compile_source(source, library=True,
                                 search_dir=str(path.parent))
        module = {
            'functions': program.get('functions', {}),
            'types': program.get('types', {}),
            'path': path,
        }
        _MODULE_CACHE[key] = module
        return module
    finally:
        _LOADING.discard(key)


def reset_cache():
    _MODULE_CACHE.clear()
    _LOADING.clear()
