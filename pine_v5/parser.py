"""Pine v5 parser — expression parser, AST resolver, and source compiler."""
from __future__ import annotations
import re, hashlib
from .constants import (
    PineError, CALLS, ALLOWED_KW, CONSTANTS, BASES, ALIASES, SIGNATURES,
)
from .lexer import tokenize, logical_lines
from .builtins.registry import REGISTRY

# Namespaces that are never treated as the receiver of a method-call sugar.
KNOWN_NS = {
    'ta', 'math', 'array', 'matrix', 'map', 'line', 'label', 'box',
    'table', 'polyline', 'color', 'timeframe', 'syminfo', 'barstate',
    'input', 'strategy', 'request', 'str',
}
# Namespaces that may receive method-call sugar, in priority order.
METHOD_NS = ('array', 'matrix', 'map', 'line', 'label', 'box', 'table',
             'polyline')


def is_supported_call(name):
    """Check if a function name is supported (core CALLS or registry)."""
    return name in CALLS or name in REGISTRY


class Parser:
    """Recursive-descent expression parser with precedence climbing."""
    PRE = {'or': 1, 'and': 2, '==': 3, '!=': 3, '>': 4, '<': 4,
           '>=': 4, '<=': 4, '+': 5, '-': 5, '*': 6, '/': 6, '%': 6}

    def __init__(self, s):
        self.tokens = tokenize(s)
        self.i = 0

    def peek(self):
        return self.tokens[self.i][1]

    def pop(self):
        r = self.tokens[self.i]
        self.i += 1
        return r

    def need(self, x):
        if self.peek() != x:
            raise PineError('应为 ' + x)
        self.pop()

    def expression(self, p=0):
        kind, t = self.pop()
        if t in ('-', '+', 'not'):
            left = ('unary', t, self.expression(7))
        elif t == '(':
            left = self.expression()
            self.need(')')
        elif t == '[':
            values = []
            if self.peek() != ']':
                while True:
                    values.append(self.expression())
                    if self.peek() != ',':
                        break
                    self.pop()
            self.need(']')
            left = ('list', values)
        elif kind in ('num', 'str'):
            left = ('const', t)
        elif kind == 'id':
            if self.peek() == '(':
                self.pop()
                args = []
                kwargs = {}
                if self.peek() != ')':
                    while True:
                        if (self.tokens[self.i][0] == 'id'
                                and self.tokens[self.i + 1][1] == '='):
                            name = self.pop()[1]
                            self.pop()
                            if name in kwargs:
                                raise PineError('重复命名参数 ' + name)
                            kwargs[name] = self.expression()
                        else:
                            if kwargs:
                                raise PineError('位置参数必须在命名参数之前')
                            args.append(self.expression())
                        if self.peek() != ',':
                            break
                        self.pop()
                self.need(')')
                left = ('call', t, args, kwargs)
            else:
                left = ('name', t)
        else:
            raise PineError('无效表达式')
        while True:
            op = self.peek()
            if op == '[':
                self.pop()
                offset = self.expression()
                self.need(']')
                left = ('history', left, offset)
                continue
            prec = self.PRE.get(op, -1)
            if prec < p:
                break
            self.pop()
            left = ('binary', op, left, self.expression(prec + 1))
        if p == 0 and self.peek() == '?':
            self.pop()
            yes = self.expression()
            self.need(':')
            no = self.expression()
            left = ('ternary', left, yes, no)
        return left

    def parse(self):
        n = self.expression()
        if self.tokens[self.i][0] != 'end':
            raise PineError('表达式剩余未解析内容：' + str(self.peek()))
        return n


def walk(n):
    """Yield every AST node in the tree (depth-first)."""
    yield n
    for x in n[1:]:
        if isinstance(x, tuple):
            yield from walk(x)
        elif isinstance(x, list):
            for y in x:
                if isinstance(y, tuple):
                    yield from walk(y)
        elif isinstance(x, dict):
            for y in x.values():
                if isinstance(y, tuple):
                    yield from walk(y)


