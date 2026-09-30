"""V7 行情记录器：录制 OKX 公开数据，供以后研究（盘口、逐笔、资金费率、持仓量、爆仓）。

- 只用公开接口：一条独立的公开 WebSocket，不需要 API Key，不经过交易连接，和下单互不影响。
- 录哪些币：24h 成交额前 top_n 个 USDT 永续 + 扫描中的币（watch）+ 手动添加（extra），每小时刷新。
- 每个币每分钟汇总一行：成交 OHLC、主动买/卖金额、笔数、5 档盘口平均多空比/点差/深度、
  分钟末 5 档盘口、资金费率、持仓量、爆仓买/卖金额。按 UTC 日期写 recorder_data/YYYY-MM-DD.csv，
  过了当天自动压缩成 .csv.gz。
- 代理：读取 .env 的 PROXY_URL（http/https/socks5）。
"""
from __future__ import annotations
import csv, gzip, json, math, os, shutil, threading, time, urllib.request
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

BASE = Path(__file__).parent
CONFIG_FILE = Path(os.getenv('ALPHA_RECORDER_CONFIG', str(BASE / 'recorder_config.json')))
DATA_DIR = Path(os.getenv('ALPHA_RECORDER_DIR', str(BASE / 'recorder_data')))
WS_URL = os.getenv('ALPHA_OKX_WS_PUBLIC_URL', 'wss://ws.okx.com:8443/ws/v5/public')
REST_URL = os.getenv('ALPHA_OKX_REST_URL', 'https://www.okx.com')
DEFAULTS = dict(enabled=True, top_n=50, extra=[])
MAX_SYMBOLS = 150
WATCH_TTL = 2 * 3600
CHANNELS = ('trades', 'books5', 'funding-rate', 'open-interest')
LEVELS = 5
COLUMNS = (['ts', 'inst', 'open', 'high', 'low', 'close', 'buy_usdt', 'sell_usdt', 'trades', 'book_samples',
            'imbalance', 'spread_bps', 'bid5_usdt', 'ask5_usdt', 'mid', 'funding_rate', 'next_funding_ms',
            'oi_ccy', 'oi_usdt', 'liq_buy_usdt', 'liq_sell_usdt']
           + [f'{s}{k}_{f}' for s in ('b', 'a') for k in range(1, LEVELS + 1) for f in ('px', 'usdt')])
_LOCK = threading.RLock()


def _num(x):
    try:
        x = float(x)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding='utf-8')))
    except (OSError, ValueError):
        pass
    return validate_config(cfg)


def validate_config(cfg):
    out = dict(DEFAULTS)
    out['enabled'] = bool(cfg.get('enabled', DEFAULTS['enabled']))
    try:
        top = int(cfg.get('top_n', DEFAULTS['top_n']))
    except (TypeError, ValueError):
        raise ValueError('记录币数必须是整数')
    if not 0 <= top <= MAX_SYMBOLS:
        raise ValueError(f'记录币数须在 0~{MAX_SYMBOLS} 之间')
    out['top_n'] = top
    extra = cfg.get('extra') or []
    if isinstance(extra, str):
        extra = [x for x in extra.replace('，', ',').split(',')]
    norm = []
    for x in extra:
        x = str(x).strip().upper()
        if not x:
            continue
        if not x.endswith('-USDT-SWAP'):
            x = x.split('-')[0] + '-USDT-SWAP'
        if x not in norm:
            norm.append(x)
    out['extra'] = norm[:MAX_SYMBOLS]
    return out


def save_config(cfg):
    cfg = validate_config(cfg)
    tmp = CONFIG_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, CONFIG_FILE)
    return cfg


def _proxy():
    url = os.getenv('PROXY_URL', '').strip()
    if not url:
        return None
    p = urlparse(url)
    return dict(url=url, scheme=(p.scheme or 'http').lower(), host=p.hostname, port=p.port)


