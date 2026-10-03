import json, time, urllib.request, numpy as np
def get(sym, p1, p2, iv="1m"):
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval={iv}&period1={p1}&period2={p2}"
    r = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read())["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    return r["timestamp"], q
for sym in ("NQ=F", "ES=F"):
    now = int(time.time()); rows = {}
    for k in range(5):
        p2 = now - k * 6 * 86400; p1 = p2 - 6 * 86400
        try:
            ts, q = get(sym, p1, p2)
        except Exception as e:
            print(sym, k, e); continue
        for i, t in enumerate(ts):
            if None not in (q["open"][i], q["high"][i], q["low"][i], q["close"][i]):
                rows[t] = (q["open"][i], q["high"][i], q["low"][i], q["close"][i])
    t = np.array(sorted(rows)); a = np.array([rows[x] for x in t])
    np.savez(f"{sym[:2]}_1m.npz", ts=t * 1000, open=a[:, 0], high=a[:, 1], low=a[:, 2], close=a[:, 3], volume=a[:, 0] * 0)
    print(sym, len(t), time.strftime("%Y-%m-%d", time.gmtime(t[0])), time.strftime("%Y-%m-%d", time.gmtime(t[-1])))