def resolve_expression(node, scope, user_functions=None,
                       udt_types=None, modules=None, extra_allowed=None):
    """Resolve names in an expression AST to internal variable IDs.

    Normalises:
      * array / generic method calls ``obj.m(...)`` -> ``ns.m(obj, ...)``
      * UDT construction ``T(...)`` / ``T.new(...)`` -> ``('udt_new', ...)``
      * UDT field access ``point.x`` -> ``('field', point, 'x')``
    """
    user_functions = user_functions or {}
    udt_types = udt_types or set()
    modules = modules or set()
    extra_allowed = extra_allowed or set()
    kind = node[0]
    if kind == 'name':
        n = node[1]
        if '.' in n:
            head, tail = n.rsplit('.', 1)
            if head in scope and head not in KNOWN_NS:
                return ('field',
                        resolve_expression(('name', head), scope,
                                           user_functions, udt_types,
                                           modules, extra_allowed),
                        tail)
        return ('name', scope.get(n, n))
    if kind == 'call':
        raw_name = node[1]
        args = [resolve_expression(x, scope, user_functions, udt_types,
                                   modules, extra_allowed) for x in node[2]]
        kw = {k: resolve_expression(v, scope, user_functions, udt_types,
                                    modules, extra_allowed)
              for k, v in node[3].items()}
        # UDT constructor: T(...) or T.new(...)
        udt_name = None
        if raw_name in udt_types:
            udt_name = raw_name
        elif raw_name.endswith('.new') and raw_name[:-4] in udt_types:
            udt_name = raw_name[:-4]
        if udt_name:
            return ('udt_new', udt_name, args, kw)
        # Generic method-call sugar: obj.method(...) -> ns.method(obj, ...)
        if '.' in raw_name:
            head, tail = raw_name.rsplit('.', 1)
            if head not in KNOWN_NS and head not in modules:
                candidates = [ns for ns in METHOD_NS
                              if ns + '.' + tail in CALLS
                              or ns + '.' + tail in REGISTRY]
                if candidates:
                    args = [resolve_expression(
                        ('name', head), scope, user_functions, udt_types,
                        modules, extra_allowed)] + args
                    raw_name = candidates[0] + '.' + tail
        name = ALIASES.get(raw_name, raw_name)
        # User-defined function call
        if name in user_functions:
            params = user_functions[name]['params']
            if (len(args) > len(params)
                    or set(kw) - set(params)
                    or set(params[:len(args)]) & kw.keys()):
                raise PineError(name + ' 函数参数不匹配')
            supplied = dict(zip(params, args))
            supplied.update(kw)
            if any(param not in supplied for param in params):
                raise PineError(name + ' 函数缺少必需参数')
            args = [supplied[param] for param in params]
            kw = {}
        # Signature-based builtin resolution
        if name in ('ta.highest', 'ta.lowest'):
            signature = ('length',) if len(args) + len(kw) == 1 else ('source', 'length')
        elif (name in ('ta.pivothigh', 'ta.pivotlow')
              and len(args) + len(kw) == 2 and 'source' not in kw):
            signature = ('leftbars', 'rightbars')
        else:
            signature = SIGNATURES.get(name)
        if signature:
            if len(args) > len(signature) or set(kw) - set(signature):
                raise PineError(name + ' 参数不匹配')
            supplied = dict(zip(signature, args))
            if supplied.keys() & kw.keys():
                raise PineError(name + ' 参数重复')
            supplied.update(kw)
            required = (3 if name == 'request.security'
                        else len(signature) - (
                            1 if name in ('ta.stdev', 'ta.change',
                                          'ta.pivothigh', 'ta.pivotlow')
                            else 0))
            if any(k not in supplied for k in signature[:required]):
                raise PineError(name + ' 缺少必需参数')
            args = [supplied[k] for k in signature if k in supplied]
            kw = {}
        else:
            limit = (2 if name.startswith('input') or name in (
                'plot', 'plotshape', 'hline', 'strategy.entry', 'strategy.exit')
                else 1 if name in ('strategy.close', 'indicator', 'study',
                                   'strategy', 'na')
                else 3 if name == 'alertcondition'
                else 2 if name == 'nz'
                else None)
            if limit is not None and len(args) > limit:
                raise PineError(name + ' 额外参数请使用已支持的命名参数')
            positional = (
                ('id', 'direction') if name == 'strategy.entry'
                else ('id', 'from_entry') if name == 'strategy.exit'
                else ('id',) if name == 'strategy.close'
                else ('defval', 'title') if name.startswith('input')
                else ('title',) if name in ('indicator', 'study', 'strategy')
                else ('series', 'title') if name in ('plot', 'plotshape')
                else ('price', 'title') if name == 'hline'
                else ('condition', 'title', 'message') if name == 'alertcondition'
                else ()
            )
            if set(positional[:len(args)]) & kw.keys():
                raise PineError(name + ' 参数重复')
        return ('call', name, args, kw)
    return tuple(
        resolve_expression(x, scope, user_functions, udt_types,
                           modules, extra_allowed) if isinstance(x, tuple)
        else [resolve_expression(y, scope, user_functions, udt_types,
                                 modules, extra_allowed) for y in x]
        if isinstance(x, list)
        else x
        for x in node
    )


