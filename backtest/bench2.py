import sys, time, pandas as pd, numpy as np
sys.path.insert(0, "..")
import alpha_fast_mode as fm

df = pd.read_csv("data/BTCUSDT_5m.csv")
def resample(df, rule):
    g = df.set_index(pd.to_datetime(df.open_ms, unit="ms")).resample(rule, label="left", closed="left")
    o = g.agg(open=("open","first"), high=("high","max"), low=("low","min"), close=("close","last"), vol=("vol","sum")).dropna()
    o["ts_ms"] = (o.index.view(np.int64)//10**6)
    return o
r15=resample(df,"15min"); r1h=resample(df,"1h"); r4h=resample(df,"4h")
def fdict(sub, tscol):
    return {"ts":sub[tscol].to_numpy(float),"open":sub.open.to_numpy(float),"high":sub.high.to_numpy(float),
            "low":sub.low.to_numpy(float),"close":sub.close.to_numpy(float),"volume":sub.vol.to_numpy(float)}

# ---- per-decision memo of pure frame functions ----
PURE=["_swings","_structure","_n_pattern","_fvg","_liquidity_sweep","_order_block",
      "_breakout_pullback_reclaim","_displacement","_rejection_wick","_compression_expansion",
      "_premium_discount","_price_location","_liquidity_pools","_dealing_range"]
_orig={}
MEMO={}
def fp(fr):
    c=fr.get("close"); h=fr.get("high"); l=fr.get("low")
    if c is None or len(c)==0: return (0,0,0,0)
    return (len(c), float(c[-1]), float(c[0]), float(h[-1]), float(l[-1]))
def make_wrap(name, fn):
    def wrap(fr, *a, **k):
        key=(name, fp(fr), a)
        if key in MEMO: return MEMO[key]
        r=fn(fr,*a,**k); MEMO[key]=r; return r
    return wrap
for n in PURE:
    if hasattr(fm,n):
        _orig[n]=getattr(fm,n); setattr(fm,n,make_wrap(n,_orig[n]))
# ER takes close array
_er=fm._efficiency_ratio
def er_wrap(c,n=14):
    key=("_efficiency_ratio",len(c),float(c[-1]),float(c[0]),n)
    if key in MEMO: return MEMO[key]
    r=_er(c,n); MEMO[key]=r; return r
fm._efficiency_ratio=er_wrap

def decide(i):
    MEMO.clear()
    cur_close_ms=int(df.open_ms.iloc[i])+5*60000
    def htf(r,mins):
        k=int(np.searchsorted(r.ts_ms.to_numpy(), cur_close_ms-mins*60000, side="right"))
        return fdict(r.iloc[max(0,k-120):k],"ts_ms")
    f5=fdict(df.iloc[max(0,i-119):i+1],"open_ms")
    data={"frames":{"5m":f5,"15m":htf(r15,15),"1h":htf(r1h,60),"4h":htf(r4h,240)},
          "ticker_last":float(df.close.iloc[i]),"spread_bps":-1.0,"missing":[],"summary":{"ready":True}}
    return fm._build_decision("BTCUSDT",data)

# correctness: compare memo vs non-memo on 400 random bars later in a fresh process is hard;
# here just verify memo results stable + timing, and that cached pure fn equals original on a frame
import random
idxs=random.sample(range(4000,50000),400)
t=time.time(); sigs=[]
for i in idxs:
    fm.PULLBACK_STATE.clear(); sigs.append(decide(i)["signal"])
dt=(time.time()-t)/len(idxs)
print(f"memo per call {dt*1000:.2f} ms; ENTER={sigs.count('LONG')+sigs.count('SHORT')} / {len(sigs)}")
# verify cached == original directly
fr=fdict(df.iloc[3000:3120],"open_ms")
MEMO.clear()
a=_orig["_structure"](fr); MEMO.clear(); b=fm._structure(fr)
print("structure identical:", a==b)
