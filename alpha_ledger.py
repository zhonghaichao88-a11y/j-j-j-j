"""ALPHA-X 6.0 append-only execution ledger with trade attribution."""
from __future__ import annotations
import json, sqlite3, threading, time
from pathlib import Path
ROOT=Path(__file__).parent; DB=ROOT/"alpha_ledger.sqlite3"; LOCK=threading.RLock()

def _db():
    c=sqlite3.connect(DB,timeout=10); c.execute("PRAGMA journal_mode=WAL"); c.execute("CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, symbol TEXT, payload TEXT NOT NULL)"); c.commit(); return c

def record(kind:str,symbol:str,payload:dict):
    with LOCK:
        c=_db(); c.execute("INSERT INTO events(ts,kind,symbol,payload) VALUES(?,?,?,?)",(time.time(),kind,symbol,json.dumps(payload,ensure_ascii=False,default=str))); c.commit(); c.close()

def recent(limit=100):
    with LOCK:
        c=_db(); rows=c.execute("SELECT id,ts,kind,symbol,payload FROM events ORDER BY id DESC LIMIT ?",(int(limit),)).fetchall(); c.close()
    return [{"id":r[0],"ts":r[1],"kind":r[2],"symbol":r[3],"payload":json.loads(r[4])} for r in rows]

def trade_attribution(symbol, entry, exit, side, qty, entry_fee=0.0, exit_fee=0.0, funding=0.0, slippage=0.0, reason=""):
    e=float(entry); x=float(exit); q=float(qty); gross=(x-e)*q if side=="long" else (e-x)*q
    net=gross-float(entry_fee)-float(exit_fee)-float(funding)-float(slippage)
    rec={"entry":e,"exit":x,"side":side,"qty":q,"gross_pnl":gross,"entry_fee":float(entry_fee),"exit_fee":float(exit_fee),"funding":float(funding),"slippage":float(slippage),"net_pnl":net,"reason":reason}
    record("TRADE_ATTRIBUTION",symbol,rec); return rec
