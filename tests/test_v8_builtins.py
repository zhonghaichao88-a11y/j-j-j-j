"""Golden tests for v8 registered builtins: str / ta-extras / math-extras /
array-extras / matrix / map / color.

Run: python3 -m pytest tests/test_v8_builtins.py -q
"""
import math
import unittest

import numpy as np

from alpha_v7_pine import run, compile_source, PineError


def frame(close, high=None, low=None, vol=None):
    close = np.asarray(close, dtype=float)
    n = len(close)
    high = close + 1 if high is None else np.asarray(high, dtype=float)
    low = close - 1 if low is None else np.asarray(low, dtype=float)
    vol = np.ones(n) if vol is None else np.asarray(vol, dtype=float)
    return dict(ts=np.arange(n) * 300000, open=close.copy(), close=close,
                high=high, low=low, volume=vol)


def last(src, fr=None, idx=-1):
    """Run an indicator script, return the `idx`-th plot's last value."""
    fr = frame([10, 11, 12, 11, 10, 9, 10, 11]) if fr is None else fr
    r = run('indicator("t")\n' + src, fr)
    return r['plots'][idx]['values'][-1]


def plots(src, fr=None):
    fr = frame([10, 11, 12, 11, 10, 9, 10, 11]) if fr is None else fr
    r = run('indicator("t")\n' + src, fr)
    return [p['values'][-1] for p in r['plots']]


# ──────────────────────────── str 命名空间 ────────────────────────────
class StrBuiltinTests(unittest.TestCase):
    def test_length(self):
        self.assertEqual(last('plot(str.length("hello"))'), 5.0)

    def test_length_na_propagates(self):
        # str.length(na) -> na -> plot stores None
        self.assertIsNone(last('plot(str.length(na))'))

    def test_tonumber(self):
        self.assertEqual(last('plot(str.tonumber("3.14"))'), 3.14)
        self.assertIsNone(last('plot(str.tonumber("not a number"))'))

    def test_substring_inclusive_end(self):
        # Pine substring: beginPos..endPos inclusive -> "ell"
        self.assertEqual(last('plot(str.length(str.substring("hello", 1, 3)))'), 3.0)

    def test_contains_startswith_endswith(self):
        self.assertEqual(last('plot(str.contains("hello", "ell") ? 1 : 0)'), 1.0)
        self.assertEqual(last('plot(str.contains("hello", "xyz") ? 1 : 0)'), 0.0)
        self.assertEqual(last('plot(str.startswith("hello", "he") ? 1 : 0)'), 1.0)
        self.assertEqual(last('plot(str.endswith("hello", "llo") ? 1 : 0)'), 1.0)

    def test_replace_all_occurrences(self):
        self.assertEqual(
            last('plot(str.length(str.replace("abcbc", "bc", "X")))'), 3.0)

    def test_lower_upper(self):
        self.assertEqual(
            last('plot(str.lower("AbC") == "abc" ? 1 : 0)'), 1.0)
        self.assertEqual(
            last('plot(str.upper("AbC") == "ABC" ? 1 : 0)'), 1.0)

    def test_split_returns_array(self):
        self.assertEqual(last('plot(array.size(str.split("a,b,c", ",")))'), 3.0)

    def test_format_indexed(self):
        self.assertEqual(
            last('plot(str.length(str.format("{0}-{1}", "x", 5)))'), 3.0)

    def test_tostring_number_and_na(self):
        self.assertEqual(
            last('plot(str.length(str.tostring(3.14, "#.##")))'), 4.0)
        self.assertEqual(last('plot(str.length(str.tostring(na)))'), 2.0)  # "na"

    def test_concat(self):
        self.assertEqual(
            last('plot(str.length(str.concat("foo", "bar")))'), 6.0)


