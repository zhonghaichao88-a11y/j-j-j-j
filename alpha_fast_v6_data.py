"""V6 可选实时证据。只读公共接口；失败/过期返回缺失，不伪造零值。"""
import time
import threading
from alpha_fast_v6 import num
_CACHE={}; _HISTORY={}; _LOCK=threading.RLock()


def snapshot(exchange, symbol, now=None):
    now=time.time() if now is None else now
    with _LOCK:
        prev=_CACHE.get(symbol)
        if prev and now-prev[0]<5: return dict(prev[1])
    out=dict(book_imbalance=None, trade_imbalance=None, funding_rate=None, open_interest=None,
             oi_change_pct=None, fresh=False, book_confirmed=False, missing=[], received_at=now)
    # 串行限频，复用用户已连接的交易所；没有独立账户/密钥。
    book_ts=0
    try:
        book=exchange.fetch_order_book(symbol,limit=20)
        book_ts=num(book.get('timestamp'))/1000
        if not book_ts or not 0<=now-book_ts<=10: raise ValueError('盘口缺少时间戳或过期')
        bids=book.get('bids') or []; asks=book.get('asks') or []
        if not bids or not asks or num(bids[0][0])>=num(asks[0][0]): raise ValueError('盘口无效')
        b=sum(num(x[0])*num(x[1]) for x in bids[:10]); a=sum(num(x[0])*num(x[1]) for x in asks[:10])
        if min(a,b)<=0: raise ValueError('盘口深度不足')
        out['book_imbalance']=(b-a)/(b+a)
    except Exception as exc: out['missing'].append('盘口:'+str(exc))
    try:
        trades=exchange.fetch_trades(symbol,limit=100); buy=sell=0.0; seen=set(); times=[]
        for t in trades:
            ts=num(t.get('timestamp'))/1000
            if not ts or not 0<=now-ts<=30: continue
            key=t.get('id') or (ts,t.get('side'),t.get('price'),t.get('amount'))
            if key in seen: continue
            seen.add(key); n=num(t.get('price'))*num(t.get('amount'))
            if n<=0: continue
            if t.get('side')=='buy': buy+=n; times.append(ts)
            elif t.get('side')=='sell': sell+=n; times.append(ts)
        if buy+sell<=0 or len(times)<5: raise ValueError('近期有效成交不足5笔')
        out['trade_imbalance']=(buy-sell)/(buy+sell)
        out['trade_count']=len(times); out['trade_last_ts']=max(times)
    except Exception as exc: out['missing'].append('逐笔成交:'+str(exc))
    # 慢变量 60 秒缓存；单次值不能伪装成 OI 变化。
    with _LOCK: history=dict(_HISTORY.get(symbol) or {})
    if now-num(history.get('slow_at'))>=60:
        slow={}
        for name,method,field in [('funding_rate','fetch_funding_rate','fundingRate'),('open_interest','fetch_open_interest','openInterestAmount')]:
            try:
                d=getattr(exchange,method)(symbol); val=d.get(field)
                if val is None or not __import__('math').isfinite(float(val)): raise ValueError('字段缺失')
                slow[name]=float(val)
            except Exception as exc: out['missing'].append(name+':'+str(exc)); slow[name]=None
        old=history.get('open_interest'); new=slow.get('open_interest')
        change=(new/old-1) if old and new is not None else None
        history.update(slow,slow_at=now,oi_change_pct=change)
    for k in ('funding_rate','open_interest','oi_change_pct'): out[k]=history.get(k)
    last_book=history.get('book_imbalance'); last_ts=num(history.get('book_ts'))
    cur=out['book_imbalance']
    out['book_confirmed']=bool(cur is not None and last_book is not None and 0<book_ts-last_ts<=15 and cur*last_book>0 and min(abs(cur),abs(last_book))>.10)
    out['fresh']=cur is not None and out['trade_imbalance'] is not None
    if cur is not None: history.update(book_imbalance=cur,book_ts=book_ts)
    with _LOCK: _HISTORY[symbol]=history; _CACHE[symbol]=(now,dict(out))
    return out
