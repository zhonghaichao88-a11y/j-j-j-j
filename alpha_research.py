"""ALPHA-X 3.0 research control plane: model registry, champion/challenger and drift gates.
No model is auto-promoted to live solely because of a backtest score.
"""
from __future__ import annotations
import json, time, hashlib
from pathlib import Path
from typing import Dict, Any, Optional
import numpy as np

ROOT=Path(__file__).parent
REGISTRY=ROOT/"alpha_model_registry.json"


def _read():
    if not REGISTRY.exists(): return {"version":1,"symbols":{}}
    try: return json.loads(REGISTRY.read_text(encoding="utf-8"))
    except Exception: return {"version":1,"symbols":{}}

def _write(x):
    tmp=REGISTRY.with_suffix('.tmp'); tmp.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(REGISTRY)

def fingerprint(meta: Dict[str,Any]) -> str:
    raw=json.dumps({k:meta.get(k) for k in ('version','symbol','timeframe','features','tp','sl','horizon','entry','data_bars')},sort_keys=True,ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def register(symbol:str, meta:Dict[str,Any], role:str='candidate'):
    r=_read(); s=r.setdefault('symbols',{}).setdefault(symbol,{"champion":None,"challengers":[],"history":[]})
    rec={"fingerprint":fingerprint(meta),"trained_at":meta.get('trained_at',time.time()),"grade":meta.get('grade'),'metrics':meta.get('metrics',{}),'role':role}
    s['history'].append(rec); s['history']=s['history'][-30:]
    if role=='candidate': s['challengers']=[x for x in s.get('challengers',[]) if x.get('fingerprint')!=rec['fingerprint']]; s['challengers'].append(rec); s['challengers']=s['challengers'][-5:]
    _write(r); return rec

def promote(symbol:str, fingerprint_id:str, min_grade=('S','A'), min_test_trades=30):
    r=_read(); s=r.get('symbols',{}).get(symbol); 
    if not s: return False,'symbol_not_registered'
    target=next((x for x in s.get('challengers',[]) if x.get('fingerprint')==fingerprint_id),None)
    if not target:return False,'challenger_not_found'
    m=target.get('metrics',{}); grade=target.get('grade')
    if grade not in min_grade or int(m.get('total_trades',0))<min_test_trades:return False,'promotion_gate_failed'
    old=s.get('champion');
    if old: old['role']='retired'
    target['role']='champion'; target['promoted_at']=time.time(); s['champion']=target; s['challengers']=[x for x in s.get('challengers',[]) if x.get('fingerprint')!=fingerprint_id]; _write(r); return True,'promoted'

def status(): return _read()

def drift(reference: np.ndarray, current: np.ndarray) -> Dict[str,float]:
    a=np.asarray(reference,dtype=float); b=np.asarray(current,dtype=float); a=a[np.isfinite(a)]; b=b[np.isfinite(b)]
    if len(a)<50 or len(b)<50:return {'score':0.0,'n_reference':len(a),'n_current':len(b)}
    mu=abs(np.mean(b)-np.mean(a))/(np.std(a)+1e-12); sd=abs(np.log((np.std(b)+1e-9)/(np.std(a)+1e-9)))
    return {'score':float(min(10,mu+sd)),'mean_shift':float(mu),'scale_shift':float(sd),'n_reference':len(a),'n_current':len(b)}

# --- 5.5 shadow-performance layer ---
def record_shadow(symbol:str, fingerprint_id:str, pnl:float, outcome:str):
    """Record out-of-sample shadow outcome. Shadow data is never mixed into training labels."""
    r=_read(); s=r.setdefault('symbols',{}).setdefault(symbol,{"champion":None,"challengers":[],"history":[]})
    for c in s.get('challengers',[]):
        if c.get('fingerprint')==fingerprint_id:
            sh=c.setdefault('shadow',{"trades":0,"wins":0,"pnl":0.0,"pnls":[]})
            sh['trades']+=1; sh['wins']+=1 if float(pnl)>0 else 0; sh['pnl']+=float(pnl); sh['pnls']=(sh.get('pnls',[])+[float(pnl)])[-500:]
            c['shadow_win_rate']=sh['wins']/max(sh['trades'],1)
            break
    _write(r)

def promotion_report(symbol:str, fingerprint_id:str)->Dict[str,Any]:
    r=_read(); s=r.get('symbols',{}).get(symbol,{})
    c=next((x for x in s.get('challengers',[]) if x.get('fingerprint')==fingerprint_id),None)
    if not c:return {"eligible":False,"reason":"challenger_not_found"}
    m=c.get('metrics',{}); sh=c.get('shadow',{}); trades=int(sh.get('trades',0)); pnls=np.asarray(sh.get('pnls',[]),dtype=float)
    shadow_pf=0.0
    if len(pnls):
        wins=pnls[pnls>0]; losses=pnls[pnls<0]; shadow_pf=float(wins.sum()/max(-losses.sum(),1e-12)) if len(losses) else (99.0 if len(wins) else 0.0)
    eligible=(c.get('grade') in ('S','A') and int(m.get('total_trades',0))>=30 and trades>=30 and shadow_pf>=1.05 and float(sh.get('pnl',0))>0)
    return {"eligible":eligible,"shadow_trades":trades,"shadow_profit_factor":shadow_pf,"shadow_pnl":float(sh.get('pnl',0)),"test_grade":c.get('grade'),"test_trades":int(m.get('total_trades',0)),"reason":"ready" if eligible else "shadow_or_test_gate_failed"}


def shadow_statistics(symbol:str, fingerprint_id:str)->dict:
    r=_read(); s=r.get('symbols',{}).get(symbol,{})
    c=next((x for x in s.get('challengers',[])+([s.get('champion')] if s.get('champion') else []) if x and x.get('fingerprint')==fingerprint_id),None)
    if not c:return {'trades':0,'pf':0.0,'mean':0.0,'sharpe':0.0}
    p=np.asarray(c.get('shadow',{}).get('pnls',[]),dtype=float); p=p[np.isfinite(p)]
    if len(p)<2:return {'trades':int(len(p)),'pf':0.0,'mean':float(p.mean()) if len(p) else 0.0,'sharpe':0.0}
    wins=p[p>0]; losses=p[p<0]; pf=float(wins.sum()/max(-losses.sum(),1e-12)) if len(losses) else 99.0
    return {'trades':int(len(p)),'pf':pf,'mean':float(p.mean()),'sharpe':float(p.mean()/(p.std(ddof=1)+1e-12)*np.sqrt(len(p)))}

def auto_retrain_required(symbol:str, fingerprint_id:str, max_age_days:float=7.0, drift_score:float=0.0)->dict:
    r=_read(); s=r.get('symbols',{}).get(symbol,{})
    target=s.get('champion') or next(iter(s.get('challengers',[])),None)
    if not target:return {'required':True,'reasons':['no_model']}
    age=(time.time()-float(target.get('trained_at',0)))/86400
    sh=shadow_statistics(symbol,fingerprint_id)
    reasons=[]
    if age>max_age_days: reasons.append('model_age')
    if drift_score>=2.5: reasons.append('feature_drift')
    if sh.get('trades',0)>=30 and (sh.get('pf',0)<1.0 or sh.get('mean',0)<0): reasons.append('shadow_decay')
    return {'required':bool(reasons),'reasons':reasons,'age_days':age,'shadow':sh}

# --- 7.5 governance extensions ---
def promotion_score(symbol:str, fingerprint_id:str)->dict:
    r=_read(); s=r.get('symbols',{}).get(symbol,{})
    items=s.get('challengers',[])+([s.get('champion')] if s.get('champion') else [])
    c=next((x for x in items if x and x.get('fingerprint')==fingerprint_id),None)
    if not c:return {'eligible':False,'reason':'model_not_found'}
    m=c.get('metrics',{}); sh=c.get('shadow',{}); pnls=np.asarray(sh.get('pnls',[]),dtype=float); pnls=pnls[np.isfinite(pnls)]
    n=len(pnls); mean=float(pnls.mean()) if n else 0.0; sd=float(pnls.std(ddof=1)) if n>1 else 0.0
    se=sd/max(n**0.5,1.0); z=mean/max(se,1e-12) if n>1 else 0.0
    return {'eligible':bool(c.get('grade') in ('S','A') and int(m.get('total_trades',0))>=30 and n>=50 and mean>0 and z>=1.64), 'trades':n,'mean_pnl':mean,'shadow_z':z,'grade':c.get('grade'),'test_trades':int(m.get('total_trades',0)),'fingerprint':fingerprint_id}
