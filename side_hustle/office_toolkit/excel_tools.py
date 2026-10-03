"""Excel/CSV 合并与拆分。

用法:
  python -m office_toolkit.excel_tools merge 输入文件夹 输出.xlsx [--add-source]
  python -m office_toolkit.excel_tools split 输入.xlsx 列名 输出文件夹 [--sheet 表名] [--one-file]
"""
import argparse
import re
from pathlib import Path

import pandas as pd

EXCEL_EXT = {".xlsx", ".xlsm", ".xls"}


def _read_any(path: Path) -> dict:
    """返回 {表名: DataFrame}；CSV 自动尝试 utf-8-sig / gbk 编码。"""
    if path.suffix.lower() == ".csv":
        for enc in ("utf-8-sig", "gbk", "gb18030"):
            try:
                return {"csv": pd.read_csv(path, encoding=enc)}
            except UnicodeDecodeError:
                continue
        raise ValueError(f"无法识别编码: {path}")
    return pd.read_excel(path, sheet_name=None)


def merge(folder, output, add_source=True, recursive=False):
    """把文件夹里所有 Excel/CSV 的所有工作表纵向合并成一张表。"""
    folder = Path(folder)
    pattern = "**/*" if recursive else "*"
    files = sorted(p for p in folder.glob(pattern)
                   if p.suffix.lower() in EXCEL_EXT | {".csv"} and not p.name.startswith("~$")
                   and p.resolve() != Path(output).resolve())
    if not files:
        raise FileNotFoundError(f"{folder} 下没有 Excel/CSV 文件")
    frames = []
    for f in files:
        for sheet, df in _read_any(f).items():
            if df.empty:
                continue
            if add_source:
                df = df.copy()
                df.insert(0, "来源文件", f.name)
                df.insert(1, "来源工作表", sheet)
            frames.append(df)
    result = pd.concat(frames, ignore_index=True, sort=False)
    result.to_excel(output, index=False)
    return len(files), len(result)


def _safe_name(value) -> str:
    name = re.sub(r'[\\/:*?"<>|\[\]]', "_", str(value)).strip() or "空值"
    return name[:31]  # Excel 工作表名最长 31 字符


def split(path, column, out_dir, sheet=0, one_file=False):
    """按某列的值把一张表拆成多个文件（或同一文件的多个工作表）。"""
    df = pd.read_excel(path, sheet_name=sheet) if Path(path).suffix.lower() != ".csv" \
        else _read_any(Path(path))["csv"]
    if column not in df.columns:
        raise KeyError(f"找不到列 '{column}'，现有列: {list(df.columns)}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    groups = list(df.groupby(df[column].fillna("空值"), sort=True))
    if one_file:
        target = out_dir / f"{Path(path).stem}_按{_safe_name(column)}拆分.xlsx"
        with pd.ExcelWriter(target) as w:
            used = set()
            for key, g in groups:
                name = _safe_name(key)
                base, i = name, 2
                while name in used:
                    name = f"{base[:28]}_{i}"
                    i += 1
                used.add(name)
                g.to_excel(w, sheet_name=name, index=False)
    else:
        for key, g in groups:
            g.to_excel(out_dir / f"{_safe_name(key)}.xlsx", index=False)
    return len(groups)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Excel/CSV 合并与拆分")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge", help="合并文件夹内所有表格")
    m.add_argument("folder")
    m.add_argument("output")
    m.add_argument("--no-source", action="store_true", help="不添加来源文件/工作表列")
    m.add_argument("-r", "--recursive", action="store_true", help="包含子文件夹")
    s = sub.add_parser("split", help="按列拆分")
    s.add_argument("path")
    s.add_argument("column")
    s.add_argument("out_dir")
    s.add_argument("--sheet", default=0)
    s.add_argument("--one-file", action="store_true", help="输出为一个文件的多个工作表")
    a = ap.parse_args(argv)
    if a.cmd == "merge":
        n_files, n_rows = merge(a.folder, a.output, not a.no_source, a.recursive)
        print(f"已合并 {n_files} 个文件，共 {n_rows} 行 -> {a.output}")
    else:
        n = split(a.path, a.column, a.out_dir, a.sheet, a.one_file)
        print(f"已按 '{a.column}' 拆分为 {n} 份 -> {a.out_dir}")


if __name__ == "__main__":
    main()
