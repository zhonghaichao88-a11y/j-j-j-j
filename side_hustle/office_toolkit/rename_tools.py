"""批量重命名：按 Excel 对照表、或按"前缀+序号"。默认只预览，加 --apply 才真改。

用法:
  python -m office_toolkit.rename_tools map 文件夹 对照表.xlsx [--old 原文件名 --new 新文件名] [--apply]
  python -m office_toolkit.rename_tools seq 文件夹 "报告_" [--start 1] [--width 3] [--ext .jpg] [--apply]
"""
import argparse
from pathlib import Path

import pandas as pd


def plan_map(folder, table, old_col="原文件名", new_col="新文件名"):
    df = pd.read_excel(table, dtype=str)
    folder = Path(folder)
    plan, problems = [], []
    for old, new in zip(df[old_col], df[new_col]):
        if pd.isna(old) or pd.isna(new):
            continue
        src = folder / old.strip()
        if not src.exists():
            problems.append(f"找不到: {old}")
            continue
        new = new.strip()
        if not Path(new).suffix:
            new += src.suffix  # 对照表没写扩展名时保留原扩展名
        plan.append((src, folder / new))
    return plan, problems


def plan_seq(folder, prefix, start=1, width=3, ext=None):
    files = sorted(p for p in Path(folder).iterdir() if p.is_file()
                   and (not ext or p.suffix.lower() == ext.lower()))
    return [(p, p.with_name(f"{prefix}{i:0{width}d}{p.suffix}"))
            for i, p in enumerate(files, start)], []


def check(plan):
    targets = [d for _, d in plan]
    sources = {s for s, _ in plan}
    problems = [f"重名: {d.name}" for d in set(targets) if targets.count(d) > 1]
    problems += [f"目标已存在: {d.name}" for d in targets if d.exists() and d not in sources]
    return problems


def apply(plan):
    # 两步改名：先改成临时名，避免 a->b、b->a 这种互换冲突
    temps = []
    for i, (src, dst) in enumerate(plan):
        tmp = src.with_name(f".__rename_tmp_{i}__{src.suffix}")
        src.rename(tmp)
        temps.append((tmp, dst))
    for tmp, dst in temps:
        tmp.rename(dst)
    log = plan[0][0].parent / "重命名记录.csv" if plan else None
    if log:
        pd.DataFrame([(s.name, d.name) for s, d in plan], columns=["原名", "新名"]) \
            .to_csv(log, index=False, encoding="utf-8-sig")
    return len(plan)


def main(argv=None):
    ap = argparse.ArgumentParser(description="批量重命名（默认仅预览）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("map"); m.add_argument("folder"); m.add_argument("table")
    m.add_argument("--old", default="原文件名"); m.add_argument("--new", default="新文件名")
    s = sub.add_parser("seq"); s.add_argument("folder"); s.add_argument("prefix")
    s.add_argument("--start", type=int, default=1); s.add_argument("--width", type=int, default=3)
    s.add_argument("--ext")
    for p in (m, s):
        p.add_argument("--apply", action="store_true", help="真正执行（否则只预览）")
    a = ap.parse_args(argv)
    if a.cmd == "map":
        plan, problems = plan_map(a.folder, a.table, a.old, a.new)
    else:
        plan, problems = plan_seq(a.folder, a.prefix, a.start, a.width, a.ext)
    problems += check(plan)
    for src, dst in plan:
        print(f"{src.name}  ->  {dst.name}")
    for p in problems:
        print("⚠", p)
    if not a.apply:
        print(f"\n预览完成，共 {len(plan)} 个。确认无误后加 --apply 执行。")
    elif any(p.startswith(("重名", "目标已存在")) for p in problems):
        print("存在冲突，未执行。")
    else:
        print(f"已重命名 {apply(plan)} 个文件，记录保存在 重命名记录.csv")


if __name__ == "__main__":
    main()
