"""NFI 实盘看门程序（黑窗口里一直开着）：
- 启动 freqtrade（日志写到 user_data/logs，黑窗口只显示要紧的）
- 每 5 分钟一行工作状态；开仓 / 补仓 / 平仓 / 报错马上显示
- 每 6 小时按欧易真实成交额更新一次选币名单
- freqtrade 意外退出就自动重启（1 小时内最多 5 次，超过就停下等你处理）
- 关掉黑窗口 = 全部停止（已经开着的仓位留在交易所，下次启动接着管）"""
import base64, json, os, signal, subprocess, sys, time, urllib.request, webbrowser
from collections import deque
from datetime import datetime, timezone
import pairs
import webpage
import threading
from common import HERE, LIMITS, UD, read_proxy, read_settings, write_settings

LOG_DIR = os.path.join(UD, "logs")
FT_LOG = os.path.join(LOG_DIR, "freqtrade.log")
RUN_LOG = os.path.join(LOG_DIR, "工作状态.txt")
API = "http://127.0.0.1:8080/api/v1/"
STATUS_EVERY, PAIRS_EVERY, POLL = 300, 6 * 3600, 20


def say(msg):
    line = f"{datetime.now():%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    try:
        with open(RUN_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if os.path.getsize(RUN_LOG) > 5_000_000:
            os.replace(RUN_LOG, RUN_LOG + ".old")
    except OSError:
        pass


def ft_cmd():
    exe = os.path.join(HERE, ".venv", "Scripts", "freqtrade.exe") if os.name == "nt" else os.path.join(HERE, ".venv", "bin", "freqtrade")
    exe = os.environ.get("NFI_FT_EXE", exe)
    return [exe, "trade", "--userdir", "user_data", "--config", os.path.join("user_data", "config.json"),
            "--strategy", "NostalgiaForInfinityX7", "--logfile", os.path.join("user_data", "logs", "freqtrade.log")]


class Api:
    def __init__(self):
        c = json.load(open(os.path.join(UD, "config.json"), encoding="utf-8"))["api_server"]
        self.user, self.pw = c["username"], c["password"]
        self.auth = "Basic " + base64.b64encode(f"{c['username']}:{c['password']}".encode()).decode()

    def post(self, path):
        req = urllib.request.Request(API + path, data=b"{}", method="POST",
                                     headers={"Authorization": self.auth, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    def get(self, path):
        req = urllib.request.Request(API + path, headers={"Authorization": self.auth})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))


def short(pair):
    return str(pair).split("/")[0]


def num(x, fmt="{:.2f}"):
    """接口偶尔返回空值时显示 -，不让程序因为格式化出错停掉"""
    try:
        return fmt.format(float(x))
    except (TypeError, ValueError):
        return "-"


class Watcher:
    def __init__(self):
        self.proc = None
        self.starts = deque()
        self.log_pos = 0
        self.api = None
        self.open = {}            # trade_id -> 已成交的买入次数
        self.seen_closed = set()
        self.last_status = 0
        self.last_pairs = time.time()      # 启动前 make_config 刚更新过
        self.last_ok = 0
        self.warned_api = False
        self.t0 = int(time.time() * 1000)         # 这次启动的时间：之前平掉的单不再报
        self._settings_lock = threading.Lock()

    # ---------------------------------------------------- freqtrade 进程
    def start(self):
        now = time.time()
        while self.starts and now - self.starts[0] > 3600:
            self.starts.popleft()
        if len(self.starts) >= 5:
            say("❌ freqtrade 1 小时内已经重启 5 次还是退出，先停下。看 user_data\\logs\\freqtrade.log 最后的报错，截图发我")
            return False
        self.starts.append(now)
        os.makedirs(LOG_DIR, exist_ok=True)
        if os.path.exists(FT_LOG):
            self.log_pos = os.path.getsize(FT_LOG)
        out = open(os.path.join(LOG_DIR, "console.log"), "ab")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.proc = subprocess.Popen(ft_cmd(), cwd=HERE, stdout=out, stderr=subprocess.STDOUT, creationflags=flags)
        say(f"freqtrade 已启动（第 {len(self.starts)} 次），正在加载各个币的历史K线，第一次大约要几分钟…")
        return True

    def stop(self):
        if self.proc and self.proc.poll() is None:
            say("正在停止：先撤掉还没成交的挂单，再关 freqtrade（已成交的仓位留在交易所，下次启动接着管）…")
            try:                                   # 先让 freqtrade 自己停（这一步会撤未成交挂单），再关进程
                (self.api or Api()).post("stop")
                time.sleep(8)
            except Exception as e:  # noqa: BLE001
                say(f"⚠ 没能通知 freqtrade 撤单（{e}），下次启动它会自动核对挂单")
            try:
                if os.name == "nt":
                    self.proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    self.proc.terminate()
                self.proc.wait(timeout=30)
            except Exception:  # noqa: BLE001
                self.proc.kill()

    # ---------------------------------------------------- 日志里的报错
    def scan_log(self):
        if not os.path.exists(FT_LOG):
            return
        size = os.path.getsize(FT_LOG)
        if size < self.log_pos:                 # 日志被轮换了
            self.log_pos = 0
        with open(FT_LOG, "rb") as f:
            f.seek(self.log_pos)
            data = f.read()
            self.log_pos = f.tell()
        for raw in data.decode("utf-8", "replace").splitlines():
            if " - ERROR - " in raw or " - CRITICAL - " in raw:
                if "API Error calling" in raw:     # 网页 / 状态查询时机器人还没准备好，不是交易问题
                    continue
                msg = raw.split(" - ", 3)[-1][:300]
                hint = ""
                if "Insufficient" in msg or "insufficient" in msg:
                    hint = "（余额不够）"
                elif "Could not load markets" in msg or "NetworkError" in msg or "RequestTimeout" in msg:
                    hint = "（网络 / 代理问题，会自动重试）"
                elif "Invalid" in msg and ("key" in msg.lower() or "sign" in msg.lower()):
                    hint = "（API 密钥问题）"
                say(f"⚠ 报错{hint}：{msg}")

    # ---------------------------------------------------- 交易事件 + 工作状态
    def poll_api(self):
        if self.api is None:
            self.api = Api()
        try:
            cfg = self.api.get("show_config")
            trades = self.api.get("status")
        except Exception:  # noqa: BLE001
            if self.last_ok and time.time() - self.last_ok > 180 and not self.warned_api:
                say("⚠ 3 分钟没连上 freqtrade（可能正在加载数据或卡住了），继续等…")
                self.warned_api = True
            return
        self.last_ok, self.warned_api = time.time(), False
        if cfg.get("dry_run") and not getattr(self, "_dry_warned", False):
            say("⚠ 注意：现在是不下单的测试状态，不是实盘")
            self._dry_warned = True
        cur = {}
        for t in trades if isinstance(trades, list) else []:
            tid, n = t["trade_id"], int(t.get("nr_of_successful_entries") or 0)
            if n == 0:                          # 只是挂着买单、还没成交：不算开仓（没成交会被自动撤掉）
                continue
            cur[tid] = n
            if tid not in self.open:
                if self.last_status:                # 启动时已有的仓位不当成新开仓
                    say(f"🟢 开仓 {short(t['pair'])} 做多 价 {num(t.get('open_rate'), '{:.6g}')} 投入 {num(t.get('stake_amount'))}U")
            elif n > self.open[tid]:
                say(f"🟡 补仓 {short(t['pair'])} 第 {n - 1} 次补仓，现在共投入 {num(t.get('stake_amount'))}U，"
                    f"均价 {num(t.get('open_rate'), '{:.6g}')}，当前 {num(t.get('profit_pct'), '{:+.2f}')}%")
        try:                                    # 最近平掉的单（只报这次启动以后平的，不漏也不重复）
            recent = self.api.get("trades?limit=20&order_by_id=false").get("trades", [])   # 按平仓时间从新到旧
        except Exception:  # noqa: BLE001
            recent = []
        for t in recent:
            if t.get("is_open") or t["trade_id"] in self.seen_closed:
                continue
            if int(t.get("close_timestamp") or 0) < self.t0:
                self.seen_closed.add(t["trade_id"])
                continue
            self.seen_closed.add(t["trade_id"])
            pnl = float(t.get("close_profit_abs") or t.get("profit_abs") or 0)
            say(f"{'🔵' if pnl >= 0 else '🔴'} 平仓 {short(t['pair'])} 盈亏 {pnl:+.2f}U（{num((t.get('close_profit') or 0) * 100, '{:+.2f}')}%）原因 {t.get('exit_reason')}")
        self.open = cur
        if time.time() - self.last_status >= STATUS_EVERY:
            self.status_line(cfg, trades)
            self.last_status = time.time()

    def status_line(self, cfg, trades):
        try:
            bal = self.api.get("balance")
            prof = self.api.get("profit")
            day = self.api.get("daily?timescale=1")["data"][0]
            wl = self.api.get("whitelist")
        except Exception as e:  # noqa: BLE001
            say(f"状态读取失败：{e}")
            return
        state = webpage.STATE_CN.get(cfg.get("state"), f"状态 {cfg.get('state')}")
        trades = [t for t in trades if int(t.get("nr_of_successful_entries") or 0) > 0] if isinstance(trades, list) else []
        n_open = len(trades)
        pos = "、".join(f"{short(t['pair'])} {num(t.get('profit_pct'), '{:+.1f}')}%"
                        + (f"(补{int(t.get('nr_of_successful_entries') or 1) - 1})" if int(t.get('nr_of_successful_entries') or 1) > 1 else "")
                        for t in trades) or "无"
        ts = int(prof.get("latest_trade_timestamp") or 0) / 1000
        if ts:
            m = int((time.time() - ts) // 60)
            last = f"{m} 分钟前" if m < 60 else (f"{m // 60} 小时前" if m < 1440 else f"{m // 1440} 天前")
        else:
            last = "还没有"
        used = sum(float(t.get("stake_amount") or 0) for t in trades) if isinstance(trades, list) else 0.0
        self.check_whitelist(wl)
        say(f"{state}｜盯 {wl.get('length', 0)} 个币｜持仓 {n_open}/{int(cfg.get('max_open_trades') or 0)}：{pos}｜占用保证金 {used:.2f}U｜"
            f"账户 {num(bal.get('total'))}U｜今日(北京8点起) {num(day.get('abs_profit'), '{:+.2f}')}U｜累计 {num(prof.get('profit_all_coin'), '{:+.2f}')}U"
            f"（{prof.get('closed_trade_count', 0)} 笔已平，赢 {prof.get('winning_trades', 0)} 输 {prof.get('losing_trades', 0)}）｜最近一单 {last}")

    def apply_settings(self, new):
        """网页上改设置：检查范围 → 写 设置.txt → 马上重写选币名单和 freqtrade 设置 → 让 freqtrade 重新读设置（持仓不受影响）。
        返回给网页显示的一句话"""
        with self._settings_lock:
            vals = {}
            for k, (_, lo, hi) in LIMITS.items():
                try:
                    v = int(float(new.get(k, "")))
                except ValueError:
                    return f"「{k}」要填数字"
                if not lo <= v <= hi:
                    return f"「{k}」要在 {lo}~{hi} 之间"
                vals[k] = v
            old = dict(zip(LIMITS, read_settings()))
            if vals == old:
                return "设置没有变化"
            write_settings(vals)
            try:
                pairs.write(read_proxy(), vals["扫多少个币"])
            except Exception as e:  # noqa: BLE001
                say(f"⚠ 改币数后更新选币名单失败（继续用旧名单）：{e}")
            p = os.path.join(UD, "config.json")
            c = json.load(open(p, encoding="utf-8"))
            c["max_open_trades"] = vals["最多同时几单"]
            c["max_entry_position_adjustment"] = vals["最多补仓次数"]
            tmp = p + ".tmp"
            json.dump(c, open(tmp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
            os.replace(tmp, p)
            try:
                self.api.post("reload_config")
            except Exception as e:  # noqa: BLE001
                say(f"⚠ 设置已保存，但没能通知 freqtrade 重新读取（{e}），重启程序后生效")
                return "设置已保存，重启程序后生效"
            chg = "，".join(f"{k} {old[k]}→{vals[k]}" for k in vals if vals[k] != old[k])
            say(f"网页上改了设置：{chg}。freqtrade 正在重新读取设置（大约 1 分钟），已开的仓位不受影响")
            return f"已保存并生效：{chg}（freqtrade 重新读取设置大约要 1 分钟，已开的仓位不受影响）"

    def check_whitelist(self, wl):
        """核对 freqtrade 真的在用我们写的选币名单（不然可能在盯错的币）"""
        try:
            want = json.load(open(pairs.PATH, encoding="utf-8"))["pairs"]
        except Exception:  # noqa: BLE001
            say("⚠ 选币名单文件读不到（user_data\\pairs.json），下次更新时会重新生成")
            return
        n, _, _ = read_settings()
        want = set(want[:n])
        have = set(wl.get("whitelist") or [])
        if not have:
            say("⚠ freqtrade 现在一个币都没盯，检查网络 / 代理；名单文件下次更新会重写")
        elif len(have & want) < 0.8 * min(len(want), len(have)):
            say(f"⚠ freqtrade 盯的币和选币名单对不上（只重合 {len(have & want)} 个），1 小时内会自动换成新名单，没换就截图发我")

    def refresh_pairs(self):
        if time.time() - self.last_pairs < PAIRS_EVERY:
            return
        self.last_pairs = time.time()
        try:
            n, _, _ = read_settings()
            top, _ = pairs.write(read_proxy(), n)
            say(f"选币名单已更新：成交额前 {len(top)} 个币（freqtrade 1 小时内换用新名单；已开的仓位不受影响）")
        except Exception as e:  # noqa: BLE001
            say(f"⚠ 选币名单更新失败，继续用上一次的：{e}")

    def run(self):
        try:
            webpage.serve(self, RUN_LOG)
            say(f"状态网页：http://127.0.0.1:{webpage.PORT}  （正在自动打开浏览器；关掉网页不影响交易）")
            webbrowser.open(f"http://127.0.0.1:{webpage.PORT}")
        except Exception as e:  # noqa: BLE001
            say(f"⚠ 状态网页没开起来（不影响交易）：{e}")
        if not self.start():
            return 1
        try:
            while True:
                time.sleep(POLL)
                try:
                    self.scan_log()
                except Exception as e:  # noqa: BLE001
                    say(f"⚠ 读日志出错（不影响交易）：{e}")
                if self.proc.poll() is not None:
                    self.scan_log()
                    say(f"⚠ freqtrade 退出了（代码 {self.proc.returncode}），1 分钟后自动重启")
                    time.sleep(60)
                    self.api = None
                    if not self.start():
                        return 1
                    continue
                try:
                    self.poll_api()
                    self.refresh_pairs()
                except Exception as e:  # noqa: BLE001  —— 显示出错不能让看门程序停掉，freqtrade 照常跑
                    say(f"⚠ 状态显示出错（不影响交易）：{e}")
        except KeyboardInterrupt:
            self.stop()
            say("已停止。现在可以关掉窗口了。")
            return 0


if __name__ == "__main__":
    sys.exit(Watcher().run())
