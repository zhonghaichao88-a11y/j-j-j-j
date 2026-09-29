"""Optional OKX private WebSocket monitor for ALPHA-X.
REST remains the source of truth for recovery; WS is the low-latency event feed.
"""
from __future__ import annotations
import base64, hashlib, hmac, json, os, threading, time
from typing import Callable, Optional

try:
    import websocket
except Exception:
    websocket=None

class AlphaPrivateWS:
    def __init__(self, on_event:Optional[Callable[[dict],None]]=None):
        self.on_event=on_event or (lambda x:None); self.ws=None; self.thread=None; self.stop_flag=threading.Event(); self.last_event=0.0; self.connected_at=0.0; self.last_transport_ok=0.0; self.error=""
    def _url(self): return os.getenv("ALPHA_OKX_WS_PRIVATE_URL","wss://ws.okx.com:8443/ws/v5/private")
    def _sign(self,timestamp,method="GET",request_path="/users/self/verify",body=""):
        msg=f"{timestamp}{method}{request_path}{body}"; secret=os.getenv("OKX_API_SECRET","")
        return base64.b64encode(hmac.new(secret.encode(),msg.encode(),hashlib.sha256).digest()).decode()
    def _login(self):
        ts=str(time.time()); return {"op":"login","args":[{"apiKey":os.getenv("OKX_API_KEY",""),"passphrase":os.getenv("OKX_API_PASSPHRASE",os.getenv("OKX_API_PASSWORD","")),"timestamp":ts,"sign":self._sign(ts)}]}
    def _run(self):
        if websocket is None: self.error="websocket-client 未安装"; return
        while not self.stop_flag.is_set():
            try:
                self.ws=websocket.create_connection(self._url(),timeout=5,enable_multithread=True,ping_interval=20,ping_timeout=10)
                self.connected_at=time.time(); self.last_transport_ok=self.connected_at; self.error=""
                self.ws.send(json.dumps(self._login()))
                self.ws.send(json.dumps({"op":"subscribe","args":[{"channel":"orders","instType":"SWAP"},{"channel":"positions","instType":"SWAP"}]}))
                while not self.stop_flag.is_set():
                    try: msg=self.ws.recv()
                    except Exception as exc: raise RuntimeError(str(exc))
                    if not msg: continue
                    now=time.time(); self.last_transport_ok=now
                    data=json.loads(msg); self.last_event=now; self.on_event(data)
            except Exception as exc:
                self.error=str(exc); time.sleep(2)
            finally:
                try:
                    if self.ws:self.ws.close()
                except Exception: pass
                self.ws=None
    def start(self):
        if self.thread and self.thread.is_alive(): return False
        self.stop_flag.clear(); self.thread=threading.Thread(target=self._run,daemon=True,name="alpha-okx-private-ws"); self.thread.start(); return True
    def stop(self):
        self.stop_flag.set()
        try:
            if self.ws:self.ws.close()
        except Exception: pass
    def status(self):
        running=bool(self.thread and self.thread.is_alive()); now=time.time()
        return {"running":running,"last_event":self.last_event,"last_transport_ok":self.last_transport_ok,"connected_at":self.connected_at,"transport_age":(now-self.last_transport_ok) if self.last_transport_ok else None,"event_age":(now-self.last_event) if self.last_event else None,"error":self.error,"url":self._url()}
