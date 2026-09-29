from alpha_alpha_research2 import source_audit, parameter_stability, CostModel, stressed_edge, select_meta_oof
import numpy as np

def main():
    rows=[{"funding_rate":.0001,"ofi":.2,"spread_bps":4,"close":100}]
    a=source_audit(rows); assert a["independent_sources"]==3
    st=parameter_stability([{"net_expectancy_bps":x} for x in [1,1.1,1.2,1.3,1.0,0.9]]); assert st["stable"]
    e=stressed_edge(20,2,CostModel(5,5,2,2),1.5); assert e["cost_bps"]>0 and e["net_bps"]<20
    rng=np.random.default_rng(7); X=rng.normal(size=(420,12)); y=np.tile([0,1,2],140)
    folds=[(np.arange(0,250),np.arange(250,330)),(np.arange(0,330),np.arange(330,420))]
    m,meta=select_meta_oof(X,y,folds,200); assert m is not None and meta["enabled"]
    print("ALPHA-X Research2 self-test: PASS")
if __name__=='__main__': main()
