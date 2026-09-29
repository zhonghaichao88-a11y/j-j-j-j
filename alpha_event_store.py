"""Append-only event store with hash chaining, idempotency and deterministic replay ordering."""
from __future__ import annotations
import hashlib,json,os,time
class EventStore:
    def __init__(self,path): self.path=path
    def append(self,event_type,payload,event_id=None):
        rows=self._read(); eid=event_id or hashlib.sha256((event_type+json.dumps(payload,sort_keys=True)).encode()).hexdigest()[:24]
        if any(r['event_id']==eid for r in rows): return rows[-1] if rows else None
        prev=rows[-1]['hash'] if rows else '0'*64
        rec={'seq':len(rows)+1,'event_id':eid,'ts':time.time(),'type':event_type,'payload':payload,'prev_hash':prev}
        rec['hash']=hashlib.sha256(json.dumps(rec,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        os.makedirs(os.path.dirname(self.path) or '.',exist_ok=True)
        with open(self.path,'a',encoding='utf-8') as f:f.write(json.dumps(rec,separators=(',',':'))+'\n')
        return rec
    def _read(self):
        if not os.path.exists(self.path):return []
        with open(self.path,encoding='utf-8') as f:return [json.loads(x) for x in f if x.strip()]
    def verify(self):
        rows=self._read(); prev='0'*64
        for i,r in enumerate(rows,1):
            if r.get('seq')!=i or r.get('prev_hash')!=prev:return {'ok':False,'error':'chain'}
            h=r.get('hash'); c=dict(r); c.pop('hash',None)
            if hashlib.sha256(json.dumps(c,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=h:return {'ok':False,'error':'tamper'}
            prev=h
        return {'ok':True,'events':len(rows)}
