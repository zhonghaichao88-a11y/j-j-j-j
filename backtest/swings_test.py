import sys, pandas as pd, numpy as np
sys.path.insert(0,"..")
import alpha_fast_mode as fm

def swings_fast(frame,left=2,right=2):
    h=frame.get("high",np.array([])); l=frame.get("low",np.array([]))
    n=len(h)
    if n<left+right+5: return [],[]
    sh=pd.Series(h); sl=pd.Series(l)
    def neigh(s,how):
        roll=pd.Series.rolling if False else None
        if left>0:
            lm=(s.rolling(left).max() if how=="max" else s.rolling(left).min()).shift(1)
        else:
            lm=pd.Series(np.full(n,np.nan))
        if right>0:
            rm=(s.rolling(right).max() if how=="max" else s.rolling(right).min()).shift(-right)
        else:
            rm=pd.Series(np.full(n,np.nan))
        f=np.fmax if how=="max" else np.fmin
        return f(lm.to_numpy(),rm.to_numpy())
    nh=neigh(sh,"max"); nl=neigh(sl,"min")
    highs=[];lows=[];min_sep=max(left+right,3)
    for i in range(left,n-right):
        hi=float(h[i]);lo=float(l[i])
        if hi>float(nh[i]):
            if not highs or i-highs[-1][0]>=min_sep: highs.append((i,hi))
            elif hi>highs[-1][1]: highs[-1]=(i,hi)
        if lo<float(nl[i]):
            if not lows or i-lows[-1][0]>=min_sep: lows.append((i,lo))
            elif lo<lows[-1][1]: lows[-1]=(i,lo)
    return highs,lows

# verify on real data windows + random frames
rng=np.random.default_rng(0); mism=0;total=0
df=pd.read_csv("data/BTCUSDT_5m.csv")
for start in range(0,20000,37):
    for L in (60,90,120):
        sub=df.iloc[start:start+L]
        fr={"high":sub.high.to_numpy(float),"low":sub.low.to_numpy(float),"close":sub.close.to_numpy(float)}
        a=fm._swings(fr); b=swings_fast(fr); total+=1
        if a!=b: mism+=1; print("MISMATCH real",start,L,a,b); break
# random walks incl plateaus
for t in range(20000):
    n=int(rng.integers(5,130)); x=np.cumsum(rng.normal(0,1,n))+100
    # inject equal plateaus
    if rng.random()<0.3:
        j=int(rng.integers(1,n-2)); x[j]=x[j-1]
    fr={"high":x+abs(rng.normal(0,.2,n)),"low":x-abs(rng.normal(0,.2,n)),"close":x}
    a=fm._swings(fr);b=swings_fast(fr);total+=1
    if a!=b:
        mism+=1
        if mism<5: print("MISMATCH rand",t,n,a,b)
print(f"total {total} mismatch {mism}")
