"""Registered drawing builtins: line / label / box / table / polyline / linefill.

These delegate to ``PlotStore`` (on ``runtime.plot_store``) which maintains
``runtime.draw_objects`` and ``runtime.draw_events``.  No rendering is done.
"""
from __future__ import annotations

from ..constants import ALLOWED_KW, CONSTANTS
from .registry import register


# ── Parser-facing extensions: allowed kwargs + constant passthrough ────────
ALLOWED_KW.setdefault('line.new', set()).update(
    {'xloc', 'extend', 'color', 'width', 'style'})
ALLOWED_KW.setdefault('label.new', set()).update(
    {'xloc', 'yloc', 'color', 'textcolor', 'size', 'style', 'tooltip',
     'text'})
ALLOWED_KW.setdefault('box.new', set()).update(
    {'border_color', 'border_width', 'border_style', 'bgcolor', 'extend'})
ALLOWED_KW.setdefault('table.new', set()).update(
    {'position', 'bgcolor', 'border_width', 'border_color', 'columns',
     'rows'})
ALLOWED_KW.setdefault('table.cell', set()).update(
    {'text_color', 'text_halign', 'text_valign', 'bgcolor', 'width',
     'height', 'text'})
ALLOWED_KW.setdefault('table.get_cell', set()).update({'field'})
ALLOWED_KW.setdefault('polyline.new', set()).update(
    {'xloc', 'color', 'width', 'style', 'extend'})
ALLOWED_KW.setdefault('linefill.new', set()).update({'color'})

# xloc / yloc / extend / style / position constants passed through as strings.
for _c in ('xloc.bar_index', 'xloc.bar_time',
           'yloc.price', 'yloc.abovebar', 'yloc.belowbar',
           'extend.none', 'extend.left', 'extend.right', 'extend.both',
           'line.style_solid', 'line.style_dotted', 'line.style_dashed',
           'line.style_arrow_left', 'line.style_arrow_right',
           'label.style_labeldown', 'label.style_labelup',
           'label.style_label_left', 'label.style_label_right',
           'size.auto', 'size.tiny', 'size.small', 'size.normal',
           'size.large', 'size.huge',
           'position.top_left', 'position.top_center', 'position.top_right',
           'position.middle_left', 'position.middle_center',
           'position.middle_right', 'position.bottom_left',
           'position.bottom_center', 'position.bottom_right',
           'text.align_left', 'text.align_center', 'text.align_right',
           'text.align_top', 'text.align_center', 'text.align_bottom',
           'color.none'):
    CONSTANTS.setdefault(_c, _c)


def _args(node):
    return node[2]


def _kw(node):
    return node[3]


def _ev(runtime, node, i, idx, key, default=None):
    kw = _kw(node)
    if key in kw:
        return runtime.evaluate(kw[key], i)
    args = _args(node)
    if idx < len(args):
        return runtime.evaluate(args[idx], i)
    return default


# ── line ──────────────────────────────────────────────────────────────────
@register('line.new')
def line_new(runtime, node, i):
    return runtime.plot_store.line_new(
        _ev(runtime, node, i, 0, 'x1'), _ev(runtime, node, i, 1, 'y1'),
        _ev(runtime, node, i, 2, 'x2'), _ev(runtime, node, i, 3, 'y2'),
        xloc=_ev(runtime, node, i, 4, 'xloc', 'xloc.bar_index'),
        extend=_ev(runtime, node, i, 5, 'extend', 'extend.none'),
        color=_ev(runtime, node, i, 6, 'color', 'color.blue'),
        width=_ev(runtime, node, i, 7, 'width', 1),
        style=_ev(runtime, node, i, 8, 'style', 'line.style_solid'))


_LINE_SETTERS = {
    'line.set_xy': ('x', 'y'),
    'line.set_x1': ('x1',), 'line.set_y1': ('y1',),
    'line.set_x2': ('x2',), 'line.set_y2': ('y2',),
    'line.set_color': ('color',), 'line.set_width': ('width',),
    'line.set_style': ('style',), 'line.set_extend': ('extend',),
}
for _fn, _attrs in _LINE_SETTERS.items():
    def _make(fn, attrs):
        def _handler(runtime, node, i):
            obj_id = runtime.evaluate(_args(node)[0], i)
            values = [runtime.evaluate(a, i) for a in _args(node)[1:]]
            for attr, val in zip(attrs, values):
                runtime.plot_store.set_attr('line', obj_id, attr, val)
            return None
        _handler.__name__ = fn
        return _handler
    register(_fn)(_make(_fn, _attrs))


