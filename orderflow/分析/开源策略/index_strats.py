"""把 /home/user/ext/srcs 里所有仓库的 freqtrade 策略类找出来，去掉完全相同的，写索引。用法：python index_strats.py 输出文件 [只要这些仓库(逗号分隔)]"""
import os, re, sys, hashlib, json
SRC = '/home/user/ext/srcs'; only = set(sys.argv[2].split(',')) if len(sys.argv) > 2 else None
rows, seen = [], set()
for root, _, fs in os.walk(SRC):
    for f in fs:
        if not f.endswith('.py'): continue
        p = os.path.join(root, f); rel = './' + os.path.relpath(p, SRC)
        if only and rel.split('/')[1] not in only: continue
        s = open(p, encoding='utf-8', errors='ignore').read(); h = hashlib.md5(s.encode()).hexdigest()[:10]
        for m in re.finditer(r'^class\s+(\w+)\s*\(\s*IStrategy\s*\)\s*:', s, re.M):
            tf = re.search(r'^\s+timeframe\s*=\s*[\'"](\w+)[\'"]', s, re.M) or re.search(r'^\s+ticker_interval\s*=\s*[\'"](\w+)[\'"]', s, re.M)
            key = h + m.group(1)
            if key in seen: continue
            seen.add(key)
            rows.append(dict(cls=m.group(1), base='IStrategy', path=rel, tf=tf.group(1) if tf else '?', h=h, freqai='freqai' in s.lower(),
                             short='can_short = True' in s or 'can_short=True' in s, v3='populate_entry_trend' in s, ntb=len(s)))
json.dump(rows, open(sys.argv[1], 'w'), ensure_ascii=False); print(len(rows), '个策略', sum(r['tf'] in {'5m', '15m', '1h', '4h', '1d'} and not r['freqai'] for r in rows), '个能跑')
