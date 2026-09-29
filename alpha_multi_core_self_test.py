"""Network-free tests for the Multi-Alpha Research Core."""
import numpy as np
from alpha_multi_core import factor_vector, meta_input, fit_meta, meta_proba, blend_proba

def run():
    rng=np.random.default_rng(2026); n=900
    rows=[]; y=[]
    for i in range(n):
        rows.append({
            "trend_score": float(rng.normal(0,.015)), "ret10": float(rng.normal(0,.02)),
            "ret20": float(rng.normal(0,.03)), "rsi14": float(np.clip(.5+rng.normal(0,.12),.05,.95)),
            "dist_ema20": float(rng.normal(0,.018)), "vol_regime": float(np.exp(rng.normal(0,.25))),
            "vol_ratio": float(np.exp(rng.normal(0,.25))), "body_pct": float(rng.normal(0,.008)),
            "obv_slope": float(rng.normal(0,.04)), "trend_strength": float(abs(rng.normal(0,1.5))),
        })
        y.append(2 if rows[-1]["trend_score"]+rows[-1]["ret10"]>.01 else (0 if rows[-1]["trend_score"]+rows[-1]["ret10"]<-.01 else 1))
    p=np.full((n,3),1/3,dtype=float)
    # Give the base model a weak directional signal so the meta layer has a real input.
    for i,r in enumerate(rows):
        z=float(np.clip((r["trend_score"]+r["ret10"])/.025,-2,2)); p[i,2]=.33+.16*max(z,0); p[i,0]=.33+.16*max(-z,0); p[i,1]=1-p[i,0]-p[i,2]
    X=np.vstack([meta_input(r,p[i]) for i,r in enumerate(rows)])
    assert X.shape[1] == 20 and np.isfinite(X).all()
    m=fit_meta(X[:650],np.asarray(y[:650])); assert m is not None
    mp=meta_proba(m,X[650:]); assert mp.shape==(250,3) and np.allclose(mp.sum(axis=1),1,atol=1e-6)
    bp=p[650:]; z=blend_proba(bp,mp,.65); assert z.shape==mp.shape and np.allclose(z.sum(axis=1),1,atol=1e-6)
    assert len(factor_vector(rows[0],p[0]))==15
    print("ALPHA-X MULTI-ALPHA CORE SELF-TEST: PASS")

if __name__ == "__main__": run()
