"""管理命令（在 side_hustle 目录下运行）。

  python -m web.admin gen 10 --credits 50 --note "闲鱼 9.9 元 50 次"   # 生成 10 个兑换码
  python -m web.admin check ABCD-EFGH-JKLM                            # 查兑换码剩余次数
  python -m web.admin stats                                           # 使用统计
"""
import argparse
import json

from . import billing


def main(argv=None):
    ap = argparse.ArgumentParser(description="办公工具网站管理")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen", help="生成兑换码")
    g.add_argument("n", type=int)
    g.add_argument("--credits", type=int, default=50)
    g.add_argument("--note", default="")
    c = sub.add_parser("check", help="查兑换码")
    c.add_argument("code")
    sub.add_parser("stats", help="使用统计")
    a = ap.parse_args(argv)
    if a.cmd == "gen":
        for code in billing.new_codes(a.n, a.credits, a.note):
            print(code)
    elif a.cmd == "check":
        left = billing.code_credits(a.code)
        print("兑换码不存在" if left is None else f"剩余 {left} 次")
    else:
        print(json.dumps(billing.stats(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