# ──────────────────────────── math 额外函数 ────────────────────────────
class MathExtraTests(unittest.TestCase):
    def test_trig_roundtrip(self):
        self.assertAlmostEqual(last('plot(math.sin(0))'), 0.0)
        self.assertAlmostEqual(last('plot(math.cos(0))'), 1.0)
        self.assertAlmostEqual(last('plot(math.tan(0))'), 0.0)
        self.assertAlmostEqual(last('plot(math.asin(0))'), 0.0)
        self.assertAlmostEqual(last('plot(math.acos(1))'), 0.0)
        self.assertAlmostEqual(last('plot(math.atan2(1, 1))'), math.pi / 4, places=6)
        self.assertAlmostEqual(last('plot(math.sinh(0))'), 0.0)
        self.assertAlmostEqual(last('plot(math.cosh(0))'), 1.0)

    def test_mod(self):
        self.assertEqual(last('plot(math.mod(7, 3))'), 1.0)
        self.assertIsNone(last('plot(math.mod(7, 0))'))

    def test_na_propagates(self):
        self.assertIsNone(last('plot(math.sin(na))'))
        self.assertIsNone(last('plot(math.mod(na, 3))'))

    def test_random_deterministic(self):
        fr = frame(list(range(20)))
        a = plots('plot(math.random(0, 100))', fr)[0]
        b = plots('plot(math.random(0, 100))', fr)[0]
        self.assertEqual(a, b)
        self.assertGreaterEqual(a, 0.0)
        self.assertLess(a, 100.0)


# ──────────────────────────── ta 额外指标 ────────────────────────────
class TaExtraTests(unittest.TestCase):
    """close = [10,11,12,11,10,9,10,11], high=close+1, low=close-1, vol=1."""

    def test_obv_cumulative(self):
        # 0,+1,+2,+1,0,-1,0,+1  -> last 1
        self.assertEqual(last('plot(ta.obv())'), 1.0)

    def test_momentum(self):
        # close[7] - close[5] = 11 - 9 = 2
        self.assertEqual(last('plot(ta.momentum(close, 2))'), 2.0)

    def test_wpr_hand_computed(self):
        # last bar window highs=[10,11,12] -> hh=12, lows=[8,9,10] -> ll=8,
        # c=11 -> (12-11)/(12-8)*-100 = -25
        self.assertAlmostEqual(last('plot(ta.wpr(close, 3))'), -25.0, places=4)

    def test_cci_hand_computed(self):
        # window [9,10,11], mean=10, md=2/3 -> (11-10)/(0.015*2/3)=100
        self.assertAlmostEqual(last('plot(ta.cci(close, 3))'), 100.0, places=3)

    def test_mfi_hand_computed(self):
        # TP==close; window bars5..7: pos=10+11=21, neg=9 -> 100-100/(1+21/9)=70
        self.assertAlmostEqual(last('plot(ta.mfi(3))'), 70.0, places=3)

    def test_highestbars_offset(self):
        # at bar4 window [12,11,10] max at oldest -> -2
        fr = frame([10, 11, 12, 11, 10, 9, 10, 11])
        r = run('indicator("t")\nplot(ta.highestbars(close, 3))', fr)
        vals = r['plots'][0]['values']
        self.assertEqual(vals[4], -2.0)
        self.assertEqual(vals[7], 0.0)

    def test_lowestbars_offset(self):
        fr = frame([10, 11, 12, 11, 10, 9, 10, 11])
        r = run('indicator("t")\nplot(ta.lowestbars(close, 3))', fr)
        self.assertEqual(r['plots'][0]['values'][5], 0.0)  # min 9 at bar5

    def test_slope(self):
        # [9,10,11] -> slope 1
        self.assertAlmostEqual(last('plot(ta.slope(close, 3))'), 1.0, places=4)

    def test_percentrank(self):
        # window [9,10,11], current 11, two below -> 66.667
        self.assertAlmostEqual(last('plot(ta.percentrank(close, 3))'),
                               200.0 / 3.0, places=3)

    def test_kc_tuple_ordering(self):
        out = plots('[b, u, l] = ta.kc(close, 3, 2, true)\nplot(b)\nplot(u)\nplot(l)')
        b, u, l = out
        self.assertGreater(u, b)
        self.assertGreater(b, l)

    def test_donchian_tuple(self):
        out = plots('[u, lo, m] = ta.donchian(3)\nplot(u)\nplot(lo)\nplot(m)')
        u, lo, m = out
        self.assertAlmostEqual(u, 12.0)
        self.assertAlmostEqual(lo, 8.0)
        self.assertAlmostEqual(m, 10.0)

    def test_insufficient_history_na(self):
        self.assertIsNone(last('plot(ta.wpr(close, 50))'))
        self.assertIsNone(last('plot(ta.momentum(close, 50))'))


