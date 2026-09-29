"""Transactional-outbox style intent queue for reliable downstream execution adapters."""
from __future__ import annotations
import json,os,time,hashlib
class Outbox:
    def __init__(self,path):self.path=path
    def put(self,topic,payload,key):
        rows=self._read()
        if any(x['key']==key for x in rows): return False
        row={'key':key,'topic':topic,'payload':payload,'created_at':time.time(),'attempts':0,'status':'PENDING'}
        with open(self.path,'a',encoding='utf-8') as f:f.write(json.dumps(row,separators=(',',':'))+'\n')
        return True
    def _read(self):
        if not os.path.exists(self.path):return []
        with open(self.path,encoding='utf-8') as f:return [json.loads(x) for x in f if x.strip()]
    def pending(self):return [x for x in self._read() if x.get('status')=='PENDING']
    @staticmethod
    def key(intent):return hashlib.sha256(json.dumps(intent,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:32]
