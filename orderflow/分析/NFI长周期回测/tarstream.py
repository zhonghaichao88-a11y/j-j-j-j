# 豆包合约K线包（k/*.parquet，约 7.8GB）：并行分段下载 + 顺序拆 tar，只留要的币，不整包落盘。断了从断点接着下。
# 留：NFI 头部币名单里的币，或文件 ≥ 11MB（大约 2 年以上历史）的币；硬盘剩不到 400MB 就不再写新文件。
import sys, os, time, json, shutil, subprocess, threading, queue
AKA = 'https://aka.doubaocdn.com/s/m3k4tU557o'
OUT = '/home/user/ext/bnk'; CK = f'{OUT}/_checkpoint.json'; LOG = f'{OUT}/_log.txt'
CH = 8 * 1024 * 1024; WIN = 24
TOP = set(open('/home/user/ext/nfisig/top.txt').read().split())
os.makedirs(OUT, exist_ok=True)


def log(*a):
    s = time.strftime('%H:%M:%S ') + ' '.join(str(x) for x in a)
    print(s, flush=True); open(LOG, 'a').write(s + '\n')


def signed():
    for _ in range(10):
        r = subprocess.run(['curl', '-sS', '-o', '/dev/null', '-w', '%{redirect_url}', AKA], capture_output=True, text=True)
        if r.stdout.startswith('http'): return r.stdout
        time.sleep(5)
    raise SystemExit('拿不到下载地址')


URL = signed()
r = subprocess.run(['curl', '-sS', '-r', '0-0', '-D', '-', '-o', '/dev/null', URL], capture_output=True, text=True)
SIZE = int([l for l in r.stdout.splitlines() if l.lower().startswith('content-range')][0].split('/')[-1])


def fetch(off):
    end = min(off + CH, SIZE) - 1
    for k in range(30):
        r = subprocess.run(['curl', '-sS', '--max-time', '120', '-r', f'{off}-{end}', URL], capture_output=True)
        if len(r.stdout) == end - off + 1: return r.stdout
        time.sleep(min(2 + k, 15))
    raise RuntimeError(f'{off} 下载失败')


class Stream:
    """按顺序吐出字节；后台保持 WIN 个分段在下载"""
    def __init__(self, start):
        self.pos = start; self.buf = b''; self.next_off = start; self.q = {}
        self.lock = threading.Lock(); self.ev = threading.Condition(self.lock)
        self.inflight = 0; self.err = None
        self.pool = []
        self._fill()

    def _worker(self, off):
        try:
            b = fetch(off)
        except Exception as e:
            b = None; self.err = e
        with self.ev:
            self.q[off] = b; self.inflight -= 1; self.ev.notify_all()

    def _fill(self):
        while self.inflight < WIN and self.next_off < SIZE:
            off = self.next_off; self.next_off += CH; self.inflight += 1
            threading.Thread(target=self._worker, args=(off,), daemon=True).start()

    def read(self, n):
        while len(self.buf) < n:
            want = self.pos + len(self.buf)
            if want >= SIZE: break
            with self.ev:
                self._fill()
                while want not in self.q:
                    if self.err: raise self.err
                    self.ev.wait(1)
                b = self.q.pop(want)
            if b is None: raise RuntimeError('分段失败')
            self.buf += b
        out, self.buf = self.buf[:n], self.buf[n:]
        self.pos += len(out)
        return out


ck = json.load(open(CK)) if os.path.exists(CK) else {'off': 0}
st = Stream(ck['off'] // CH * CH)
skip = ck['off'] - ck['off'] // CH * CH
if skip: st.read(skip)
t0 = time.time(); kept = skipped = 0; nfiles = 0
while True:
    hdr_off = st.pos
    h = st.read(512)
    if len(h) < 512 or h == b'\0' * 512: break
    name = h[:100].split(b'\0')[0].decode('utf-8', 'ignore')
    size = int(h[124:136].split(b'\0')[0].strip() or b'0', 8)
    pad = (size + 511) // 512 * 512
    coin = os.path.basename(name)[:-8] if name.endswith('.parquet') else ''
    keep = bool(coin) and (coin in TOP or size >= 11 * 1024 * 1024) and shutil.disk_usage('/').free > 400 * 1024 * 1024
    data = st.read(pad)
    if keep and not os.path.exists(f'{OUT}/{coin}.parquet'):
        open(f'{OUT}/{coin}.parquet.tmp', 'wb').write(data[:size]); os.replace(f'{OUT}/{coin}.parquet.tmp', f'{OUT}/{coin}.parquet'); kept += 1
    elif coin:
        skipped += 1
    nfiles += 1
    json.dump({'off': st.pos, 'last': name}, open(CK, 'w'))
    if nfiles % 20 == 0:
        el = time.time() - t0
        log(f'{st.pos / 1e9:.2f}/{SIZE / 1e9:.2f}GB {(st.pos - ck["off"]) / 1e6 / el:.1f}MB/s 留{kept} 跳{skipped} 最近 {name} 剩盘 {shutil.disk_usage("/").free / 1e9:.1f}GB')
log('DONE', '留', kept, '跳', skipped)
