"""Drawing object state model (line / label / box / table / polyline / linefill).

This module does *not* render anything.  It maintains, on the Runtime, two
structures that can be serialised and replayed:

* ``runtime.draw_objects`` -- ``id -> object state dict`` (current snapshot).
* ``runtime.draw_events``  -- ordered list of ``create/set/delete`` events that
  reconstruct the snapshot.

Object ids are monotonically increasing integers returned by the ``*.new``
functions.  Every mutation is recorded so a downstream renderer can replay the
timeline (including deletions).
"""
from __future__ import annotations

import math

from .constants import PineError


def _num(v, allow_none=False):
    if v is None and allow_none:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise PineError('需要数值参数')
    if not math.isfinite(float(v)):
        return None if allow_none else float('nan')
    return float(v)


class PlotStore:
    """Owns the draw_objects / draw_events state on a Runtime."""

    def __init__(self, runtime):
        self.runtime = runtime
        runtime.draw_objects = {}
        runtime.draw_events = []
        self._next_id = 1

    # ── event recording ──────────────────────────────────────────────────
    def _record(self, kind, obj_type, obj_id, attrs=None):
        self.runtime.draw_events.append(dict(
            kind=kind, obj_type=obj_type, id=obj_id,
            attrs=dict(attrs or {}), bar=self.runtime.i))

    def _new_id(self):
        obj_id = self._next_id
        self._next_id += 1
        return obj_id

    def _require(self, obj_id, obj_type):
        obj = self.runtime.draw_objects.get(obj_id)
        if obj is None:
            raise PineError('绘图对象不存在 id=' + str(obj_id))
        if obj['type'] != obj_type:
            raise PineError('对象类型不匹配 id=' + str(obj_id))
        return obj

    # ── line ─────────────────────────────────────────────────────────────
    def line_new(self, x1, y1, x2, y2, xloc='xloc.bar_index',
                 extend='extend.none', color='color.blue', width=1,
                 style='line.style_solid'):
        obj_id = self._new_id()
        state = dict(type='line', x1=_num(x1), y1=_num(y1), x2=_num(x2),
                     y2=_num(y2), xloc=str(xloc), extend=str(extend),
                     color=str(color), width=int(width), style=str(style),
                     bar_created=self.runtime.i)
        self.runtime.draw_objects[obj_id] = state
        self._record('create', 'line', obj_id, state)
        return obj_id

    # ── label ────────────────────────────────────────────────────────────
    def label_new(self, x, y, text='', xloc='xloc.bar_index',
                  yloc='yloc.price', color='color.blue',
                  textcolor='color.white', size='size.normal',
                  style='label.style_labeldown', tooltip=''):
        obj_id = self._new_id()
        state = dict(type='label', x=_num(x), y=_num(y), text=str(text),
                     xloc=str(xloc), yloc=str(yloc), color=str(color),
                     textcolor=str(textcolor), size=str(size),
                     style=str(style), tooltip=str(tooltip),
                     bar_created=self.runtime.i)
        self.runtime.draw_objects[obj_id] = state
        self._record('create', 'label', obj_id, state)
        return obj_id

    # ── box ───────────────────────────────────────────────────────────────
    def box_new(self, left, top, right, bottom, border_color='color.blue',
                border_width=1, border_style='line.style_solid',
                bgcolor='color.blue', extend='extend.none'):
        obj_id = self._new_id()
        state = dict(type='box', left=_num(left), top=_num(top),
                     right=_num(right), bottom=_num(bottom),
                     border_color=str(border_color),
                     border_width=int(border_width),
                     border_style=str(border_style),
                     bgcolor=str(bgcolor), extend=str(extend),
                     bar_created=self.runtime.i)
        self.runtime.draw_objects[obj_id] = state
        self._record('create', 'box', obj_id, state)
        return obj_id

    # ── table ─────────────────────────────────────────────────────────────
    def table_new(self, position='position.top_right', columns=1, rows=1,
                  bgcolor='color.gray', border_width=1,
                  border_color='color.black'):
        obj_id = self._new_id()
        if not _finite_int(columns, 1, 100) or not _finite_int(rows, 1, 100):
            raise PineError('table行列须为1至100整数')
        state = dict(type='table', position=str(position),
                     columns=int(columns), rows=int(rows),
                     bgcolor=str(bgcolor), border_width=int(border_width),
                     border_color=str(border_color), cells={},
                     bar_created=self.runtime.i)
        self.runtime.draw_objects[obj_id] = state
        self._record('create', 'table', obj_id,
                     {k: v for k, v in state.items() if k != 'cells'})
        return obj_id

    def table_cell(self, table_id, column, row, text='',
                   text_color='color.black', text_halign='text.align_left',
                   text_valign='text.align_top', bgcolor='color.gray',
                   width=None, height=None):
        obj = self._require(table_id, 'table')
        c = _finite_int(column, 0, obj['columns'] - 1)
        r = _finite_int(row, 0, obj['rows'] - 1)
        cell = dict(text=str(text), text_color=str(text_color),
                    text_halign=str(text_halign), text_valign=str(text_valign),
                    bgcolor=str(bgcolor), width=_num(width, allow_none=True),
                    height=_num(height, allow_none=True))
        obj['cells'][(int(c), int(r))] = cell
        self._record('set', 'table.cell', table_id,
                     dict(column=int(c), row=int(r), **cell))
        return None

    def table_get_cell(self, table_id, column, row, field='text'):
        obj = self._require(table_id, 'table')
        c = _finite_int(column, 0, obj['columns'] - 1)
        r = _finite_int(row, 0, obj['rows'] - 1)
        cell = obj['cells'].get((int(c), int(r)))
        if cell is None:
            return float('nan')
        return cell.get(field, float('nan'))

    # ── polyline ────────────────────────────────────────────────────────
    def polyline_new(self, points, xloc='xloc.bar_index', color='color.blue',
                     width=1, style='line.style_solid', extend='extend.none'):
        obj_id = self._new_id()
        if not isinstance(points, list):
            raise PineError('polyline.points必须是[x,y]数组')
        pts = []
        for p in points:
            if not isinstance(p, (list, tuple)) or len(p) != 2:
                raise PineError('polyline每个点须为[x,y]')
            pts.append([float(p[0]), float(p[1])])
        state = dict(type='polyline', points=pts, xloc=str(xloc),
                     color=str(color), width=int(width), style=str(style),
                     extend=str(extend), bar_created=self.runtime.i)
        self.runtime.draw_objects[obj_id] = state
        self._record('create', 'polyline', obj_id, state)
        return obj_id

    def polyline_set_points(self, obj_id, points):
        obj = self._require(obj_id, 'polyline')
        pts = []
        for p in points:
            if not isinstance(p, (list, tuple)) or len(p) != 2:
                raise PineError('polyline每个点须为[x,y]')
            pts.append([float(p[0]), float(p[1])])
        obj['points'] = pts
        self._record('set', 'polyline', obj_id, dict(points=pts))
        return None

    # ── linefill ──────────────────────────────────────────────────────────
    def linefill_new(self, line1, line2, color='color.blue'):
        a = self._require(line1, 'line')
        b = self._require(line2, 'line')
        obj_id = self._new_id()
        state = dict(type='linefill', line1=line1, line2=line2,
                     color=str(color), bar_created=self.runtime.i)
        self.runtime.draw_objects[obj_id] = state
        self._record('create', 'linefill', obj_id, state)
        return obj_id

    # ── generic set/get/delete ───────────────────────────────────────────
    def set_attr(self, obj_type, obj_id, attr, value):
        obj = self._require(obj_id, obj_type)
        if attr not in obj:
            raise PineError(obj_type + '无属性 ' + attr)
        if attr in ('columns', 'rows', 'cells', 'type', 'bar_created'):
            raise PineError(attr + ' 不可直接修改')
        # numeric coercion for known numeric attrs
        if attr in ('x1', 'y1', 'x2', 'y2', 'x', 'y', 'left', 'top',
                    'right', 'bottom'):
            obj[attr] = _num(value)
        elif attr in ('width', 'border_width'):
            obj[attr] = int(value)
        else:
            obj[attr] = str(value)
        self._record('set', obj_type, obj_id, {attr: obj[attr]})
        return None

    def get_attr(self, obj_type, obj_id, attr):
        obj = self._require(obj_id, obj_type)
        if attr not in obj:
            raise PineError(obj_type + '无属性 ' + attr)
        return obj[attr]

    def delete(self, obj_type, obj_id):
        obj = self._require(obj_id, obj_type)
        self._record('delete', obj_type, obj_id, {})
        self.runtime.draw_objects.pop(obj_id, None)
        return None


def _finite_int(v, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise PineError('需要整数参数')
    v = float(v)
    if not math.isfinite(v) or int(v) != v:
        raise PineError('需要整数参数')
    if not (lo <= int(v) <= hi):
        raise PineError('索引越界')
    return int(v)