def rest(path, timeout=15):
    """公开 REST（只读）。有 http/https 代理就走代理。"""
    handlers = []
    px = _proxy()
    if px and px['scheme'] in ('http', 'https'):
        handlers.append(urllib.request.ProxyHandler({'http': px['url'], 'https': px['url']}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(REST_URL + path, headers={'User-Agent': 'Mozilla/5.0 alpha-x-recorder'})
    with opener.open(req, timeout=timeout) as r:
        body = json.load(r)
    if str(body.get('code')) != '0':
        raise RuntimeError(f'OKX 接口返回错误：{body.get("msg")}')
    return body.get('data') or []


class Minute:
    """一个币在当前这一分钟里的累计。"""
    def __init__(self):
        self.o = self.h = self.l = self.c = None
        self.buy = self.sell = 0.0
        self.n = 0
        self.bs = 0
        self.imb = self.spread = self.bidq = self.askq = 0.0
        self.liq_buy = self.liq_sell = 0.0

    def empty(self):
        return self.n == 0 and self.bs == 0 and self.liq_buy == 0 and self.liq_sell == 0


class InstState:
    def __init__(self):
        self.m = Minute()
        self.book = None          # 最近一次 5 档盘口 (bids, asks)，金额单位 USDT
        self.mid = None
        self.funding = None
        self.next_funding = None
        self.oi_ccy = None
        self.recent = deque(maxlen=240)   # 最近 4 小时的分钟行，给页面看


class Recorder:
    def __init__(self):
        self.cfg = load_config()
        self.ct_val = {}           # instId -> 合约面值（币）
        self.universe = []         # 当前录制的币
        self.top = []              # 成交额前 N
        self.watch_until = {}      # 扫描中的币 -> 过期时间
        self.states = {}
        self.ws = None
        self.connected = False
        self.subscribed = set()
        self.thread = None
        self.flusher = None
        self.stop_evt = threading.Event()
        self.last_msg = 0.0
        self.last_error = ''
        self.started_at = None
        self.reconnects = 0
        self.rows_written = 0
        self.universe_at = 0.0
        self.ct_at = 0.0
        self._file_day = None
        self._fh = None
        self._writer = None

    # ---------------- 录制名单 ----------------
    def watch(self, symbols):
        """扫描中的币加入录制（2 小时内没再出现就移出）。"""
        until = time.time() + WATCH_TTL
        with _LOCK:
            for s in symbols or []:
                s = str(s).upper()
                if s.endswith('-USDT-SWAP'):
                    self.watch_until[s] = until

    def _refresh_contracts(self):
        rows = rest('/api/v5/public/instruments?instType=SWAP')
        ct = {}
        for r in rows:
            if r.get('settleCcy') == 'USDT' and r.get('ctType') == 'linear' and r.get('state') == 'live':
                v = _num(r.get('ctVal'))
                if v and v > 0:
                    ct[r['instId']] = v
        if ct:
            with _LOCK:
                self.ct_val = ct
            self.ct_at = time.time()

    def _refresh_top(self):
        rows = rest('/api/v5/market/tickers?instType=SWAP')
        vol = []
        for r in rows:
            inst = r.get('instId', '')
            if not inst.endswith('-USDT-SWAP'):
                continue
            q = (_num(r.get('volCcy24h')) or 0) * (_num(r.get('last')) or 0)
            vol.append((q, inst))
        vol.sort(reverse=True)
        with _LOCK:
            self.top = [i for _, i in vol[:self.cfg['top_n']]]
        self.universe_at = time.time()

    def target_universe(self):
        now = time.time()
        with _LOCK:
            self.watch_until = {k: v for k, v in self.watch_until.items() if v > now}
            names = list(self.cfg['extra']) + list(self.top) + sorted(self.watch_until)
            out = []
            for s in names:
                if s not in out and (not self.ct_val or s in self.ct_val):
                    out.append(s)
            return out[:MAX_SYMBOLS]

    # ---------------- 消息处理（可离线测试） ----------------
    def handle(self, raw, now=None):
        if raw == 'pong':
            return
        now = time.time() if now is None else now
        msg = json.loads(raw)
        if msg.get('event') == 'error':
            self.last_error = 'WS订阅失败：' + str(msg.get('msg'))[:160]
            return
        if msg.get('event'):
            return
        arg = msg.get('arg') or {}
        channel = arg.get('channel')
        rows = msg.get('data')
        if not isinstance(rows, list):
            return
        self.last_msg = now
        with _LOCK:
            if channel == 'liquidation-orders':
                for r in rows:
                    inst = r.get('instId')
                    st = self.states.get(inst)
                    ct = self.ct_val.get(inst)
                    if st is None or not ct:
                        continue
                    for d in r.get('details') or []:
                        sz, px = _num(d.get('sz')), _num(d.get('bkPx'))
                        if sz is None or px is None:
                            continue
                        q = sz * ct * px
                        if d.get('side') == 'buy':
                            st.m.liq_buy += q      # 强平买单 = 空头被爆
                        else:
                            st.m.liq_sell += q     # 强平卖单 = 多头被爆
                return
            inst = arg.get('instId')
            st = self.states.get(inst)
            ct = self.ct_val.get(inst)
            if st is None:
                return
            if channel == 'trades' and ct:
                for r in rows:
                    px, sz = _num(r.get('px')), _num(r.get('sz'))
                    if px is None or sz is None or px <= 0 or sz <= 0:
                        continue
                    q = sz * ct * px
                    m = st.m
                    if m.o is None:
                        m.o = m.h = m.l = px
                    m.h = max(m.h, px); m.l = min(m.l, px); m.c = px
                    m.n += 1
                    if r.get('side') == 'buy':
                        m.buy += q
                    else:
                        m.sell += q
            elif channel == 'books5' and ct:
                for r in rows:
                    bids = [(_num(a[0]), _num(a[1])) for a in (r.get('bids') or [])[:LEVELS]]
                    asks = [(_num(a[0]), _num(a[1])) for a in (r.get('asks') or [])[:LEVELS]]
                    if not bids or not asks or any(p is None or s is None for p, s in bids + asks):
                        continue
                    if bids[0][0] >= asks[0][0]:
                        continue
                    b = [(p, s * ct * p) for p, s in bids]
                    a = [(p, s * ct * p) for p, s in asks]
                    bq = sum(q for _, q in b); aq = sum(q for _, q in a)
                    if bq + aq <= 0:
                        continue
                    mid = (bids[0][0] + asks[0][0]) / 2
                    m = st.m
                    m.bs += 1
                    m.imb += (bq - aq) / (bq + aq)
                    m.spread += (asks[0][0] - bids[0][0]) / mid * 10000
                    m.bidq += bq; m.askq += aq
                    st.book = (b, a); st.mid = mid
            elif channel == 'funding-rate':
                for r in rows:
                    v = _num(r.get('fundingRate'))
                    if v is not None:
                        st.funding = v
                        st.next_funding = int(_num(r.get('fundingTime')) or 0) or None
            elif channel == 'open-interest':
                for r in rows:
                    v = _num(r.get('oiCcy'))
                    if v is not None:
                        st.oi_ccy = v

    def flush(self, minute_ms):
        """把每个币当前这一分钟写成一行（minute_ms=这一分钟的开始时间），并开始新的一分钟。"""
        rows = []
        with _LOCK:
            for inst, st in self.states.items():
                m = st.m
                st.m = Minute()
                if m.empty() and st.mid is None:
                    continue
                o = m.o if m.o is not None else st.mid
                row = dict(ts=minute_ms, inst=inst, open=o, high=m.h if m.h is not None else o,
                           low=m.l if m.l is not None else o, close=m.c if m.c is not None else st.mid,
                           buy_usdt=round(m.buy, 2), sell_usdt=round(m.sell, 2), trades=m.n, book_samples=m.bs,
                           imbalance=round(m.imb / m.bs, 4) if m.bs else None,
                           spread_bps=round(m.spread / m.bs, 3) if m.bs else None,
                           bid5_usdt=round(m.bidq / m.bs, 2) if m.bs else None,
                           ask5_usdt=round(m.askq / m.bs, 2) if m.bs else None,
                           mid=st.mid, funding_rate=st.funding, next_funding_ms=st.next_funding, oi_ccy=st.oi_ccy,
                           oi_usdt=round(st.oi_ccy * st.mid, 2) if st.oi_ccy is not None and st.mid else None,
                           liq_buy_usdt=round(m.liq_buy, 2), liq_sell_usdt=round(m.liq_sell, 2))
                if st.book:
                    for side, levels in (('b', st.book[0]), ('a', st.book[1])):
                        for k, (p, q) in enumerate(levels, 1):
                            row[f'{side}{k}_px'] = p; row[f'{side}{k}_usdt'] = round(q, 2)
                st.recent.append(row)
                rows.append(row)
        if rows:
            self._write(minute_ms, rows)
        return rows

    # ---------------- 存盘 ----------------
    def _write(self, minute_ms, rows):
        day = time.strftime('%Y-%m-%d', time.gmtime(minute_ms / 1000))
        if day != self._file_day:
            old = self._file_day
            self._close_file()
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            path = DATA_DIR / f'{day}.csv'
            new = not path.exists()
            self._fh = open(path, 'a', newline='', encoding='utf-8')
            self._writer = csv.DictWriter(self._fh, fieldnames=COLUMNS, extrasaction='ignore')
            if new:
                self._writer.writeheader()
            self._file_day = day
            if old:
                threading.Thread(target=compress_day, args=(DATA_DIR / f'{old}.csv',), daemon=True).start()
        self._writer.writerows(rows)
        self._fh.flush()
        self.rows_written += len(rows)

    def _close_file(self):
        if self._fh:
            try:
                self._fh.close()
            except OSError:
                pass
        self._fh = self._writer = None

    # ---------------- 运行 ----------------
    def start(self):
        with _LOCK:
            if self.thread and self.thread.is_alive():
                if not self.stop_evt.is_set():
                    return
                self.thread.join(timeout=15)      # 刚关掉又打开：等旧线程退出
                if self.flusher:
                    self.flusher.join(timeout=5)
            self.stop_evt.clear()
            self.started_at = time.time()
            self.thread = threading.Thread(target=self._run, daemon=True, name='v7-recorder')
            self.thread.start()
            self.flusher = threading.Thread(target=self._flush_loop, daemon=True, name='v7-recorder-flush')
            self.flusher.start()

    def stop(self):
        self.stop_evt.set()
        ws = self.ws
        if ws:
            try:
                ws.close()
            except Exception:
                pass

    def running(self):
        return bool(self.thread and self.thread.is_alive() and not self.stop_evt.is_set())

    def _flush_loop(self):
        current = int(time.time() // 60)
        while not self.stop_evt.is_set():
            self.stop_evt.wait(1.0)
            minute = int(time.time() // 60)
            if minute != current:
                try:
                    self.flush(current * 60000)
                except Exception as exc:
                    self.last_error = '写文件失败：' + str(exc)[:160]
                current = minute
        with _LOCK:
            self._close_file()

    def _sync_subscriptions(self):
        target = self.target_universe()
        with _LOCK:
            for inst in target:
                self.states.setdefault(inst, InstState())
            for inst in list(self.states):
                if inst not in target:
                    del self.states[inst]
            self.universe = target
        want = set(target)
        add = sorted(want - self.subscribed); remove = sorted(self.subscribed - want)
        ws = self.ws
        if ws and self.connected:
            for op, insts in (('unsubscribe', remove), ('subscribe', add)):
                args = [{'channel': c, 'instId': i} for i in insts for c in CHANNELS]
                for k in range(0, len(args), 60):
                    ws.send(json.dumps({'op': op, 'args': args[k:k + 60]}))
            self.subscribed = want

    def _connect(self):
        import websocket
        kw = dict(timeout=10)
        px = _proxy()
        if px and px['host']:
            kw.update(http_proxy_host=px['host'], http_proxy_port=px['port'] or 80,
                      proxy_type='http' if px['scheme'] in ('http', 'https') else px['scheme'])
        return websocket.create_connection(WS_URL, **kw)

    def _run(self):
        import websocket
        while not self.stop_evt.is_set():
            try:
                if time.time() - self.ct_at > 86400 or not self.ct_val:
                    self._refresh_contracts()
                if time.time() - self.universe_at > 3600:
                    self._refresh_top()
                self.ws = self._connect()
                self.connected = True; self.subscribed = set()
                self.ws.send(json.dumps({'op': 'subscribe', 'args': [{'channel': 'liquidation-orders', 'instType': 'SWAP'}]}))
                self._sync_subscriptions()
                self.last_error = ''
                ping_at = 0.0; sync_at = time.time()
                while not self.stop_evt.is_set():
                    if time.time() - sync_at >= 60:       # 每分钟同步一次名单（扫描的币变化），每小时刷新成交额前N
                        if time.time() - self.universe_at > 3600:
                            try:
                                self._refresh_top()
                            except Exception as exc:
                                self.last_error = '刷新成交额榜失败：' + str(exc)[:120]
                        self._sync_subscriptions(); sync_at = time.time()
                    try:
                        raw = self.ws.recv()
                        if not raw:
                            raise ConnectionError('WS 已关闭')
                        self.handle(raw); ping_at = 0.0
                    except websocket.WebSocketTimeoutException:
                        if ping_at and time.time() - ping_at >= 10:
                            raise ConnectionError('WS 心跳超时')
                        self.ws.send('ping'); ping_at = time.time()
            except Exception as exc:
                if not self.stop_evt.is_set():
                    self.last_error = str(exc)[:200]
                    self.reconnects += 1
            finally:
                self.connected = False
                if self.ws:
                    try:
                        self.ws.close()
                    except Exception:
                        pass
                self.ws = None
            self.stop_evt.wait(5)

    # ---------------- 页面数据 ----------------
    def status(self):
        files = sorted(DATA_DIR.glob('*.csv*')) if DATA_DIR.exists() else []
        size = sum(f.stat().st_size for f in files)
        now = time.time()
        return dict(config=self.cfg, running=self.running(), connected=self.connected,
                    symbols=len(self.universe), watched=len(self.watch_until),
                    last_message_age=round(now - self.last_msg, 1) if self.last_msg else None,
                    rows_written=self.rows_written, reconnects=self.reconnects, last_error=self.last_error,
                    started_at=self.started_at, data_dir=str(DATA_DIR), files=len(files),
                    first_day=files[0].name.split('.')[0] if files else None, disk_mb=round(size / 1e6, 1))

    def overview(self):
        out = []
        with _LOCK:
            for inst in self.universe:
                st = self.states.get(inst)
                if not st or not st.recent:
                    continue
                last = st.recent[-1]
                hour = [r for r in st.recent if r['ts'] > last['ts'] - 3600000]
                buy = sum(r['buy_usdt'] for r in hour); sell = sum(r['sell_usdt'] for r in hour)
                oi0 = next((r['oi_ccy'] for r in hour if r.get('oi_ccy')), None)
                imbs = [r['imbalance'] for r in hour if r.get('imbalance') is not None]
                out.append(dict(inst=inst, ts=last['ts'], close=last.get('close'),
                                delta_1m=round(last['buy_usdt'] - last['sell_usdt'], 2),
                                delta_1h=round(buy - sell, 2), flow_1h=round((buy - sell) / (buy + sell), 4) if buy + sell else None,
                                imbalance=last.get('imbalance'), imbalance_1h=round(sum(imbs) / len(imbs), 4) if imbs else None,
                                spread_bps=last.get('spread_bps'), funding_rate=last.get('funding_rate'),
                                oi_usdt=last.get('oi_usdt'),
                                oi_change_1h=round(last['oi_ccy'] / oi0 - 1, 5) if oi0 and last.get('oi_ccy') else None,
                                liq_buy_1h=round(sum(r['liq_buy_usdt'] for r in hour), 2),
                                liq_sell_1h=round(sum(r['liq_sell_usdt'] for r in hour), 2), minutes=len(st.recent)))
        return out

    def series(self, inst, minutes=120):
        with _LOCK:
            st = self.states.get(inst)
            return list(st.recent)[-int(minutes):] if st else []


def compress_day(path):
    """把已经结束的一天压缩成 .gz，省硬盘（约缩小 5~8 倍）。"""
    try:
        if not path.exists():
            return
        with open(path, 'rb') as src, gzip.open(str(path) + '.gz', 'wb') as dst:
            shutil.copyfileobj(src, dst)
        path.unlink()
    except OSError:
        pass


RECORDER = Recorder()


def watch(symbols):
    RECORDER.watch(symbols)


def start_if_enabled():
    if os.getenv('ALPHA_RECORDER_AUTOSTART', '1') == '0':
        return False
    if RECORDER.cfg['enabled']:
        RECORDER.start()
        return True
    return False


def set_config(values):
    cfg = dict(RECORDER.cfg)
    cfg.update({k: v for k, v in (values or {}).items() if v is not None})
    cfg = save_config(cfg)
    top_changed = cfg['top_n'] != RECORDER.cfg['top_n']
    RECORDER.cfg = cfg
    if top_changed:
        RECORDER.universe_at = 0.0          # 下次同步时重新取成交额前 N
    if cfg['enabled']:
        RECORDER.start()
    else:
        RECORDER.stop()
    return cfg
