"""Pine v8 syntax extensions: while / switch / multi-statement functions /
method calls / UDT / varip / import / global ternary streaming.

Each feature has at least one happy-path and one fail-closed case.
"""
import unittest
import numpy as np
from alpha_v7_pine import (
    run, compile_source, Runtime, PineError,
)


def frame(n=40):
    c = 100 + np.sin(np.arange(n) * .19) * 6
    return dict(ts=np.arange(n) * 300000, open=c, close=c,
                high=c + 1, low=c - 1, volume=np.ones(n) * 100)


class WhileLoopTests(unittest.TestCase):
    def test_while_accumulates(self):
        src = ('indicator("w")\ni = 0\ns = 0\n'
               'while i < 5\n    i := i + 1\n    s := s + i\nplot(s)')
        r = run(src, frame())
        self.assertEqual(r['plots'][0]['values'][-1], 15.0)

    def test_while_break_continue(self):
        src = ('indicator("w")\ni = 0\ns = 0\n'
               'while i < 100\n    i := i + 1\n'
               '    if i == 3\n        continue\n'
               '    if i == 6\n        break\n'
               '    s := s + i\nplot(s)')
        r = run(src, frame())
        self.assertEqual(r['plots'][0]['values'][-1], 12.0)

    def test_while_iteration_cap(self):
        src = ('indicator("w")\ni = 0\nwhile i < 100000\n    i := i + 1\nplot(i)')
        with self.assertRaises(PineError):
            run(src, frame())

    def test_while_requires_body(self):
        with self.assertRaises(PineError):
            compile_source('indicator("w")\nwhile true')

    def test_while_without_body_indent(self):
        with self.assertRaises(PineError):
            compile_source('indicator("w")\nwhile true\nplot(close)')


class SwitchTests(unittest.TestCase):
    def test_switch_on_value(self):
        src = ('indicator("s")\nk = 2\nv = 0\nswitch k\n'
               '    1 => v := 10\n    2 => v := 20\n    => v := 99\nplot(v)')
        self.assertEqual(run(src, frame())['plots'][0]['values'][-1], 20.0)

    def test_switch_default(self):
        src = ('indicator("s")\nk = 9\nv = 0\nswitch k\n'
               '    1 => v := 10\n    => v := 99\nplot(v)')
        self.assertEqual(run(src, frame())['plots'][0]['values'][-1], 99.0)

    def test_switch_condition_form(self):
        src = ('indicator("s")\nv = 0\nswitch\n'
               '    close > 101 => v := 1\n'
               '    close < 99 => v := 2\n    => v := 3\nplot(v)')
        r = run(src, frame())
        self.assertIn(r['plots'][0]['values'][-1], (1.0, 2.0, 3.0))

    def test_switch_block_body(self):
        src = ('indicator("s")\nk = 1\nv = 0\nswitch k\n'
               '    1 =>\n        v := 5\n        v := v + 5\n'
               '    => v := 0\nplot(v)')
        self.assertEqual(run(src, frame())['plots'][0]['values'][-1], 10.0)

    def test_switch_branch_must_use_arrow(self):
        with self.assertRaises(PineError):
            compile_source('indicator("s")\nswitch k\n    1\n')


class MultiStatementFunctionTests(unittest.TestCase):
    def test_multi_statement_body_returns_last(self):
        src = ('indicator("f")\naddmul(a, b) =>\n    s = a + b\n    m = s * 2\n    m\n'
               'plot(addmul(3, 4))')
        self.assertEqual(run(src, frame())['plots'][0]['values'][-1], 14.0)

    def test_function_with_if_and_var(self):
        src = ('indicator("f")\nf(x) =>\n    var count = 0\n'
               '    count := count + 1\n    y = x\n'
               '    if x > 100\n        y := x\n    else\n        y := count\n'
               '    y\nplot(f(close))')
        r = run(src, frame())
        self.assertIsNotNone(r['plots'][0]['values'][-1])

    def test_recursion_rejected(self):
        src = 'indicator("r")\nf(n) =>\n    f(n - 1)\nplot(f(5))'
        with self.assertRaises(PineError):
            run(src, frame())

    def test_single_line_function_still_works(self):
        src = 'indicator("f")\ndouble(x) => x * 2\nplot(double(close))'
        r = run(src, frame())
        self.assertAlmostEqual(r['plots'][0]['values'][-1],
                               frame()['close'][-1] * 2)


