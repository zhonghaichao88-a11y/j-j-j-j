import json,base64,urllib.request,urllib.parse,sys,collections
c=json.load(open('user_data/config.json'))['api_server']
auth="Basic "+base64.b64encode(f"{c['username']}:{c['password']}".encode()).decode()
def get(p):
    r=urllib.request.Request("http://127.0.0.1:8080/api/v1/"+p,headers={"Authorization":auth})
    return json.loads(urllib.request.urlopen(r,timeout=60).read())
wl=get("whitelist")["whitelist"]
out={}; nan=collections.Counter(); lens=[]
for p in wl:
    d=get("pair_candles?"+urllib.parse.urlencode({"pair":p,"timeframe":"5m","limit":1000}))
    cols=d["columns"]; rows=d["data"]
    lens.append(len(rows))
    ie=cols.index("enter_long") if "enter_long" in cols else None
    it=cols.index("enter_tag") if "enter_tag" in cols else None
    idt=cols.index("date")
    sig=[(r[idt],r[it]) for r in rows if ie is not None and r[ie]==1]
    out[p]=sig
    # key indicators NaN on last row
    last=rows[-1] if rows else []
    for k in ("RSI_14","EMA_200","RSI_14_1h","RSI_14_4h","RSI_14_1d","ROC_9_1d","BTC_close"):
        if k in cols and (last[cols.index(k)] is None): nan[k]+=1
print("pairs",len(wl),"rows min/max",min(lens),max(lens),"ncols",len(cols))
print("has enter_long:", "enter_long" in cols)
print("last-row NaN counts:",dict(nan))
tot=sum(len(v) for v in out.values()); print("signal candles total",tot)
for p,v in out.items():
    if v: print(p,len(v),v[:3])
json.dump(out,open('livesig.json','w'))