# ──────────────────────────── array 额外操作 ────────────────────────────
class ArrayExtraTests(unittest.TestCase):
    def test_insert_sort_first_last(self):
        src = ('a = array.new_float()\n'
               'array.push(a, 3)\narray.push(a, 1)\narray.push(a, 2)\n'
               'array.insert(a, 0, 9)\n'
               'array.sort(a, order.descending)\n'
               'plot(array.first(a))\nplot(array.last(a))')
        self.assertEqual(plots(src), [9.0, 1.0])

    def test_slice(self):
        src = ('a = array.from(1, 2, 3, 4)\n'
               'b = array.slice(a, 1, 3)\n'
               'plot(array.size(b))\nplot(array.get(b, 0))')
        self.assertEqual(plots(src), [2.0, 2.0])

    def test_fill(self):
        src = ('a = array.from(1, 2, 3, 4)\n'
               'array.fill(a, 0, 1, 3)\n'
               'plot(array.sum(a))')
        self.assertEqual(plots(src), [1.0 + 0 + 0 + 4.0])

    def test_join(self):
        src = 'a = array.from(1, 2, 3)\nplot(str.length(array.join(a, "|")))'
        self.assertEqual(plots(src), [5.0])  # "1|2|3"

    def test_sort_ascending_default(self):
        src = ('a = array.from(3, 1, 2)\narray.sort(a)\nplot(array.first(a))')
        self.assertEqual(plots(src), [1.0])

    def test_first_empty_raises(self):
        with self.assertRaises(PineError):
            last('a = array.new_float()\nplot(array.first(a))')


# ──────────────────────────── matrix 命名空间 ────────────────────────────
class MatrixTests(unittest.TestCase):
    def test_new_get_set_sum(self):
        src = ('m = matrix.new_float(2, 2, 1.5)\n'
               'matrix.set(m, 0, 0, 4)\n'
               'plot(matrix.sum(m))\nplot(matrix.rows(m))\nplot(matrix.cols(m))')
        self.assertEqual(plots(src), [4 + 1.5 * 3, 2.0, 2.0])

    def test_transpose(self):
        src = ('m = matrix.new_float(1, 2, 1)\n'
               'matrix.set(m, 0, 1, 9)\n'
               't = matrix.transpose(m)\n'
               'plot(matrix.get(t, 1, 0))')
        self.assertEqual(plots(src), [9.0])

    def test_elementwise_add(self):
        src = ('a = matrix.new_float(2, 2, 1)\n'
               'b = matrix.new_float(2, 2, 2)\n'
               'c = matrix.add(a, b)\n'
               'plot(matrix.sum(c))')
        self.assertEqual(plots(src), [12.0])  # 4 cells * 3

    def test_reshape(self):
        src = ('a = matrix.new_float(2, 2, 1)\n'
               'b = matrix.reshape(a, 1, 4)\n'
               'plot(matrix.rows(b))\nplot(matrix.cols(b))\nplot(matrix.sum(b))')
        self.assertEqual(plots(src), [1.0, 4.0, 4.0])

    def test_fill_clear(self):
        src = ('m = matrix.new_float(2, 2, 1)\n'
               'matrix.fill(m, 5)\nplot(matrix.sum(m))\n'
               'matrix.clear(m)\nplot(matrix.avg(m))')
        out = plots(src)
        self.assertEqual(out[0], 20.0)
        self.assertIsNone(out[1])


