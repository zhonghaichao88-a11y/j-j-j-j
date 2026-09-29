"""ALPHA-X 10.0 retraining policy: emits jobs, never silently promotes models."""
from __future__ import annotations
import json,time,threading
from pathlib import Path
from typing import Any,Dict
ROOT=Path(__file__).parent; FILE=ROOT/'alpha_retrain_queue.json'; LOCK=threading.RLock()

def _load():
    if not FILE.exists(): return {'version':1,'jobs':[]}
    try:return json.loads(FILE.read_text(encoding='utf-8'))
    except Exception:return {'version':1,'jobs':[]}

def _save(x):
    tmp=FILE.with_suffix('.tmp');tmp.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(FILE)

def request(symbol:str,reasons:list[str],priority:str='normal')->dict:
    with LOCK:
        x=_load();
        active=next((j for j in x['jobs'] if j['symbol']==symbol and j['status'] in ('queued','running')),None)
        if active:return active
        j={'id':f'RT-{symbol.replace("/","_").replace(":","_")}-{int(time.time()*1000)}','symbol':symbol,'reasons':list(reasons),'priority':priority,'status':'queued','created_at':time.time()}
        x['jobs'].append(j);x['jobs']=x['jobs'][-200:];_save(x);return j

def pending():
    with LOCK:return [j for j in _load()['jobs'] if j['status'] in ('queued','running')]

def complete(job_id:str,status:str='finished',meta:Dict[str,Any]|None=None):
    with LOCK:
        x=_load()
        for j in x['jobs']:
            if j['id']==job_id:j['status']=status;j['finished_at']=time.time();j['meta']=meta or {};break
        _save(x)
    return next((j for j in x['jobs'] if j['id']==job_id),None)
