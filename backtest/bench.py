import sys, time, pandas as pd, numpy as np
sys.path.insert(0, "..")
import alpha_fast_mode as fm

df = pd.read_csv("data/BTCUSDT_5m.csv")
def resample(df, rule, mins):
    g = df.set_index(pd.to_datetime(df.open_ms, unit="ms")).resample(rule, label="left", closed="left")
    o = g.agg(open=("open","first"), high=("high","max"), low=("low","min"), close=("close","last"), vol=("vol","sum")).dropna()
    o["ts_ms"] = (o.index.view(np.int64)//10**6)
    return o

r15 = resample(df, "15min", 15); r1h = resample(df, "1h", 60); r4h = resample(df, "4h", 240)
def fdict(sub, tscol="open_ms"):
    ts = (sub[tscol].to_numpy() if tscol in sub else sub["ts_ms"].to_numpy()).astype(float)
    return {"ts":ts,"open":sub.open.to_numpy(float),"high":sub.high.to_numpy(float),
            "low":sub.low.to_numpy(float),"close":sub.close.to_numpy(float),"volume":sub.vol.to_numpy(float)}

# warmup 4000
i=4000
cur_close_ms = int(df.open_ms.iloc[i])+5*60000
def htf(r, mins):
    k = int(np.searchsorted(r.ts_ms.to_numpy(), cur_close_ms - mins*60000, side="right"))
    return fdict(r.iloc[max(0,k-120):k])
f5=fdict(df.iloc[max(0,i-119):i+1])
data={"frames":{"5m":f5,"15m":htf(r15,15),"1h":htf(r1h,60),"4h":htf(r4h,240)},
      "ticker_last":float(df.close.iloc[i]),"spread_bps":-1.0,"missing":[],
      "summary":{"ready":True}}
t=time.time()
N=300
for k in range(N):
    fm.PULLBACK_STATE.clear()
    r=fm._build_decision("BTCUSDT", data)
dt=(time.time()-t)/N
print(f"per call {dt*1000:.2f} ms; signal={r['signal']} reason={r['reason'][:60]}")
print("estimated per symbol 57k bars:", dt*57000/60, "min")
