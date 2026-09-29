"""Pine v8 user-defined type (UDT) support.

A UDT is declared as:
    type Point
        float x
        float y

Instances are plain Python dicts carrying a ``__type__`` tag so the runtime
can perform field access and construction validation.
"""
from __future__ import annotations
from .constants import PineError


class UDT:
    """A user-defined type: named field list with order-preserving fields."""

    def __init__(self, name, fields, line):
        if not name or not name[0].isalpha() and name[0] != '_':
            raise PineError('UDT名称无效：' + str(name))
        self.name = name
        # fields: list of (field_name, declared_type)
        self.fields = list(fields)
        self.line = line

    @property
    def field_names(self):
        return [f[0] for f in self.fields]

    def new_instance(self, positional, kw):
        """Build a dict instance from positional args (by field order) and kwargs."""
        names = self.field_names
        if len(positional) > len(names):
            raise PineError(self.name + ' 构造参数过多')
        supplied = dict(zip(names, positional))
        for key in kw:
            if key in supplied:
                raise PineError(self.name + ' 字段参数重复：' + key)
            if key not in names:
                raise PineError(self.name + ' 不存在字段 ' + key
                                + '；可用字段：' + ','.join(names))
            supplied[key] = kw[key]
        missing = [n for n in names if n not in supplied]
        if missing:
            raise PineError(self.name + ' 缺少字段：' + ','.join(missing))
        obj = {'__type__': self.name}
        for n in names:
            obj[n] = supplied[n]
        return obj

    def __repr__(self):  # pragma: no cover - debug aid
        return f'UDT({self.name}{self.field_names})'
