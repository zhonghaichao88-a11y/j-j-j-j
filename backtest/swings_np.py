import sys, time, pandas as pd, numpy as np
from numpy.lib.stride_tricks import sliding_window_view as swv
sys.path.insert(0,"..")
import alpha_fast_mode as fm

def swings_np(frame,left=2,right=2):
    h=np.asarray(frame.get("high",np.array([])),float); l=np.asarray(frame.get("low",np.array([])),float)
    n=len(h)
    if n<left+right+5: return [],[]
    w=left+right+1
    def piv(x,want):
        win=swv(x,w)                 # rows s cover [s,s+w)
        if want=="max":
            neigh=np.maximum(win[:,:left].max(1), win[:,left+1:].max(1))
            center=x[left:n-right]
            ok=center>neigh
        else:
            neigh=np.minimum(win[:,:left].min(1), win[:,left+1:].min(1))
            center=x[left:n-right]
            ok=center<neigh
        idxs=np.nonzero(ok)[0]+left
        vals=center[ok]
        out=[]; min_sep=max(left+right,3)
        for ii in range(len(idxs)):
            i=int(idxs[ii]); v=float(vals[ii])
            if not out or i-out[-1][0]>=min_sep: out.append((i,v))
            elif (want=="max" and v>out[-1][1]) or (want=="min" and v<out[-1][1]): out[-1]=(i,v)
        return out
    return piv(h,"max"), piv(l,"min")

# equivalence
rng=np.random.default_rng(1); mism=0;total=0
df=pd.read_csv("data/BTCUSDT_5m.csv")
for start in range(0,20000,37):
    for L in (60,90,120):
        sub=df.iloc[start:start+L]
        fr={"high":sub.high.to_numpy(float),"low":sub.low.to_numpy(float),"close":sub.close.to_numpy(float)}
        total+=1
        if fm._swings(fr)!=swings_np(fr): mism+=1;print("MM real",start,L)
for t in range(30000):
    n=int(rng.integers(5,130)); x=np.cumsum(rng.normal(0,1,n))+100
    if rng.random()<0.3:
        j=int(rng.integers(1,n-2)); x[j]=x[j-1]
    fr={"high":x+abs(rng.normal(0,.2,n)),"low":x-abs(rng.normal(0,.2,n)),"close":x}
    total+=1
    if fm._swings(fr)!=swings_np(fr):
        mism+=1
        if mism<4: print("MM rand",t,n)
print(f"total {total} mismatch {mism}")
# speed
import time
sub=df.iloc[4000:4120]; fr={"high":sub.high.to_numpy(float),"low":sub.low.to_numpy(float),"close":sub.close.to_numpy(float)}
for f,nm in ((fm._swings,"orig"),(swings_np,"np")):
    t=time.time()
    for _ in range(20000): f(fr)
    print(nm, f"{(time.time()-t)/20000*1e6:.1f} us/call")