# ── Statement-block compilation ──────────────────────────────────────────────
class _Ctx:
    """Mutable state shared while compiling a statement block."""

    def __init__(self, root_block, global_scope, defined, user_functions,
                 udt_types, modules, extra_allowed):
        self.stack = [(-1, root_block, 'block')]
        self.scopes = {id(root_block): dict(global_scope)}
        self.locals_by_scope = {id(root_block): set()}
        self.defined = set(defined)
        self.loop_bodies = set()
        self.nodes = 0
        self.input_names = set()
        self.user_functions = user_functions
        self.udt_types = udt_types
        self.modules = modules
        self.extra_allowed = extra_allowed
        self.declaration = None


def _strip_comment(raw):
    clean = []
    quote = None
    escape = False
    for i, ch in enumerate(raw):
        if not quote and ch == '/' and i + 1 < len(raw) and raw[i + 1] == '/':
            break
        clean.append(ch)
        if escape:
            escape = False
            continue
        if ch == '\\' and quote:
            escape = True
            continue
        if ch in ('"', "'"):
            if quote == ch:
                quote = None
            elif quote is None:
                quote = ch
    return ''.join(clean).rstrip()


def _validate_call_node(ctx, stmt_node, expr, target_is_root, is_condition):
    """Run the statement guards on a resolved expression node.

    ``is_condition`` is True only for the controlling expression of an
    if/while/switch or a switch case, where stateful ta.* calls inside a
    ternary / and / or must be rejected (the block may not run on every bar).
    At global value positions (assign / expression statement) Pine streams
    both sides of a ternary / and-or every bar, so stateful calls are allowed.
    """
    # Guard: stateful indicators inside a conditional ternary/and-or.
    if is_condition:
        for branch in walk(expr):
            if (branch[0] == 'ternary'
                    or branch[0] == 'binary' and branch[1] in ('and', 'or')):
                if any(x[0] == 'call'
                       and (x[1].startswith('ta.')
                            or x[1] in ('ema', 'rma', 'sma', 'rsi', 'atr'))
                       for x in walk(branch)):
                    raise PineError(
                        '条件表达式内指标须先在全局计算，防止历史状态跳步')
    for x in walk(expr):
        ctx.nodes += 1
        if ctx.nodes > 2500:
            raise PineError('表达式过于复杂')
        if x[0] == 'call':
            name = x[1]
            if name.startswith('input'):
                if (stmt_node is None or stmt_node['kind'] != 'assign'
                        or len(ctx.stack) > 1):
                    raise PineError('input必须在全局变量中直接声明')
                ctx.input_names.add(stmt_node['input_name'])
            if (name.startswith(('ta.', 'ema', 'rma', 'rsi', 'atr', 'sma'))
                    and len(ctx.stack) > 1):
                raise PineError(
                    '有状态指标必须在全局每根K线计算，再在if/while/switch中使用结果')
            if (not is_supported_call(name)
                    and name not in ctx.user_functions
                    and name not in ctx.extra_allowed):
                raise PineError('不支持函数 ' + name + '；禁止静默忽略')
            if (name in ('plot', 'plotshape', 'hline', 'alertcondition')
                    and not target_is_root):
                raise PineError('绘图和alertcondition必须位于全局')
            allowed = ALLOWED_KW.get(
                name,
                ALLOWED_KW['input'] if name.startswith('input.') else set())
            if set(x[3]) - allowed:
                raise PineError(name + ' 不支持参数 '
                                + ','.join(sorted(set(x[3]) - allowed)))
            if name in ('indicator', 'study', 'strategy'):
                if not target_is_root or (stmt_node is not None
                                          and stmt_node['kind'] != 'expression'):
                    raise PineError('脚本声明必须位于全局')
                if ctx.declaration:
                    raise PineError('只能有一个indicator/strategy声明')
                ctx.declaration = name
            if name in ('plot', 'plotshape') and 'offset' in x[3]:
                offset = x[3]['offset']
                if (offset[0] == 'const'
                        and (int(offset[1]) != offset[1]
                             or abs(offset[1]) > 5000)):
                    raise PineError('绘图offset必须为-5000至5000整数')
        elif x[0] == 'name':
            name = x[1]
            if (name not in ctx.defined
                    and not name.startswith((
                        'color.', 'plot.', 'shape.', 'location.', 'size.',
                        'display.', 'hline.', 'input.', 'syminfo.',
                        'timeframe.', 'barmerge.', 'format.', 'alert.',
                        'scale.', 'style.', 'order.', 'xloc.', 'yloc.',
                        'extend.', 'line.', 'label.', 'box.', 'table.',
                        'session.', 'currency.', 'direction.', 'strategy.',
                        'barstate.', 'ticker.', 'math.', 'ta.', 'str.',
                        'array.', 'matrix.', 'map.', 'request.',
                        'polyline.', 'linefill.'))):
                raise PineError('未定义或不支持标识符 ' + name)


