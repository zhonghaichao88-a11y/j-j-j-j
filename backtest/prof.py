import sys, cProfile, pstats, io, pandas as pd, numpy as np
sys.path.insert(0,"..")
import alpha_fast_mode as fm
df=pd.read_csv("data/BTCUSDT_5m.csv")
def resample(df,rule):
    g=df.set_index(pd.to_datetime(df.open_ms,unit="ms")).resample(rule,label="left",closed="left")
    o=g.agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),vol=("vol","sum")).dropna()
    o["ts_ms"]=o.index.view(np.int64)//10**6; return o
r15=resample(df,"15min");r1h=resample(df,"1h");r4h=resample(df,"4h")
def fd(sub,tc): return {"ts":sub[tc].to_numpy(float),"open":sub.open.to_numpy(float),"high":sub.high.to_numpy(float),"low":sub.low.to_numpy(float),"close":sub.close.to_numpy(float),"volume":sub.vol.to_numpy(float)}
def decide(i):
    cc=int(df.open_ms.iloc[i])+300000
    def htf(r,m):
        k=int(np.searchsorted(r.ts_ms.to_numpy(),cc-m*60000,side="right"));return fd(r.iloc[max(0,k-120):k],"ts_ms")
    data={"frames":{"5m":fd(df.iloc[i-119:i+1],"open_ms"),"15m":htf(r15,15),"1h":htf(r1h,60),"4h":htf(r4h,240)},"ticker_last":float(df.close.iloc[i]),"spread_bps":-1.0,"missing":[],"summary":{"ready":True}}
    return fm._build_decision("BTCUSDT",data)
pr=cProfile.Profile();pr.enable()
ent=0
for i in range(4000,4600):
    r=decide(i)
    if r["signal"] in ("LONG","SHORT"): ent+=1
pr.disable()
print("ENTER",ent,"/600 sequential")
s=io.StringIO();ps=pstats.Stats(pr,stream=s).sort_stats("cumulative");ps.print_stats(18);print(s.getvalue())
