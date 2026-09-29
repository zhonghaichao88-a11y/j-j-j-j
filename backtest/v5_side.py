import copy
import lab_v5_small as V
p=copy.deepcopy(V.DEFAULT); p.update(w_tr=False,mr_max_ext1h=1.5,mr_max_4gap=0.5,mr_bbw_x=1.2,mr_rr=1.1)
t=V.run(p,0.0006,sleeves=["MR"],verbose=False)
for per in ("INS","OOS"):
  g=t[t.period==per]
  for side in ("LONG","SHORT"):
    x=g[g.side==side].net_r
    if len(x): print(per,side,"n",len(x),"win",round(100*(x>0).mean(),1),"PF",round(x[x>0].sum()/max(-x[x<0].sum(),1e-9),2),"avgR",round(x.mean(),3))
