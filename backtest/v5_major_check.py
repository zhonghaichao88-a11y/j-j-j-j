import copy, os
import lab_v5_small as V
V.DATA=os.path.join(V.BT,"data"); V._CACHE.clear()
MAJ=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","LINKUSDT"]
def ev(p,cost):
    t=V.run(p,cost,universe=MAJ,verbose=False);o={}
    for per in ("INS","OOS","ALL"):
        g=t if per=="ALL" else t[t.period==per]; x=g.net_r
        if len(g):o[per]=f"n{len(x)} w{round(100*(x>0).mean(),1)} PF{round(x[x>0].sum()/max(-x[x<0].sum(),1e-9),2)} avg{round(x.mean(),3)}"
    return o
# v4 基线（无v5闸门）
base=copy.deepcopy(V.DEFAULT); base.update(w_tr=False,mr_body=False,mr_max_ext1h=99,mr_max_4gap=99,mr_bbw_x=99)
# v5（加质量闸门+强势收回）
v5=copy.deepcopy(base); v5.update(mr_body=True,mr_max_ext1h=2.5,mr_max_4gap=0.8,mr_bbw_x=1.3)
for name,pp in (("v4-base",base),("v5-gated",v5)):
    for cost in (0.0012,0.0008,0.0006):
        print(name,cost,ev(pp,cost),flush=True)
