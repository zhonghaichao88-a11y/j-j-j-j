"""给候选策略拉 Pine 源码（只有公开源码的能拉到）"""
import json, os, re, subprocess, time, sys, urllib.parse
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
SKIP = re.compile(r"template|example|how to|report|library|calculat|kelly|position siz|monthly return|backtest time|toolkit|"
                  r"tester|evaluator|analysis tool|screener|engine|framework|pnl|stats|plug|leverage and margin|buy ?& ?hold|martingale|grid|dca|"
                  r"prop firm|risk management|trailing strategy|stop loss and take profit|backtest terminal|educational|tutorial", re.I)
os.makedirs("src", exist_ok=True)
d = json.load(open("list.json"))
mins = {"1": 1, "2": 2, "3": 3, "5": 5, "10": 10, "15": 15, "30": 30, "45": 45, "60": 60}
cand = [x for x in d.values() if x.get("interval") in mins and not SKIP.search(x["name"] or "")]
cand.sort(key=lambda x: -(x["likes"] or 0))
N = int(sys.argv[1]) if len(sys.argv) > 1 else 300
got = 0
for x in cand[:N]:
    f = f"src/{x['id']}.json"
    if os.path.exists(f):
        continue
    u = "https://pine-facade.tradingview.com/pine-facade/get/" + urllib.parse.quote(x["idpart"], safe="") + "/last"
    r = subprocess.run(["curl", "-s", "-A", UA, "--max-time", "40", u], capture_output=True, text=True)
    try:
        j = json.loads(r.stdout)
    except Exception:
        j = {"error": r.stdout[:200]}
    j["_meta"] = x
    json.dump(j, open(f, "w"), ensure_ascii=False)
    got += 1
    time.sleep(1.2)
print("候选", len(cand), "本次拉", got)
