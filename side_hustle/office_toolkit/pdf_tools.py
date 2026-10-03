"""PDF 合并 / 拆分 / 提取页面 / 提取文字。

用法:
  python -m office_toolkit.pdf_tools merge 输出.pdf a.pdf b.pdf ...   (或传一个文件夹)
  python -m office_toolkit.pdf_tools split 输入.pdf 输出文件夹           (每页一个文件)
  python -m office_toolkit.pdf_tools pages 输入.pdf 输出.pdf "1-3,5,8-"
  python -m office_toolkit.pdf_tools text  输入.pdf 输出.txt
"""
import argparse
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def _expand_inputs(inputs):
    files = []
    for item in inputs:
        p = Path(item)
        files.extend(sorted(p.glob("*.pdf")) if p.is_dir() else [p])
    return files


def merge(inputs, output):
    files = _expand_inputs(inputs)
    writer = PdfWriter()
    for f in files:
        writer.append(str(f))
    with open(output, "wb") as fh:
        writer.write(fh)
    return len(files)


def split(path, out_dir):
    reader = PdfReader(str(path))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    width = len(str(len(reader.pages)))
    for i, page in enumerate(reader.pages, 1):
        w = PdfWriter()
        w.add_page(page)
        with open(out_dir / f"{Path(path).stem}_第{i:0{width}d}页.pdf", "wb") as fh:
            w.write(fh)
    return len(reader.pages)


def parse_ranges(spec, total):
    """'1-3,5,8-' -> [0,1,2,4,7,...]（0 基索引，保持书写顺序）。"""
    result = []
    for part in spec.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start = int(a) if a else 1
            end = int(b) if b else total
        else:
            start = end = int(part)
        if not (1 <= start <= end <= total):
            raise ValueError(f"页码范围 '{part}' 超出 1-{total}")
        result.extend(range(start - 1, end))
    return result


def pages(path, output, spec):
    reader = PdfReader(str(path))
    idx = parse_ranges(spec, len(reader.pages))
    w = PdfWriter()
    for i in idx:
        w.add_page(reader.pages[i])
    with open(output, "wb") as fh:
        w.write(fh)
    return len(idx)


def text(path, output):
    reader = PdfReader(str(path))
    chunks = [f"===== 第 {i} 页 =====\n{p.extract_text() or ''}"
              for i, p in enumerate(reader.pages, 1)]
    Path(output).write_text("\n\n".join(chunks), encoding="utf-8")
    return len(reader.pages)


def main(argv=None):
    ap = argparse.ArgumentParser(description="PDF 批处理")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge"); m.add_argument("output"); m.add_argument("inputs", nargs="+")
    s = sub.add_parser("split"); s.add_argument("path"); s.add_argument("out_dir")
    p = sub.add_parser("pages"); p.add_argument("path"); p.add_argument("output"); p.add_argument("spec")
    t = sub.add_parser("text"); t.add_argument("path"); t.add_argument("output")
    a = ap.parse_args(argv)
    if a.cmd == "merge":
        print(f"已合并 {merge(a.inputs, a.output)} 个 PDF -> {a.output}")
    elif a.cmd == "split":
        print(f"已拆分为 {split(a.path, a.out_dir)} 页 -> {a.out_dir}")
    elif a.cmd == "pages":
        print(f"已提取 {pages(a.path, a.output, a.spec)} 页 -> {a.output}")
    else:
        print(f"已提取 {text(a.path, a.output)} 页文字 -> {a.output}（扫描件需 OCR，本工具不含）")


if __name__ == "__main__":
    main()