for _attr in ('x1', 'y1', 'x2', 'y2', 'color', 'width', 'style', 'extend'):
    def _make_get(attr):
        def _handler(runtime, node, i):
            obj_id = runtime.evaluate(_args(node)[0], i)
            return runtime.plot_store.get_attr('line', obj_id, attr)
        return _handler
    register('line.get_' + _attr)(_make_get(_attr))


@register('line.delete')
def line_delete(runtime, node, i):
    obj_id = runtime.evaluate(_args(node)[0], i)
    runtime.plot_store.delete('line', obj_id)
    return None


@register('linefill.new')
def linefill_new(runtime, node, i):
    return runtime.plot_store.linefill_new(
        runtime.evaluate(_args(node)[0], i),
        runtime.evaluate(_args(node)[1], i),
        color=_ev(runtime, node, i, 2, 'color', 'color.blue'))


# ── label ────────────────────────────────────────────────────────────────
@register('label.new')
def label_new(runtime, node, i):
    return runtime.plot_store.label_new(
        _ev(runtime, node, i, 0, 'x'), _ev(runtime, node, i, 1, 'y'),
        text=_ev(runtime, node, i, 2, 'text', ''),
        xloc=_ev(runtime, node, i, 3, 'xloc', 'xloc.bar_index'),
        yloc=_ev(runtime, node, i, 4, 'yloc', 'yloc.price'),
        color=_ev(runtime, node, i, 5, 'color', 'color.blue'),
        textcolor=_ev(runtime, node, i, 6, 'textcolor', 'color.white'),
        size=_ev(runtime, node, i, 7, 'size', 'size.normal'),
        style=_ev(runtime, node, i, 8, 'style', 'label.style_labeldown'),
        tooltip=_ev(runtime, node, i, 9, 'tooltip', ''))


_LABEL_SETTERS = {
    'label.set_xy': ('x', 'y'), 'label.set_x': ('x',), 'label.set_y': ('y',),
    'label.set_text': ('text',), 'label.set_color': ('color',),
    'label.set_textcolor': ('textcolor',), 'label.set_size': ('size',),
    'label.set_style': ('style',), 'label.set_tooltip': ('tooltip',),
}
for _fn, _attrs in _LABEL_SETTERS.items():
    def _make(fn, attrs):
        def _handler(runtime, node, i):
            obj_id = runtime.evaluate(_args(node)[0], i)
            values = [runtime.evaluate(a, i) for a in _args(node)[1:]]
            for attr, val in zip(attrs, values):
                runtime.plot_store.set_attr('label', obj_id, attr, val)
            return None
        return _handler
    register(_fn)(_make(_fn, _attrs))


for _attr in ('x', 'y', 'text', 'color', 'textcolor', 'size', 'style',
              'tooltip'):
    def _make_get(attr):
        def _handler(runtime, node, i):
            obj_id = runtime.evaluate(_args(node)[0], i)
            return runtime.plot_store.get_attr('label', obj_id, attr)
        return _handler
    register('label.get_' + _attr)(_make_get(_attr))


@register('label.delete')
def label_delete(runtime, node, i):
    obj_id = runtime.evaluate(_args(node)[0], i)
    runtime.plot_store.delete('label', obj_id)
    return None


# ── box ──────────────────────────────────────────────────────────────────
@register('box.new')
def box_new(runtime, node, i):
    return runtime.plot_store.box_new(
        _ev(runtime, node, i, 0, 'left'), _ev(runtime, node, i, 1, 'top'),
        _ev(runtime, node, i, 2, 'right'), _ev(runtime, node, i, 3, 'bottom'),
        border_color=_ev(runtime, node, i, 4, 'border_color', 'color.blue'),
        border_width=_ev(runtime, node, i, 5, 'border_width', 1),
        border_style=_ev(runtime, node, i, 6, 'border_style', 'line.style_solid'),
        bgcolor=_ev(runtime, node, i, 7, 'bgcolor', 'color.blue'),
        extend=_ev(runtime, node, i, 8, 'extend', 'extend.none'))


