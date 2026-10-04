"""TradingView 公开策略列表：热门列表 + 编辑精选 + 短线关键词搜索，只收 script_type=strategy"""
import re, json, time, subprocess, urllib.parse, os
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
def get(url):
    for k in range(3):
        r = subprocess.run(["curl", "-s", "-A", UA, "--max-time", "40", url], capture_output=True, text=True)
        if r.returncode == 0 and len(r.stdout) > 10000: return r.stdout
        time.sleep(3 * (k + 1))
    return ""
def items(h):
    out = []
    dec = json.JSONDecoder()
    for m in re.finditer(r'\{"id":\d+,"image_url"', h):
        try: o, _ = dec.raw_decode(h, m.start())
        except Exception: continue
        if o.get("script_type") != "strategy": continue
        s = o.get("symbol") or {}
        out.append(dict(id=o["id"], name=o.get("name"), likes=o.get("likes_count"), comments=o.get("comments_count"),
                        url=o.get("chart_url"), idpart=o.get("script_id_part"), access=o.get("script_access"),
                        sym=s.get("name"), stype=s.get("type"), interval=s.get("interval"),
                        created=o.get("created_at"), user=(o.get("user") or {}).get("username"),
                        desc=(o.get("description") or "")[:600]))
    return out
db = json.load(open("list.json")) if os.path.exists("list.json") else {}
def crawl(base, pages, tag):
    n0 = len(db)
    for p in range(1, pages + 1):
        u = base.format(p="" if p == 1 else f"page-{p}/")
        h = get(u); its = items(h)
        for it in its:
            it.setdefault("src", []); old = db.get(str(it["id"]), {})
            it["src"] = sorted(set(old.get("src", []) + [tag])); db[str(it["id"])] = {**old, **it}
        if not its: break
        time.sleep(1.5)
    json.dump(db, open("list.json", "w"), ensure_ascii=False)
    print(tag, "新增", len(db) - n0, "共", len(db), flush=True)
crawl("https://www.tradingview.com/scripts/{p}?script_type=strategies", 42, "popular")
crawl("https://www.tradingview.com/scripts/editors-picks/{p}?script_type=strategies", 42, "picks")
crawl("https://www.tradingview.com/scripts/{p}?script_type=strategies&sort=recent_extended", 42, "recent")
for kw in ["scalping", "scalp", "intraday", "day trading", "crypto", "bitcoin", "btc", "5 minute", "1 minute", "15 minute",
           "momentum", "breakout", "mean reversion", "vwap", "supertrend", "rsi", "ema cross", "bollinger", "order block", "liquidity"]:
    q = urllib.parse.quote(kw)
    crawl("https://www.tradingview.com/scripts/search/" + q + "/{p}?script_type=strategies", 42, "kw:" + kw)
