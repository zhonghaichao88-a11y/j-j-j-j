"""启动前检查欧易账户：密钥能不能用、账户模式、余额、有没有别的仓位。第一次实盘要手动输入「确认实盘」"""
import os, sys
import ccxt
from common import UD, read_keys, read_proxy

CONFIRM = os.path.join(UD, "已确认实盘.txt")


def main():
    key, sec, pw = read_keys()
    proxy = read_proxy()
    ex = ccxt.okx({"apiKey": key, "secret": sec, "password": pw, "enableRateLimit": True,
                   **({"httpsProxy": proxy} if proxy else {})})
    try:
        cfg = ex.privateGetAccountConfig()["data"][0]
        bal = ex.fetch_balance()
        pos = [p for p in ex.fetch_positions() if float(p.get("contracts") or 0) != 0]
    except ccxt.AuthenticationError as e:
        raise SystemExit(f"密钥不对或没有权限：{e}\n检查「欧易子账户密钥.txt」三行是否填对，API 是否开了「交易」权限")
    except ccxt.NetworkError as e:
        raise SystemExit(f"连不上欧易：{e}\n检查 代理.txt 里的代理地址，以及代理软件是否开着")
    except ccxt.ExchangeError as e:
        txt = str(e)
        hint = "检查「欧易子账户密钥.txt」三行是否填对（API Key、Secret Key、创建 API 时设的密码）"
        if "50119" in txt or "doesn't exist" in txt:
            hint = "API Key 不存在：可能填错了。要用实盘子账户里创建的 API"
        elif "50113" in txt or "50111" in txt or "Invalid Sign" in txt:
            hint = "Secret Key 不对"
        elif "50105" in txt or "assphrase" in txt:
            hint = "PASSPHRASE（创建 API 时设的密码）不对"
        elif "50110" in txt or "IP" in txt:
            hint = "API 绑定了 IP 白名单，现在这台电脑的 IP 不在里面"
        raise SystemExit(f"欧易拒绝了：{txt[-160:]}\n{hint}")
    lv = str(cfg.get("acctLv"))
    names = {"1": "简单模式", "2": "单币种保证金", "3": "跨币种保证金", "4": "组合保证金"}
    usdt = bal.get("USDT", {}) or {}
    total, free = float(usdt.get("total") or 0), float(usdt.get("free") or 0)
    print(f"欧易账户：UID {cfg.get('uid')}｜账户模式 {names.get(lv, lv)}｜持仓模式 {'单向' if cfg.get('posMode') == 'net_mode' else '双向'}")
    print(f"USDT：总共 {total:.2f}｜可用 {free:.2f}")
    if lv == "1":
        raise SystemExit("账户是「简单模式」，做不了合约。到欧易 App → 交易设置 → 账户模式，改成「单币种保证金」再启动")
    if total < 30:
        raise SystemExit(f"账户只有 {total:.2f} USDT，太少了（至少 30U，建议 100U 以上），先往这个子账户转钱")
    db = os.path.join(UD, "tradesv3.sqlite")
    if pos and not os.path.exists(db):
        print("⚠ 这个账户里已经有别的仓位：" + "、".join(f"{p['symbol']} {p['contracts']}张" for p in pos))
        print("  NFI 会和它们共用保证金，建议用一个干净的子账户。要继续就照下面提示输入。")
    uid_line = f"UID {cfg.get('uid')}"
    done = os.path.exists(CONFIRM) and open(CONFIRM, encoding="utf-8").read().strip() == uid_line
    if not done:                               # 第一次，或者换了账户：都要重新确认
        print()
        print("=" * 60)
        print("第一次启动实盘：程序会用这个账户的钱自动下单（只做多，逐仓 3 倍）。")
        print("确认没问题，输入「确认实盘」四个字后回车；输入别的就退出。")
        print("=" * 60)
        if input("> ").strip() != "确认实盘":
            raise SystemExit("没有确认，已退出，没有下任何单")
        os.makedirs(UD, exist_ok=True)
        open(CONFIRM, "w", encoding="utf-8").write(uid_line + "\n")
    print("账户检查通过")


if __name__ == "__main__":
    main()