# ──────────────────────────── map 命名空间 ────────────────────────────
class MapTests(unittest.TestCase):
    def test_put_get_size_contains(self):
        src = ('mp = map.new("string", "float")\n'
               'map.put(mp, "x", 7)\nmap.put(mp, "y", 8)\n'
               'plot(map.get(mp, "x"))\nplot(map.size(mp))\n'
               'plot(map.contains(mp, "y") ? 1 : 0)')
        self.assertEqual(plots(src), [7.0, 2.0, 1.0])

    def test_missing_key_na(self):
        src = 'mp = map.new("string", "float")\nplot(map.get(mp, "nope"))'
        self.assertIsNone(plots(src)[0])

    def test_remove_keys_values(self):
        src = ('mp = map.new("string", "float")\n'
               'map.put(mp, "x", 1)\nmap.put(mp, "y", 2)\n'
               'map.remove(mp, "x")\nplot(map.size(mp))\n'
               'plot(array.size(map.values(mp)))')
        self.assertEqual(plots(src), [1.0, 1.0])


# ──────────────────────────── color 命名空间 ────────────────────────────
class ColorTests(unittest.TestCase):
    def test_rgb(self):
        r = run('indicator("t")\nplot(close, color=color.rgb(255, 0, 0))',
                frame([1, 2, 3]))
        self.assertEqual(r['plots'][0]['color'], '#FF0000FF')

    def test_new_transparency(self):
        r = run('indicator("t")\nplot(close, color=color.new(color.red, 50))',
                frame([1, 2, 3]))
        self.assertEqual(r['plots'][0]['color'], '#FF000080')

    def test_tostring_named(self):
        src = 'c = color.tostring(color.blue)\nplot(str.length(c))'
        self.assertEqual(last(src), 7.0)  # "#0000FF"

    def test_constant_passthrough(self):
        r = run('indicator("t")\nplot(close, color=color.teal)', frame([1, 2]))
        self.assertEqual(r['plots'][0]['color'], 'color.teal')


# ──────────────────────────── 注册完整性 ────────────────────────────
class RegistryCompletenessTests(unittest.TestCase):
    def test_expected_names_registered(self):
        from pine_v5.builtins.registry import REGISTRY
        expected = [
            'str.tostring', 'str.tonumber', 'str.length', 'str.substring',
            'str.contains', 'str.startswith', 'str.endswith', 'str.replace',
            'str.lower', 'str.upper', 'str.split', 'str.concat', 'str.format',
            'ta.wpr', 'ta.cci', 'ta.mfi', 'ta.obv', 'ta.sar', 'ta.kc',
            'ta.donchian', 'ta.highestbars', 'ta.lowestbars', 'ta.momentum',
            'ta.slope', 'ta.percentrank',
            'math.sin', 'math.cos', 'math.tan', 'math.asin', 'math.acos',
            'math.atan', 'math.atan2', 'math.sinh', 'math.cosh', 'math.mod',
            'math.random',
            'array.insert', 'array.fill', 'array.join', 'array.sort',
            'array.slice', 'array.first', 'array.last',
            'matrix.new', 'matrix.new_float', 'matrix.new_int',
            'matrix.new_bool', 'matrix.new_string', 'matrix.new_color',
            'matrix.get', 'matrix.set', 'matrix.rows', 'matrix.cols',
            'matrix.fill', 'matrix.copy', 'matrix.reshape',
            'matrix.transpose', 'matrix.add', 'matrix.sub', 'matrix.mul',
            'matrix.sum', 'matrix.avg', 'matrix.max', 'matrix.min',
            'matrix.clear',
            'map.new', 'map.put', 'map.get', 'map.remove', 'map.contains',
            'map.size', 'map.keys', 'map.values', 'map.clear', 'map.copy',
            'color.new', 'color.rgb', 'color.from_gradient', 'color.tostring',
        ]
        missing = [n for n in expected if n not in REGISTRY]
        self.assertFalse(missing, '未注册: %s' % missing)


if __name__ == '__main__':
    unittest.main()
