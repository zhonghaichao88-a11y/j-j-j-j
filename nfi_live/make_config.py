"""生成 user_data/config.json：实盘、只做多、最多补 N 次、按成交额自动选币、自动排除股票 / 贵金属 / 稳定币合约"""
import json, os, re, secrets, sys
import pairs
from common import HERE, UD, okx_get, read_keys, read_proxy, read_settings

# 稳定币、法币：不做（欧易永续没有杠杆代币，不用排）
STATIC_BLACKLIST = [r"(USDC|USDE|FDUSD|TUSD|DAI|PYUSD|BUSD|USDP|USDD|USD1|RLUSD|EUR|EURC|GBP|AUD)/.*"]


def okx_non_crypto(proxy):
    """欧易永续里不是加密币的合约（股票、贵金属、指数等，instCategory ≠ 1）→ 加进黑名单"""
    data = okx_get("/api/v5/public/instruments", {"instType": "SWAP"}, proxy)
    out = sorted({x["instId"].split("-")[0] for x in data
                  if x.get("settleCcy") == "USDT" and str(x.get("instCategory", "1")) != "1"})
    if not data:
        raise SystemExit("读不到欧易合约列表，检查 代理.txt")
    return out


def short_keys():
    """策略里全部做空条件的名字（全部关掉 = 只做多）"""
    src = open(os.path.join(UD, "strategies", "NostalgiaForInfinityX7.py"), encoding="utf-8").read()
    blk = src[src.index("short_entry_signal_params = {"):]
    blk = blk[:blk.index("}")]
    keys = re.findall(r'"(short_entry_condition_\d+_enable)"', blk)
    if not keys:
        raise SystemExit("策略文件里找不到做空条件，策略文件可能被改过")
    return keys


def build(test=False):
    n_pairs, n_open, n_adj = read_settings()
    proxy = read_proxy()
    c = json.load(open(os.path.join(HERE, "config_template.json"), encoding="utf-8"))
    if test:                                   # 只给开发时检查流程用：不连账户、不下单
        c["dry_run"], c["dry_run_wallet"] = True, 100
    else:
        c["exchange"]["key"], c["exchange"]["secret"], c["exchange"]["password"] = read_keys()
        c["dry_run"] = False
    c["max_open_trades"] = n_open
    c["max_entry_position_adjustment"] = n_adj
    c["pairlists"][0]["number_assets"] = 200       # 上限；实际盯几个由选币文件里写几个决定（网页改了马上生效）
    # 选币文件用绝对路径（不依赖从哪个文件夹启动）；Windows 上是 file:///C:/…/user_data/pairs.json
    c["pairlists"][0]["pairlist_url"] = "file:///" + pairs.PATH.replace(os.sep, "/").lstrip("/")
    if os.name != "nt":
        c["pairlists"][0]["pairlist_url"] = "file:///" + pairs.PATH
    os.makedirs(UD, exist_ok=True)
    top, vol = pairs.write(proxy, n_pairs)          # 选币名单（之后运行中每 6 小时更新一次）
    c["short_entry_signal_params"] = {k: False for k in short_keys()}
    stocks = okx_non_crypto(proxy)
    official = pairs.blacklist()                    # NFI 官方欧易黑名单（包里自带一份）
    c["exchange"]["pair_blacklist"] = STATIC_BLACKLIST + official + [f"{b}/USDT:USDT" for b in stocks]
    if proxy:
        for k in ("ccxt_config", "ccxt_async_config"):
            c["exchange"][k]["httpsProxy"] = proxy
    out = os.path.join(UD, "config.json")
    old = json.load(open(out, encoding="utf-8")).get("api_server", {}) if os.path.exists(out) else {}
    c["api_server"]["jwt_secret_key"] = old.get("jwt_secret_key") or secrets.token_hex(16)
    c["api_server"]["password"] = old.get("password") or secrets.token_hex(4)
    os.makedirs(UD, exist_ok=True)
    json.dump(c, open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=True)   # 中文写成 \uXXXX：中文 Windows 上 freqtrade 按 GBK 读文件也不会出错
    print(f"设置：扫成交额前 {n_pairs} 个币（上市满 60 天）｜最多同时 {n_open} 单｜每单最多补 {n_adj} 次｜只做多｜逐仓 3 倍")
    print(f"选币：{len(top)} 个，成交额最大 {vol[0][1]}（{vol[0][0]/1e6:.0f} 百万U），最小 {vol[-1][1]}（{vol[-1][0]/1e6:.1f} 百万U）")
    print(f"排除了 {len(stocks)} 个股票 / 贵金属等非加密币合约、稳定币，以及 NFI 官方黑名单（{len(official)} 组）")
    print("下单：跟 NFI 官方设置一样，买单挂卖一价、卖单挂买一价（基本马上成交），买单 3 分钟 / 卖单 2 分钟没成交自动撤掉重来")
    print("代理:", proxy or "(没填，直连)")
    print("网页: http://127.0.0.1:8080   账号: nfi   密码:", c["api_server"]["password"])
    return c


if __name__ == "__main__":
    build(test="--test" in sys.argv)