def _compile_line(ctx, line_no, raw, root_block):
    """Compile one logical line, appending statement nodes to ctx state."""
    raw = _strip_comment(raw)
    if not raw.strip():
        return
    if '\t' in raw:
        raise PineError(f'第{line_no}行：请使用空格缩进')
    indent = len(raw) - len(raw.lstrip())
    text = raw.strip()
    while len(ctx.stack) > 1 and indent <= ctx.stack[-1][0]:
        ctx.stack.pop()
    frame = ctx.stack[-1]
    frame_indent, target, frame_kind = frame
    target_is_root = target is root_block
    try:
        expected_indent = 0 if len(ctx.stack) == 1 else frame_indent + 4
        if indent != expected_indent:
            # Allow non-4-space indentation (e.g. 2-space v3 scripts) as
            # long as it is deeper than the parent frame.
            if len(ctx.stack) == 1 or indent <= frame_indent:
                raise PineError('局部块缩进必须深于父级')
            # Adjust frame_indent to the actual indentation used
            ctx.stack[-1] = (indent, target, frame_kind)
            frame_indent = indent
        scope = ctx.scopes[id(target)]
        new_names = []
        node = None
        if frame_kind == 'switch':
            m = re.match(r'^(.*?)\s*=>\s*(.*)$', text)
            if not m:
                raise PineError('switch分支须形如 `值或条件 => 语句`')
            cond_src, rhs = m.group(1).strip(), m.group(2).strip()
            is_default = (cond_src == '')
            if is_default:
                if target['default']:
                    raise PineError('switch重复default分支')
                body = target['default']
            else:
                cond = resolve_expression(
                    Parser(cond_src).parse(), scope, ctx.user_functions,
                    ctx.udt_types, ctx.modules, ctx.extra_allowed)
                body = []
                target['cases'].append(
                    {'cond': cond, 'body': body, 'line': line_no})
            ctx.scopes[id(body)] = dict(scope)
            ctx.locals_by_scope[id(body)] = set()
            if rhs:
                # single-line case body
                stmt, new_names = _statement_from_text(
                    ctx, line_no, rhs, body, scope, target_is_root=False)
                if 'expr' in stmt:
                    stmt['expr'] = resolve_expression(
                        stmt['expr'], scope, ctx.user_functions,
                        ctx.udt_types, ctx.modules, ctx.extra_allowed)
                if new_names:
                    _register_names(ctx, scope, body, new_names)
                if 'expr' in stmt:
                    _validate_call_node(ctx, stmt, stmt['expr'],
                                        target_is_root=False,
                                        is_condition=False)
            else:
                ctx.stack.append((indent, body, 'block'))
            if not is_default:
                _validate_call_node(ctx, None, cond, target_is_root=False,
                                    is_condition=True)
            return

        if text.startswith('if '):
            node = {'kind': 'if', 'expr': Parser(text[3:]).parse(),
                    'body': [], 'else': [], 'line': line_no}
            target.append(node)
            ctx.stack.append((indent, node['body'], 'block'))
        elif text == 'else' or text.startswith('else if '):
            if not target or target[-1]['kind'] != 'if':
                raise PineError('else必须紧随if')
            parent = target[-1]
            while parent.get('elif_child'):
                parent = parent['else'][0]
            if parent.get('else_seen'):
                raise PineError('重复else分支')
            parent['else_seen'] = True
            if text == 'else':
                ctx.stack.append((indent, parent['else'], 'block'))
                return
            node = {'kind': 'if', 'expr': Parser(text[8:]).parse(),
                    'body': [], 'else': [], 'line': line_no}
            parent['else'].append(node)
            parent['elif_child'] = True
            ctx.stack.append((indent, node['body'], 'block'))
        elif text.startswith('for '):
            m = re.match(
                r'^for\s+([A-Za-z_]\w*)\s*=\s*(.+?)\s+to\s+(.+?)'
                r'(?:\s+by\s+(.+))?$', text)
            if not m:
                raise PineError('for循环语法无效')
            loop_name, start_expr, end_expr, step_expr = m.groups()
            loop_var = f'pineloop_{line_no}_{loop_name}'
            node = {
                'kind': 'for', 'name': loop_var,
                'expr': ('list', [
                    Parser(start_expr).parse(),
                    Parser(end_expr).parse(),
                    Parser(step_expr).parse() if step_expr
                    else ('const', 1.)]),
                'body': [], 'line': line_no}
            target.append(node)
            ctx.loop_bodies.add(id(node['body']))
            ctx.stack.append((indent, node['body'], 'block'))
        elif text.startswith('while '):
            node = {'kind': 'while', 'expr': Parser(text[6:]).parse(),
                    'body': [], 'line': line_no}
            target.append(node)
            ctx.loop_bodies.add(id(node['body']))
            ctx.stack.append((indent, node['body'], 'block'))
        elif text == 'switch' or text.startswith('switch '):
            rest = text[len('switch'):].strip()
            node = {'kind': 'switch',
                    'expr': Parser(rest).parse() if rest else None,
                    'cases': [], 'default': [], 'line': line_no}
            target.append(node)
            ctx.scopes[id(node)] = scope
            ctx.locals_by_scope[id(node)] = set()
            ctx.stack.append((indent, node, 'switch'))
        elif text in ('break', 'continue'):
            if not any(id(block) in ctx.loop_bodies
                       for _, block, _ in ctx.stack
                       if isinstance(block, list)):
                raise PineError(text + '只能用于for/while循环内部')
            node = {'kind': text, 'line': line_no}
            target.append(node)
        elif text.startswith('varip'):
            raise PineError(
                'varip 需要逐tick执行模式，当前仅支持收盘K线；'
                '请改用 var')
        else:
            node, new_names = _statement_from_text(
                ctx, line_no, text, target, scope,
                target_is_root=target_is_root)

        if node is None:
            return
        # Resolve + guard the expression of this statement
        if node.get('expr') is not None:
            node['expr'] = resolve_expression(
                node['expr'], scope, ctx.user_functions, ctx.udt_types,
                ctx.modules, ctx.extra_allowed)
        is_condition = node['kind'] in ('if', 'while', 'switch')
        if node.get('expr') is not None:
            _validate_call_node(ctx, node, node['expr'],
                                target_is_root, is_condition)
        # Declare new locals only after resolving AND validating the RHS
        # (so x = x + 1 raises an undefined-identifier error).
        if new_names:
            _register_names(ctx, scope, target, new_names)
        if node['kind'] == 'if':
            for block in (node['body'], node['else']):
                ctx.scopes[id(block)] = dict(scope)
                ctx.locals_by_scope[id(block)] = set()
        if node['kind'] == 'for':
            loop_scope = dict(scope)
            loop_scope[loop_name] = loop_var
            ctx.scopes[id(node['body'])] = loop_scope
            ctx.locals_by_scope[id(node['body'])] = set()
            ctx.defined.add(loop_var)
        if node['kind'] == 'while':
            ctx.scopes[id(node['body'])] = dict(scope)
            ctx.locals_by_scope[id(node['body'])] = set()
        if node['kind'] == 'switch':
            for block in ([c['body'] for c in node['cases']] + [node['default']]):
                ctx.scopes[id(block)] = dict(scope)
                ctx.locals_by_scope[id(block)] = set()
    except (PineError, RecursionError, IndexError) as exc:
        raise PineError(f'第{line_no}行：{exc}') from exc


