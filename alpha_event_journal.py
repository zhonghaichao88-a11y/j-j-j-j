"""Append-only event journal for institutional audit/replay."""
from __future__ import annotations
import json,time,hashlib
from pathlib import Path
class EventJournal:
    def __init__(self,path): self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
    def append(self,event_type,payload):
        prev=''
        if self.path.exists():
            try: prev=self.path.read_text(encoding='utf-8').splitlines()[-1]
            except Exception: pass
        rec={'ts':time.time(),'type':str(event_type),'payload':payload,'prev_hash':hashlib.sha256(prev.encode()).hexdigest() if prev else ''}
        rec['hash']=hashlib.sha256(json.dumps(rec,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        with self.path.open('a',encoding='utf-8') as f:f.write(json.dumps(rec,ensure_ascii=False,separators=(',',':'))+'\n')
        return rec
    def verify(self):
        if not self.path.exists(): return {'ok':True,'events':0}
        prev=''; n=0
        for line in self.path.read_text(encoding='utf-8').splitlines():
            if not line: continue
            r=json.loads(line); h=r.pop('hash'); expected=hashlib.sha256(json.dumps(r,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if h!=expected or r.get('prev_hash')!=(hashlib.sha256(prev.encode()).hexdigest() if prev else ''): return {'ok':False,'events':n}
            prev=line; n+=1
        return {'ok':True,'events':n}
