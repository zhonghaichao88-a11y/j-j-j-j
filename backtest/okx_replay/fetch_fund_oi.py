"""下载资金费率历史（接口只给约3个月）与持仓量日线历史（约2年），只读公开接口。"""
import json, os, time, urllib.request
UA = {'User-Agent': 'Mozilla/5.0 alpha-x-backtest'}
def get(u):
    for k in range(5):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request('https://www.okx.com' + u, headers=UA), timeout=20))
        except Exception:
            time.sleep(2 * (k + 1))
    return {}
cats = json.load(open('categories.json'))
insts = [i for i, c in cats.items() if c == '1']
out = {}
for n, inst in enumerate(insts):
    fr = []; after = None
    while True:
        r = get(f'/api/v5/public/funding-rate-history?instId={inst}&limit=100' + (f'&after={after}' if after else ''))
        d = r.get('data') or []
        if not d: break
        fr += [(int(x['fundingTime']), float(x['realizedRate'] or x['fundingRate'])) for x in d]
        if after == d[-1]['fundingTime']: break
        after = d[-1]['fundingTime']; time.sleep(0.12)
    oi = []; end = None
    while True:
        r = get(f'/api/v5/rubik/stat/contracts/open-interest-history?instId={inst}&period=1D&limit=100' + (f'&end={end}' if end else ''))
        d = r.get('data') or []
        if not d or (end and d[-1][0] == end): break
        oi += [(int(x[0]), float(x[2])) for x in d]
        end = d[-1][0]; time.sleep(0.45)
    out[inst] = dict(funding=sorted(set(fr)), oi=sorted(set(oi)))
    print(n, inst, len(fr), len(oi), flush=True)
json.dump(out, open('fund_oi.json', 'w'))