def _statement_from_text(ctx, line_no, text, target, scope,
                         target_is_root):
    """Parse a plain statement (assign or expression) into a node dict.

    Returns ``(node, new_names)`` where ``new_names`` is the list of
    ``(declared_name, resolved_name)`` pairs introduced by an assignment;
    the caller registers them in scope only *after* the right-hand side has
    been resolved, so ``x = x + 1`` correctly rejects the forward reference.
    """
    m = re.match(
        r'(?:(var|varip|const)\s+)?'
        r'(?:(?:float|int|integer|bool|string|color)\s+|'
        r'(?:array\s*<\s*(?:float|int|integer|bool|string|color)\s*>|'
        r'(?:float|int|integer|bool|string|color)\[\])\s+)?'
        r'(\[[\w,\s]+\]|[A-Za-z_]\w*)\s*'
        r'(:=|=(?!=))\s*(.+)$', text)
    if m:
        persistent, lhs, op, rhs = m.groups()
        if persistent == 'varip':
            raise PineError(
                'varip 需要逐tick执行模式，当前仅支持收盘K线；请改用 var')
        names = [x.strip() for x in lhs.strip('[]').split(',')]
        if any(x.startswith('__') for x in names):
            raise PineError('标识符不能以双下划线开头')
        if op == ':=' and (persistent or len(names) > 1):
            raise PineError('重新赋值不能带var或元组')
        if op == ':=' and any(x not in scope for x in names):
            raise PineError('重新赋值的变量尚未定义')
        if (op == '='
                and any(x != '_'
                        and x in ctx.locals_by_scope[id(target)]
                        for x in names)):
            raise PineError('重复声明，请使用:=重新赋值')
        resolved = [
            scope[x] if op == ':='
            else x if target_is_root or x == '_'
            else f'local_{line_no}_{x}'
            for x in names]
        node = {
            'kind': 'assign', 'names': resolved,
            'input_name': names[0],
            'persistent': bool(persistent in ('var', 'const')),
            'expr': Parser(rhs).parse(), 'line': line_no}
        new_names = [(n, r) for n, r in zip(names, resolved) if n != '_']
        target.append(node)
        return node, new_names
    node = {'kind': 'expression', 'expr': Parser(text).parse(),
            'line': line_no}
    target.append(node)
    return node, []


