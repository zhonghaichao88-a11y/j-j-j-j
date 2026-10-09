"""生成前端多语言文件 app/i18n.js。

英文：手工维护 app/i18n/en.tsv（中文<TAB>英文）。
繁体：用 OpenCC（s2twp，台湾用语）从同一批中文自动转换，另附单字对照表处理动态文字。

用法（在 video_chat 目录下）：
  pip install opencc-python-reimplemented
  python tools/build_i18n.py          # 会列出源码里还没翻译的文字
"""
import ast
import json
import re
from pathlib import Path

from opencc import OpenCC

ROOT = Path(__file__).resolve().parent.parent
CJK = re.compile(r"[一-鿿]")


def js_fragments():
    src = (ROOT / "app/app.js").read_text(encoding="utf-8")
    src = "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))
    src = re.sub(r"\s//\s[^\n]*", "", src)
    return {m.strip() for m in re.findall(r'[^"`\'<>{}$\n]*[一-鿿][^"`\'<>{}$\n]*', src) if len(m.strip()) < 150}


def py_fragments():
    out = set()
    for f in ["core.py", "api_account.py", "api_social.py", "api_chat.py", "api_game.py", "hub.py", "providers.py"]:
        tree = ast.parse((ROOT / "server" / f).read_text(encoding="utf-8"))
        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) and n.body
                and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs and CJK.search(n.value):
                out.update(p.strip() for p in re.split(r"\{[^}]*\}", n.value) if CJK.search(p))
            if isinstance(n, ast.JoinedStr):
                out.update(v.value.strip() for v in n.values if isinstance(v, ast.Constant) and CJK.search(v.value))
    return out


def main():
    en = {}
    for line in (ROOT / "app/i18n/en.tsv").read_text(encoding="utf-8").splitlines():
        if "\t" in line:
            zh, tr = line.split("\t", 1)
            en[zh] = tr
    frags = js_fragments() | py_fragments() | set(en)
    missing = sorted(f for f in frags if f not in en and not f.startswith("^"))
    cc = OpenCC("s2twp")
    tw = {f: cc.convert(f) for f in frags if cc.convert(f) != f}
    chars = sorted({c for f in frags for c in f if CJK.match(c)} | {c for c in (ROOT / "app/app.js").read_text(encoding="utf-8") if CJK.match(c)})
    s2t = OpenCC("s2t")
    charmap = {c: s2t.convert(c) for c in chars if s2t.convert(c) != c}
    out = ROOT / "app/i18n.js"
    out.write_text(
        "// 由 tools/build_i18n.py 生成，不要手改。英文改 app/i18n/en.tsv 后重新生成。\n"
        f"window.I18N_DATA = {json.dumps({'en': en, 'zh-TW': tw, 'chars': charmap}, ensure_ascii=False, separators=(',', ':'))};\n",
        encoding="utf-8")
    print(f"英文 {len(en)} 条，繁体 {len(tw)} 条，单字 {len(charmap)} 个 -> {out.relative_to(ROOT)}")
    if missing:
        print(f"还没有英文翻译的 {len(missing)} 条：")
        for m in missing:
            print("  ", m)


if __name__ == "__main__":
    main()
