"""Pine v5 builtin function namespaces.

Auto-discovers all submodules in this directory and imports them, triggering
their @register decorators.  New builtin modules just need to be added as
*.py files here — no manual registration in __init__.py required.
"""
import os
import importlib

from . import registry  # noqa: F401

# Auto-import all submodules (except those starting with _)
_dir = os.path.dirname(__file__)
for _fname in sorted(os.listdir(_dir)):
    if _fname.endswith('.py') and not _fname.startswith('_'):
        _modname = _fname[:-3]
        if _modname != 'registry':
            importlib.import_module(f'pine_v5.builtins.{_modname}')