def _register_names(ctx, scope, target, new_names):
    for name, r in new_names:
        scope[name] = r
        ctx.defined.add(r)
        ctx.locals_by_scope[id(target)].add(name)


def _check_blocks(block):
    for item in block:
        if item['kind'] == 'if':
            if (not item['body']
                    or item.get('else_seen') and not item['else']):
                raise PineError(f'第{item["line"]}行：条件块不能为空')
            _check_blocks(item['body'])
            _check_blocks(item['else'])
        if item['kind'] == 'for':
            if not item['body']:
                raise PineError(f'第{item["line"]}行：for循环体不能为空')
            _check_blocks(item['body'])
        if item['kind'] == 'while':
            if not item['body']:
                raise PineError(f'第{item["line"]}行：while循环体不能为空')
            _check_blocks(item['body'])
        if item['kind'] == 'switch':
            if not item['cases'] and not item['default']:
                raise PineError(f'第{item["line"]}行：switch至少需要一个分支')
            for c in item['cases']:
                if not c['body']:
                    raise PineError(f'第{item["line"]}行：switch分支不能为空')
                _check_blocks(c['body'])


def compile_source(source, *, library=False, search_dir=None):
    """Compile Pine source to an executable program dict.

    Returns dict with keys: nodes, functions, version, kind, sha256, source,
    input_names, types, imports.
    """
    if not isinstance(source, str) or len(source) > 64000:
        raise PineError('脚本最多64000字符')
    # Normalise tabs to 4 spaces (some v3 scripts use tabs)
    source = source.replace('\t', '    ')
    original_source = source
    source = re.sub(
        r'array\.new\s*<\s*(float|int|bool|string|color)\s*>',
        lambda m: 'array.new_' + m.group(1), source)
    source = re.sub(
        r'(?m)^(\s*)([A-Za-z_]\w*)\s*([+\-*/%])=\s*(.+)$',
        lambda m: (m.group(1) + m.group(2) + ' := ' + m.group(2)
                   + ' ' + m.group(3) + ' (' + m.group(4) + ')'),
        source)
    version = re.search(r'//@version\s*=\s*(\d+)', source)
    if version and version.group(1) not in ('3', '4', '5', '6'):
        raise PineError('仅支持Pine 3/4/5/6兼容子集（v3为尽力兼容）')

    source_lines = source.splitlines()
    skip_lines = set()

    # ── Extract UDT type blocks ──
    udt_types = {}
    from .typesystem import UDT
    i = 0
    while i < len(source_lines):
        line = source_lines[i]
        if line[:1].isspace():
            i += 1
            continue
        m = re.match(r'^type\s+([A-Za-z_]\w*)\s*$', line.strip())
        if not m:
            i += 1
            continue
        tname = m.group(1)
        if tname in udt_types:
            raise PineError('UDT重复定义：' + tname)
        fields = []
        j = i + 1
        while j < len(source_lines):
            fl = source_lines[j]
            if not fl.strip() or fl.lstrip().startswith('//'):
                j += 1
                continue
            if not fl[:1].isspace():
                break
            fm = re.match(
                r'^\s*(?:var\s+)?(?:float|int|integer|bool|string|color)\s+'
                r'([A-Za-z_]\w*)\s*$', fl)
            if not fm:
                raise PineError('UDT字段声明无效：' + fl.strip())
            fields.append((fm.group(1), 'float'))
            skip_lines.add(j)
            j += 1
        if not fields:
            raise PineError('UDT ' + tname + ' 至少需要一个字段')
        udt_types[tname] = UDT(tname, fields, i + 1)
        skip_lines.add(i)
        i = j

    # ── Extract user-defined functions ──
    function_sources = {}
    for index, line in enumerate(source_lines):
        if index in skip_lines or line[:1].isspace():
            continue
        match = re.match(
            r'^([A-Za-z_]\w*)\s*\(([^()]*)\)\s*=>\s*(.*?)\s*$', line)
        if not match:
            continue
        name, raw_params, body = match.groups()
        params = []
        default_nodes = {}
        for part in filter(None, (x.strip() for x in raw_params.split(','))):
            part = re.sub(
                r'^(?:(?:series|simple|input|const)\s+)*'
                r'(?:float|int|integer|bool|string|color|line|label|box|table|matrix|map)\s+',
                '', part)
            default_src = None
            dm = re.fullmatch(r'([A-Za-z_]\w*)\s*=\s*(.+?)\s*', part)
            if dm:
                part, default_src = dm.group(1), dm.group(2)
            if not re.fullmatch(r'[A-Za-z_]\w*', part):
                raise PineError('用户函数参数只支持必需的标量形参：' + name)
            if part in params:
                raise PineError('用户函数形参重复：' + name)
            if default_src:
                # Parameters with defaults must come after required ones.
                default_nodes[part] = Parser(default_src).parse()
            elif default_nodes:
                raise PineError('带默认值的形参必须放在最后：' + name)
            params.append(part)
        if name in function_sources:
            raise PineError('用户函数重复定义：' + name)
        if not body:
            j = index + 1
            while (j < len(source_lines)
                   and (not source_lines[j].strip()
                        or source_lines[j].lstrip().startswith('//'))):
                j += 1
            if j >= len(source_lines) or not source_lines[j][:1].isspace():
                raise PineError('用户函数缺少函数体：' + name)
            # Collect the whole indented block as multi-statement body.
            block_indent = len(source_lines[j]) - len(source_lines[j].lstrip())
            body_lines = []
            k = j
            while k < len(source_lines):
                bl = source_lines[k]
                if not bl.strip() or bl.lstrip().startswith('//'):
                    skip_lines.add(k)
                    k += 1
                    continue
                ind = len(bl) - len(bl.lstrip())
                if ind < block_indent:
                    break
                skip_lines.add(k)
                body_lines.append((k + 1, bl))
                k += 1
            # Merge wrapped (continuation) physical lines inside the function
            # body into logical lines, exactly as the top-level loop does, so
            # an assignment whose RHS wraps onto following lines stays one line.
            if body_lines:
                first_phys = body_lines[0][0]
                sub = '\n'.join(bl for _, bl in body_lines)
                body_lines = [
                    (first_phys + rel - 1, combined)
                    for rel, combined in logical_lines(sub)]
            function_sources[name] = dict(
                params=params, single_expr=None, body_lines=body_lines,
                body_indent=block_indent, line=index + 1,
                param_defaults=default_nodes)
        else:
            function_sources[name] = dict(
                params=params, single_expr=body, body_lines=None,
                line=index + 1, param_defaults=default_nodes)
        skip_lines.add(index)

    # ── Extract import statements ──
    imports = {}
    extra_allowed = set()
    for index, line in enumerate(source_lines):
        if index in skip_lines or line[:1].isspace():
            continue
        text = line.strip()
        m = re.match(
            r'^import\s+([A-Za-z_]\w*)\s*(?:as\s+([A-Za-z_]\w*))?$', text)
        if m:
            libname, alias = m.group(1), m.group(2)
            alias = alias or libname
            from . import importer
            mod = importer.load_module(libname, search_dir)
            imports[alias] = mod
            for fn in mod['functions']:
                extra_allowed.add(alias + '.' + fn)
            skip_lines.add(index)
            continue
        if re.match(r'^import\s*\{', text) or re.match(r'^import\b.*from\s', text):
            raise PineError(
                '不支持TradingView云端库导入（网络不可达）；'
                '仅支持本地 .pine 文件导入')

    if skip_lines:
        for index in skip_lines:
            source_lines[index] = ''
        source = '\n'.join(source_lines)

    user_functions = {name: {'params': spec['params']}
                      for name, spec in function_sources.items()}
    udt_type_names = set(udt_types)
    module_names = set(imports)

    # ── Parse top-level statements ──
    root = []
    base_scope = {}
    ctx = _Ctx(root, base_scope,
               set(BASES) | set(CONSTANTS),
               user_functions, udt_type_names, module_names, extra_allowed)

    for line_no, raw in logical_lines(source):
        _compile_line(ctx, line_no, raw, root)

    if not library and not ctx.declaration:
        raise PineError('缺少indicator/study/strategy声明')

    _check_blocks(root)

    # ── Compile user function bodies ──
    functions = {}
    for name, spec in function_sources.items():
        function_scope = dict(ctx.scopes[id(root)])
        for param in spec['params']:
            internal = f'pinefn_{name}_{param}'
            function_scope[param] = internal
            ctx.defined.add(internal)
        try:
            if spec['body_lines'] is None:
                expr = resolve_expression(
                    Parser(spec['single_expr']).parse(), function_scope,
                    user_functions, udt_type_names, module_names, extra_allowed)
                functions[name] = {'params': spec['params'], 'expr': expr,
                                   'line': spec['line'],
                                   'defaults': spec.get('param_defaults', {})}
            else:
                # Multi-statement body: compile into a block.
                froot = []
                fctx = _Ctx(froot, function_scope, ctx.defined,
                            user_functions, udt_type_names, module_names,
                            extra_allowed)
                # function locals share the global defined set
                fctx.defined = ctx.defined
                bindent = spec.get('body_indent', 0)
                for ln, bl in spec['body_lines']:
                    # strip the common function-body indentation so the first
                    # statement sits at column 0 relative to the function root.
                    dedented = bl[bindent:] if len(bl) >= bindent else bl.lstrip()
                    _compile_line(fctx, ln, dedented, froot)
                # Determine return value: last expression statement.
                ret = None
                for stmt in reversed(froot):
                    if stmt['kind'] == 'expression':
                        ret = stmt['expr']
                        break
                    if stmt['kind'] == 'assign':
                        ret = None
                        break
                if ret is None and not froot:
                    raise PineError('函数 ' + name + ' 体不能为空')
                fnvar_names = [n for s in froot
                               if s['kind'] == 'assign' and s.get('persistent')
                               for n in s['names']]
                functions[name] = {
                    'params': spec['params'], 'body': froot,
                    'return': ret, 'fnvar_names': fnvar_names,
                    'line': spec['line'],
                    'defaults': spec.get('param_defaults', {})}
        except (PineError, RecursionError, IndexError) as exc:
            raise PineError(f'函数 {name}：{exc}') from exc
        # Validate calls / names inside the function
        def walk_exprs(tree):
            if isinstance(tree, tuple):
                yield tree
                for x in tree[1:]:
                    yield from walk_exprs(x)
            elif isinstance(tree, list):
                for x in tree:
                    yield from walk_exprs(x)
            elif isinstance(tree, dict):
                for x in tree.values():
                    if isinstance(x, (tuple, list, dict)):
                        yield from walk_exprs(x)
        for x in walk_exprs(functions[name]):
            if isinstance(x, tuple) and x[0] == 'call':
                if (not is_supported_call(x[1])
                        and x[1] not in user_functions
                        and x[1] not in extra_allowed):
                    raise PineError('函数 ' + name
                                    + ' 调用了不支持函数 ' + x[1])
            elif isinstance(x, tuple) and x[0] == 'name':
                n = x[1]
                if (n not in ctx.defined
                        and not n.startswith((
                            'color.', 'plot.', 'shape.', 'location.', 'size.',
                            'display.', 'hline.', 'input.', 'syminfo.',
                            'timeframe.', 'barmerge.'))):
                    raise PineError('函数 ' + name
                                    + ' 引用了未定义标识符 ' + n)

    return {
        'nodes': root,
        'functions': functions,
        'types': udt_types,
        'imports': imports,
        'version': int(version.group(1)) if version else 4,
        'kind': ctx.declaration,
        'sha256': hashlib.sha256(original_source.encode()).hexdigest(),
        'source': original_source,
        'input_names': sorted(ctx.input_names),
    }
