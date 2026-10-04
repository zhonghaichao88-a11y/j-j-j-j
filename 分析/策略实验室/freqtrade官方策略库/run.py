import json, os, subprocess, sys, zipfile, glob
from concurrent.futures import ThreadPoolExecutor
items = [i for i in json.load(open("list.json")) if i["tf"] != "1m" and i["cls"] != "DoesNothingStrategy"]
PER = {"A": "20241001-20250930", "B": "20251001-20260929"}
os.makedirs("out", exist_ok=True)
env = dict(os.environ, SSL_CERT_FILE="/root/.ccr/ca-bundle.crt")


def job(arg):
    it, p = arg
    tag = f"{it['cls']}_{p}"
    if os.path.exists(f"out/{tag}.json"):
        return tag, "cached"
    d = f"out/{tag}"; os.makedirs(d, exist_ok=True)
    cmd = ["nice", "/home/user/ext/ftvenv/bin/freqtrade", "backtesting", "-c", "cfg.json", "--userdir", "/home/user/ext/ft",
           "--strategy-path", "strats", "--strategy", it["cls"], "--timerange", PER[p], "--export", "trades", "--backtest-directory", d]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)
    except subprocess.TimeoutExpired:
        json.dump({"error": ["超过10分钟，跳过"]}, open(f"out/{tag}.json", "w"), ensure_ascii=False); return tag, "TIMEOUT"
    zs = glob.glob(f"{d}/*.zip")
    if not zs:
        err = (r.stdout + r.stderr).strip().splitlines()[-3:]
        json.dump({"error": err}, open(f"out/{tag}.json", "w"), ensure_ascii=False); return tag, "ERR " + " | ".join(err)[:200]
    z = zipfile.ZipFile(zs[0]); n = [x for x in z.namelist() if x.endswith(".json") and "config" not in x and "meta" not in x][0]
    st = list(json.loads(z.read(n))["strategy"].values())[0]
    keep = {k: st.get(k) for k in ("total_trades", "profit_total", "profit_total_abs", "wins", "losses", "max_drawdown_account", "profit_factor",
                                   "holding_avg", "trades_per_day")}
    keep["long"] = sum(1 for t in st["trades"] if not t["is_short"]); keep["short"] = sum(1 for t in st["trades"] if t["is_short"])
    json.dump(keep, open(f"out/{tag}.json", "w")); return tag, "ok"


if __name__ == "__main__":
    jobs = [(it, p) for it in items for p in PER]
    with ThreadPoolExecutor(4) as ex:
        for tag, res in ex.map(job, jobs):
            print(tag, res, flush=True)