_BOX_SETTERS = {
    'box.set_left': ('left',), 'box.set_top': ('top',),
    'box.set_right': ('right',), 'box.set_bottom': ('bottom',),
    'box.set_border_color': ('border_color',),
    'box.set_border_width': ('border_width',),
    'box.set_border_style': ('border_style',),
    'box.set_bgcolor': ('bgcolor',), 'box.set_extend': ('extend',),
}
for _fn, _attrs in _BOX_SETTERS.items():
    def _make(fn, attrs):
        def _handler(runtime, node, i):
            obj_id = runtime.evaluate(_args(node)[0], i)
            values = [runtime.evaluate(a, i) for a in _args(node)[1:]]
            for attr, val in zip(attrs, values):
                runtime.plot_store.set_attr('box', obj_id, attr, val)
            return None
        return _handler
    register(_fn)(_make(_fn, _attrs))


for _attr in ('left', 'top', 'right', 'bottom', 'border_color', 'bgcolor'):
    def _make_get(attr):
        def _handler(runtime, node, i):
            obj_id = runtime.evaluate(_args(node)[0], i)
            return runtime.plot_store.get_attr('box', obj_id, attr)
        return _handler
    register('box.get_' + _attr)(_make_get(_attr))


@register('box.delete')
def box_delete(runtime, node, i):
    obj_id = runtime.evaluate(_args(node)[0], i)
    runtime.plot_store.delete('box', obj_id)
    return None


# ── table ────────────────────────────────────────────────────────────────
@register('table.new')
def table_new(runtime, node, i):
    return runtime.plot_store.table_new(
        position=_ev(runtime, node, i, 0, 'position', 'position.top_right'),
        columns=_ev(runtime, node, i, 1, 'columns', 1),
        rows=_ev(runtime, node, i, 2, 'rows', 1),
        bgcolor=_ev(runtime, node, i, 3, 'bgcolor', 'color.gray'),
        border_width=_ev(runtime, node, i, 4, 'border_width', 1),
        border_color=_ev(runtime, node, i, 5, 'border_color', 'color.black'))


@register('table.cell')
def table_cell(runtime, node, i):
    return runtime.plot_store.table_cell(
        runtime.evaluate(_args(node)[0], i),
        runtime.evaluate(_args(node)[1], i),
        runtime.evaluate(_args(node)[2], i),
        text=_ev(runtime, node, i, 3, 'text', ''),
        text_color=_ev(runtime, node, i, 4, 'text_color', 'color.black'),
        text_halign=_ev(runtime, node, i, 5, 'text_halign', 'text.align_left'),
        text_valign=_ev(runtime, node, i, 6, 'text_valign', 'text.align_top'),
        bgcolor=_ev(runtime, node, i, 7, 'bgcolor', 'color.gray'),
        width=_ev(runtime, node, i, 8, 'width'),
        height=_ev(runtime, node, i, 9, 'height'))


@register('table.set_cell')
def table_set_cell(runtime, node, i):
    return table_cell(runtime, node, i)


@register('table.get_cell')
def table_get_cell(runtime, node, i):
    return runtime.plot_store.table_get_cell(
        runtime.evaluate(_args(node)[0], i),
        runtime.evaluate(_args(node)[1], i),
        runtime.evaluate(_args(node)[2], i),
        field=_ev(runtime, node, i, 3, 'field', 'text'))


@register('table.delete')
def table_delete(runtime, node, i):
    obj_id = runtime.evaluate(_args(node)[0], i)
    runtime.plot_store.delete('table', obj_id)
    return None


# ── polyline ─────────────────────────────────────────────────────────────
@register('polyline.new')
def polyline_new(runtime, node, i):
    return runtime.plot_store.polyline_new(
        _ev(runtime, node, i, 0, 'points'),
        xloc=_ev(runtime, node, i, 1, 'xloc', 'xloc.bar_index'),
        color=_ev(runtime, node, i, 2, 'color', 'color.blue'),
        width=_ev(runtime, node, i, 3, 'width', 1),
        style=_ev(runtime, node, i, 4, 'style', 'line.style_solid'),
        extend=_ev(runtime, node, i, 5, 'extend', 'extend.none'))


@register('polyline.set_points')
def polyline_set_points(runtime, node, i):
    obj_id = runtime.evaluate(_args(node)[0], i)
    points = runtime.evaluate(_args(node)[1], i)
    return runtime.plot_store.polyline_set_points(obj_id, points)


@register('polyline.delete')
def polyline_delete(runtime, node, i):
    obj_id = runtime.evaluate(_args(node)[0], i)
    runtime.plot_store.delete('polyline', obj_id)
    return None
