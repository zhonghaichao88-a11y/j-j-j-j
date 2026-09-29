import sys, time, json
import lab_v5_small as V
import pandas as pd
t0=time.time()
def summ(t):
    out={}
    for per in ("INS","OOS","ALL"):
        g=t if per=="ALL" else t[t.period==per]
        if len(g):
            r=g.net_r; out[per]=dict(n=len(g),win=round(100*(r>0).mean(),1),avgR=round(r.mean(),3),
                PF=round(r[r>0].sum()/max(-r[r<0].sum(),1e-9),2),totR=round(r.sum(),0))
    return out
for sleeves in (["MR"],["TR"],["MR","TR"]):
    for cost in (0.0012,0.0008,0.0006):
        t=V.run(V.DEFAULT,cost,sleeves=sleeves,verbose=False)
        print(sleeves,cost,json.dumps(summ(t),ensure_ascii=False),flush=True)
print("elapsed",round(time.time()-t0,1),"s")
