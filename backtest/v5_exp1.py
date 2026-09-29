import copy, itertools
import lab_v5_small as V
def ev(p,sleeves,cost):
    t=V.run(p,cost,sleeves=sleeves,verbose=False); o={}
    for per in ("INS","OOS"):
        g=t[t.period==per]; x=g.net_r
        if len(g): o[per]=dict(n=len(g),win=round(100*(x>0).mean(),1),pf=round(x[x>0].sum()/max(-x[x<0].sum(),1e-9),2),avgR=round(x.mean(),3))
    return o
base=copy.deepcopy(V.DEFAULT); base.update(w_tr=False,mr_max_ext1h=1.5,mr_max_4gap=0.5,mr_bbw_x=1.2)
print("=== MR 5m：目标模式 × 兜底RR × 成本（严格区间否决档）===")
for tgt in ("vwap","mid","band"):
    for rr in (1.1,1.3,1.6):
        p=copy.deepcopy(base); p.update(mr_target=tgt,mr_rr=rr)
        line=f"target={tgt:4s} rr={rr}: "
        for cost in (0.0006,0.0004):
            o=ev(p,["MR"],cost); line+=f"[{cost*100:.2f}%] INS {o['INS']} OOS {o['OOS']}  "
        print(line,flush=True)
