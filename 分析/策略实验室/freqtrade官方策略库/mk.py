import json, os, re, glob, shutil
SRC = "/home/user/freqtrade/freqtrade-strategies/user_data/strategies"
os.makedirs("strats", exist_ok=True)
items = []
for f in sorted(glob.glob(f"{SRC}/*.py") + glob.glob(f"{SRC}/berlinguyinca/*.py") + glob.glob(f"{SRC}/futures/*.py")):
    s = open(f, encoding="utf-8").read()
    cls = re.findall(r"^class\s+(\w+)\s*\(\s*(?:IStrategy|\w*Strategy\w*)\s*\)", s, re.M)
    tf = re.search(r"timeframe\s*=\s*['\"](\w+)['\"]", s)
    if not cls or not tf:
        continue
    shutil.copy(f, "strats/")
    items.append(dict(file=os.path.basename(f), cls=cls[-1], tf=tf.group(1), short="can_short = True" in s or "can_short: bool = True" in s,
                      folder=os.path.basename(os.path.dirname(f))))
json.dump(items, open("list.json", "w"), indent=1, ensure_ascii=False)
from collections import Counter
print(len(items), Counter(i["tf"] for i in items)); print([i["cls"] for i in items])
c = json.load(open("/home/user/ext/nfi/watchtest/cfg.json"))
for k in ("short_entry_signal_params", "long_entry_signal_params", "max_entry_position_adjustment"):
    c.pop(k, None)
c.update(stake_amount=100, dry_run_wallet=100000, max_open_trades=6, tradable_balance_ratio=0.99, fee=0.0015, margin_mode="isolated", trading_mode="futures")
c.pop("unfilledtimeout", None)
json.dump(c, open("cfg.json", "w"), indent=1)
