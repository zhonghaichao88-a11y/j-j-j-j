"""Pine v5 lexer — tokenization and logical-line extraction."""
from __future__ import annotations
import ast
from .constants import TOK, PineError


def tokenize(text):
    out = []
    pos = 0
    while pos < len(text):
        m = TOK.match(text, pos)
        if not m:
            if not text[pos:].strip():
                break
            raise PineError('无法识别表达式：' + text[pos:pos + 30])
        num, string, hexcolor, name, op = m.groups()
        out.append(
            ('num', float(num)) if num else
            ('str', ast.literal_eval(string)) if string else
            ('str', hexcolor) if hexcolor else
            ('id', name) if name else
            ('op', op)
        )
        pos = m.end()
    return out + [('end', '')]


# Operators that allow a line to break right before (next line begins with
# them) or right after (current line ends with them). Pine permits wrapping
# long expressions at binary/ternary operators.
_CONT_START_OPS = ('?', ':', '+', '-', '*', '/', '%', '>', '<', '.')
_CONT_END_OPS = ('+', '-', '*', '/', '%', '?', ',')
_CONT_START_WORDS = ('and ', 'or ')
_CONT_END_WORDS = (' and', ' or')


def _clean_line(raw):
    """Strip // comments outside quoted strings; return cleaned text."""
    clean = []
    quote = None
    escape = False
    depth = 0
    for i, ch in enumerate(raw):
        if not quote and raw[i:i + 2] == '//':
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
        elif not quote:
            if ch in '([':
                depth += 1
            elif ch in ')]':
                depth -= 1
    return ''.join(clean).rstrip(), depth


def _indent(raw):
    return len(raw) - len(raw.lstrip(' \t'))


def _ends_dangling(text):
    """True when a line ends with an operator that promises continuation."""
    s = text.rstrip()
    if s.endswith(_CONT_END_OPS) or s.endswith(_CONT_END_WORDS):
        return True
    # Assignment continuation: ':=' or a bare '=' whose preceding char is not
    # part of an arrow/comparison (=> == >= <= !=).
    if s.endswith(':='):
        return True
    if s.endswith('=') and not s.endswith(('==', '>=', '<=', '!=')):
        return True
    return False


def logical_lines(source):
    """Yield (start_line_number, combined_text) for each logical line.

    Handles line continuations via:
      * bracket depth (parentheses/brackets spanning lines);
      * a dangling operator/comma at the end of a line;
      * a continuation operator at the start of a more-indented next line
        (e.g. wrapped ternary ``?`` / ``:`` or binary operators).
    Comments outside quoted strings are stripped.
    """
    physical = []
    for number, raw in enumerate(source.splitlines(), 1):
        text, ddepth = _clean_line(raw)
        physical.append((number, raw, text, ddepth))

    i = 0
    n = len(physical)
    while i < n:
        start, base_raw, text, ddepth = physical[i]
        if ddepth < 0:
            raise PineError(f'第{start}行：括号不匹配')
        group = text
        depth = ddepth
        base_indent = _indent(base_raw)
        j = i
        while j + 1 < n:
            nxt_num, nxt_raw, nxt_text, nxt_ddepth = physical[j + 1]
            if not nxt_text.strip():
                # Blank line: absorb only while brackets stay open.
                if depth > 0:
                    j += 1
                    continue
                break
            stripped = nxt_text.strip()
            nxt_indent = _indent(nxt_raw)
            cont = False
            if depth > 0:
                cont = True
            elif _ends_dangling(group):
                cont = True
            elif nxt_indent > base_indent and (
                    stripped.startswith(_CONT_START_OPS) or
                    stripped.startswith(_CONT_START_WORDS)):
                cont = True
            if not cont:
                break
            group += ' ' + stripped
            depth += nxt_ddepth
            if depth < 0:
                raise PineError(f'第{nxt_num}行：括号不匹配')
            j += 1
        if depth != 0:
            raise PineError(f'第{start}行：括号未闭合')
        if group.strip():
            yield start, group
        i = j + 1
