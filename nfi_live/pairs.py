"""按欧易真实的 24 小时成交额（美元）选币，写到 user_data/pairs.json 给 freqtrade 读。
不用 freqtrade 自带的成交量排名：欧易K线的成交量是"张数"，便宜的币一张就是几百万个币，排出来全是土狗币。
只要：USDT 本位、加密币（排除股票 / 贵金属等）、正常交易中、上市满 60 天、不是稳定币 / 法币、不在 NFI 官方欧易黑名单里"""
import json, os, re, time
from common import HERE, UD, okx_get

STABLE = {"USDC", "USDE", "FDUSD", "TUSD", "DAI", "PYUSD", "BUSD", "USDP", "USDD", "USD1", "RLUSD", "EUR", "EURC", "GBP", "AUD"}
PATH = os.path.join(UD, "pairs.json")
MIN_DAYS = 60                                  # 和 NFI 官方选币一样：上市满 60 天（新币K线不够、波动乱）
BLACKLIST_FILE = os.path.join(HERE, "nfi_blacklist_okx.json")


def blacklist():
    """NFI 官方欧易黑名单（正则，跟 freqtrade 一样整串匹配、不分大小写）"""
    return json.load(open(BLACKLIST_FILE, encoding="utf-8"))["pair_blacklist"]


def banned(base, pats=None):
    pair = f"{base}/USDT:USDT"
    return any(re.fullmatch(p, pair, re.IGNORECASE) for p in (pats if pats is not None else blacklist()))


def rank(proxy, n):
    inst = okx_get("/api/v5/public/instruments", {"instType": "SWAP"}, proxy)
    tick = okx_get("/api/v5/market/tickers", {"instType": "SWAP"}, proxy)
    if not inst or not tick:
        raise RuntimeError("欧易没返回合约 / 行情数据")
    now = time.time() * 1000
    pats = blacklist()
    ok = {x["instId"]: x for x in inst
          if x.get("settleCcy") == "USDT" and x.get("ctType") == "linear" and x.get("state") == "live"
          and str(x.get("instCategory", "1")) == "1" and now - int(x.get("listTime") or now) >= MIN_DAYS * 86_400_000
          and x["instId"].split("-")[0] not in STABLE and not banned(x["instId"].split("-")[0], pats)}
    vol = []
    for t in tick:
        if t["instId"] in ok:
            try:
                vol.append((float(t["volCcy24h"]) * float(t["last"]), t["instId"].split("-")[0]))   # volCcy24h = 币数量
            except (TypeError, ValueError):
                pass
    vol.sort(reverse=True)
    return [f"{b}/USDT:USDT" for _, b in vol[:n]], vol[:n]


def write(proxy, n):
    pairs, vol = rank(proxy, n)
    if len(pairs) < min(10, n):
        raise RuntimeError(f"只选出 {len(pairs)} 个币，不对劲，保留上一次的名单")
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"pairs": pairs, "refresh_period": 3600, "updated": time.strftime("%Y-%m-%d %H:%M")}, f)
    os.replace(tmp, PATH)                       # 先写临时文件再替换，freqtrade 不会读到写了一半的文件
    return pairs, vol