class MethodCallTests(unittest.TestCase):
    def test_array_method_sugar(self):
        src = ('indicator("m")\na = array.new_float(3, 1.0)\n'
               'a.push(5.0)\nplot(array.size(a))')
        self.assertEqual(run(src, frame())['plots'][0]['values'][-1], 4.0)

    def test_array_pop_method(self):
        src = ('indicator("m")\na = array.from(1.0, 2.0, 3.0)\n'
               'x = a.pop()\nplot(x)')
        self.assertEqual(run(src, frame())['plots'][0]['values'][-1], 3.0)


class UDTTests(unittest.TestCase):
    def test_define_instantiate_access(self):
        src = ('indicator("u")\ntype Point\n    float x\n    float y\n'
               'p = Point.new(x=1.0, y=2.0)\nplot(p.x + p.y)')
        self.assertEqual(run(src, frame())['plots'][0]['values'][-1], 3.0)

    def test_constructor_missing_field(self):
        src = ('indicator("u")\ntype P\n    float x\n    float y\n'
               'p = P.new(x=1.0)\nplot(p.x)')
        with self.assertRaises(PineError):
            run(src, frame())

    def test_unknown_field_access(self):
        src = ('indicator("u")\ntype P\n    float x\n'
               'p = P.new(x=1.0)\nplot(p.z)')
        with self.assertRaises(PineError):
            run(src, frame())


class VaripTests(unittest.TestCase):
    def test_varip_rejected(self):
        with self.assertRaises(PineError):
            compile_source('indicator("x")\nvarip int z = 0')


class GlobalTernaryStreamingTests(unittest.TestCase):
    def test_global_ternary_with_indicator(self):
        # Both branches stream; ta.ema accumulates every bar.
        src = ('indicator("t")\ne = ta.ema(close, 5)\n'
               'x = close > 100 ? e : close\nplot(x)')
        r = run(src, frame())
        self.assertIsNotNone(r['plots'][0]['values'][-1])

    def test_if_condition_indicator_still_rejected(self):
        # ta.* directly in an if condition remains forbidden.
        with self.assertRaises(PineError):
            compile_source('indicator("x")\nif ta.ema(close, 5) > 0\n    plot(close)')


class ImportTests(unittest.TestCase):
    def _write_lib(self, tmp_path, name, body):
        path = tmp_path / (name + '.pine')
        path.write_text(body, encoding='utf-8')
        return str(tmp_path)

    def test_import_local_function(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'mylib.pine'), 'w') as f:
                f.write('doubleit(x) => x * 2\n')
            src = ('indicator("imp")\nimport mylib\n'
                   'plot(mylib.doubleit(close))')
            prog = compile_source(src, search_dir=d)
            r = Runtime(prog, frame()).run()
            self.assertAlmostEqual(r['plots'][0]['values'][-1],
                                   frame()['close'][-1] * 2)

    def test_import_as_alias(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'mathlib.pine'), 'w') as f:
                f.write('triple(x) =>\n    y = x * 3\n    y\n')
            src = ('indicator("imp")\nimport mathlib as m\n'
                   'plot(m.triple(close))')
            prog = compile_source(src, search_dir=d)
            r = Runtime(prog, frame()).run()
            self.assertAlmostEqual(r['plots'][0]['values'][-1],
                                   frame()['close'][-1] * 3)

    def test_cloud_import_rejected(self):
        with self.assertRaises(PineError):
            compile_source('indicator("x")\nimport {ta} from "tradingview/ta"')

    def test_missing_module_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(PineError):
                compile_source('indicator("x")\nimport nosuchmodule',
                               search_dir=d)


if __name__ == '__main__':
    unittest.main()
