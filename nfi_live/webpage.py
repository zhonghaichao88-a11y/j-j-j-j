"""中文状态网页 http://127.0.0.1:8090 （只在本机能打开）：账户、持仓、最近平仓、工作日志，每 10 秒自动刷新。
数据直接问本机的 freqtrade 接口；freqtrade 没起来时也能打开，会写明"正在启动"。只看不下单。"""
import html, json, os, secrets, threading, time, urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE_CN = {"running": "运行中", "reload_config": "正在重新读取设置", "stopped": "已停止", "paused": "暂停开新单"}
PORT = 8090
TOKEN = secrets.token_hex(8)          # 防止别的网页偷偷提交设置
MSG = {"text": "", "t": 0}
CSS = """body{background:#0f1419;color:#d8dee9;font:14px/1.6 "Microsoft YaHei",system-ui,sans-serif;margin:0;padding:16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:22px 0 8px;color:#9fb3c8}
.sub{color:#7d8b99;font-size:13px}.cards{display:flex;flex-wrap:wrap;gap:10px;margin-top:12px}
.card{background:#1a222c;border-radius:8px;padding:10px 14px;min-width:130px}.card b{display:block;font-size:20px}
table{border-collapse:collapse;width:100%;background:#1a222c;border-radius:8px;overflow:hidden}
th,td{padding:6px 10px;text-align:left;border-bottom:1px solid #26313d;white-space:nowrap}th{color:#7d8b99;font-weight:normal}
.pos{color:#26a69a}.neg{color:#ef5350}.warn{color:#f0b90b}.wrap{overflow-x:auto}
pre{background:#1a222c;border-radius:8px;padding:10px;white-space:pre-wrap;font:13px/1.5 Consolas,monospace;margin:0}
a{color:#64b5f6}
form.set{background:#1a222c;border-radius:8px;padding:10px 14px;display:flex;flex-wrap:wrap;gap:14px;align-items:center}
form.set input{width:64px;background:#0f1419;color:#d8dee9;border:1px solid #3a4756;border-radius:4px;padding:4px 6px;font-size:14px}
form.set button{background:#2e7d32;color:#fff;border:0;border-radius:4px;padding:6px 14px;font-size:14px;cursor:pointer}
.msg{margin-top:8px}"""


CACHE = {}          # 接口路径 -> (时间, 数据)：freqtrade 一时忙不过来时，网页先显示上一次的数据


BUSY = {"until": 0.0}


def cget(api, path):
    """问 freqtrade 要数据（最多等 10 秒）；超时就用上一次（15 分钟内）的。返回 (数据, 几秒前的)。
    有一次超时后 20 秒内直接用旧数据，不再一个个去等，网页不会卡几分钟"""
    cached = CACHE.get(path)
    if time.time() < BUSY["until"] and cached and time.time() - cached[0] < 900:
        return cached[1], time.time() - cached[0]
    try:
        d = api.get(path, timeout=10)
        CACHE[path] = (time.time(), d)
        return d, 0
    except Exception:  # noqa: BLE001
        BUSY["until"] = time.time() + 20
        if cached and time.time() - cached[0] < 900:
            return cached[1], time.time() - cached[0]
        raise


def stale_note(age):
    if age <= 0:
        return ""
    return (f"<p class='warn'>freqtrade 正忙（在算行情），这次没来得及回答网页，下面是 {int(age)} 秒前的数据。"
            f"偶尔出现没关系；经常出现说明币太多、电脑算不过来，可以在设置里把「扫多少个币」调小。</p>")


def esc(x):
    return html.escape(str(x))


def cls(v):
    try:
        return "pos" if float(v) > 0 else ("neg" if float(v) < 0 else "")
    except (TypeError, ValueError):
        return ""


def f2(v, fmt="{:.2f}"):
    try:
        return fmt.format(float(v))
    except (TypeError, ValueError):
        return "-"


def ago(ts_ms):
    try:
        m = int((time.time() - int(ts_ms) / 1000) // 60)
    except (TypeError, ValueError):
        return "-"
    return f"{m} 分钟前" if m < 60 else (f"{m // 60} 小时前" if m < 1440 else f"{m // 1440} 天前")


def settings_form():
    from common import LIMITS, read_settings
    try:
        cur = dict(zip(LIMITS, read_settings()))
    except SystemExit as e:
        cur = {k: v[0] for k, v in LIMITS.items()}
        MSG["text"], MSG["t"] = str(e), time.time()
    parts = [f"<form class='set' method='post' action='/settings'><input type='hidden' name='token' value='{TOKEN}'>"]
    for k, (_, lo, hi) in LIMITS.items():
        parts.append(f"<label>{esc(k)} <input type='number' name='{esc(k)}' value='{cur[k]}' min='{lo}' max='{hi}'></label>")
    parts.append("<button type='submit'>保存（马上生效）</button></form>")
    if MSG["text"] and time.time() - MSG["t"] < 120:          # 提示显示 2 分钟
        parts.append(f"<div class='msg warn'>{esc(MSG['text'])}</div>")
    return "".join(parts)


def render(watcher, run_log):
    api = watcher.api
    data, err, age = {}, "", 0
    try:
        if api is None:
            raise RuntimeError("还在启动")
        for k, path in (("cfg", "show_config"), ("st", "status"), ("bal", "balance"), ("prof", "profit"),
                        ("day", "daily?timescale=1"), ("wl", "whitelist"), ("tr", "trades?limit=20&order_by_id=false")):
            data[k], a = cget(api, path)
            age = max(age, a)
    except Exception as e:  # noqa: BLE001
        err = f"freqtrade 还没准备好（{e}）。刚启动时加载数据要一两分钟，这个页面会自动刷新。"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out = [f"<!doctype html><html><head><meta charset='utf-8'>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>NFI 实盘状态</title><style>{CSS}</style></head><body>"
           "<p style='margin:0 0 8px'><a href='/work'>👉 详细工作状态（程序在不在干活、每单细节、下次补仓价、离强平多远）</a></p>"]
    out.append(stale_note(age))
    if err:
        out.append(f"<h1>NFI 实盘</h1><div class='sub'>{esc(now)} 刷新</div><p class='warn'>{esc(err)}</p>")
    else:
        cfg, st, bal, prof = data["cfg"], data["st"] if isinstance(data["st"], list) else [], data["bal"], data["prof"]
        pending = [t for t in st if int(t.get("nr_of_successful_entries") or 0) == 0]       # 挂着还没成交的买单
        st = [t for t in st if int(t.get("nr_of_successful_entries") or 0) > 0]
        day = (data["day"].get("data") or [{}])[0]
        state = STATE_CN.get(cfg.get("state"), f"状态：{cfg.get('state')}")
        mode = "<span class='warn'>不下单的测试状态</span>" if cfg.get("dry_run") else "实盘"
        out.append(f"<h1>NFI 实盘 · {esc(state)}</h1><div class='sub'>{mode}｜只做多｜逐仓 3 倍｜每单最多补 {esc(cfg.get('max_entry_position_adjustment', '-'))} 次｜"
                   f"盯 {esc(data['wl'].get('length', 0))} 个币｜{esc(now)} 刷新（每 10 秒）</div>")
        out.append("<div class='cards'>")
        for name, val, c in (("账户 USDT", f2(bal.get("total")), ""),
                             ("今日（北京 8 点起）", f2(day.get("abs_profit"), "{:+.2f}"), cls(day.get("abs_profit"))),
                             ("累计盈亏", f2(prof.get("profit_all_coin"), "{:+.2f}"), cls(prof.get("profit_all_coin"))),
                             ("持仓", f"{len(st)} / {int(cfg.get('max_open_trades') or 0)}", ""),
                             ("占用保证金", f"{sum(float(t.get('stake_amount') or 0) for t in st):.2f}U", ""),
                             ("已平仓", f"{prof.get('closed_trade_count', 0)}（赢 {prof.get('winning_trades', 0)} 输 {prof.get('losing_trades', 0)}）", ""),
                             ("最近一单", ago(prof.get("latest_trade_timestamp")) if prof.get("latest_trade_timestamp") else "还没有", "")):
            out.append(f"<div class='card'><span class='sub'>{esc(name)}</span><b class='{c}'>{esc(val)}</b></div>")
        out.append("</div><h2>持仓</h2>")
        if pending:
            out.append("<p class='sub'>挂着还没成交的买单：" + "、".join(esc(str(t.get('pair')).split('/')[0]) for t in pending) + "（没成交会自动撤）</p>")
        if st:
            out.append("<div class='wrap'><table><tr><th>币</th><th>开仓</th><th>均价</th><th>现价</th><th>投入(U)</th><th>补仓</th><th>浮盈亏</th></tr>")
            for t in st:
                adds = max(int(t.get("nr_of_successful_entries") or 1) - 1, 0)
                out.append(f"<tr><td>{esc(str(t.get('pair')).split('/')[0])}</td><td>{esc(ago(t.get('open_timestamp')))}</td>"
                           f"<td>{f2(t.get('open_rate'), '{:.6g}')}</td><td>{f2(t.get('current_rate'), '{:.6g}')}</td>"
                           f"<td>{f2(t.get('stake_amount'))}</td><td>{adds} 次</td>"
                           f"<td class='{cls(t.get('profit_abs'))}'>{f2(t.get('profit_abs'), '{:+.2f}')}U（{f2(t.get('profit_pct'), '{:+.2f}')}%）</td></tr>")
            out.append("</table></div>")
        else:
            out.append("<p class='sub'>现在没有持仓（NFI 平均一天一单左右，没单是正常的）</p>")
        out.append("<h2>最近平仓</h2>")
        tr = [t for t in data["tr"].get("trades", []) if not t.get("is_open")]
        if tr:
            out.append("<div class='wrap'><table><tr><th>币</th><th>平仓</th><th>拿了多久</th><th>补仓</th><th>盈亏</th><th>原因</th></tr>")
            for t in tr:
                hold = (int(t.get("close_timestamp") or 0) - int(t.get("open_timestamp") or 0)) / 3_600_000
                adds = max(int(t.get("nr_of_successful_entries") or 1) - 1, 0)
                out.append(f"<tr><td>{esc(str(t.get('pair')).split('/')[0])}</td><td>{esc(ago(t.get('close_timestamp')))}</td>"
                           f"<td>{hold:.1f} 小时</td><td>{adds} 次</td>"
                           f"<td class='{cls(t.get('close_profit_abs'))}'>{f2(t.get('close_profit_abs'), '{:+.2f}')}U（{f2((t.get('close_profit') or 0) * 100, '{:+.2f}')}%）</td>"
                           f"<td>{esc(t.get('exit_reason'))}</td></tr>")
            out.append("</table></div>")
        else:
            out.append("<p class='sub'>还没有平仓记录</p>")
    out.append("<h2>设置</h2>" + settings_form() +
               "<p class='sub'>扫的币越多单越多（电脑也越吃力，80 个约 0.6GB 内存）；100U 本金建议最多同时 6 单；回测最好的是最多补 3 次。改了只影响以后的单。</p>")
    lines = []
    try:
        with open(run_log, encoding="utf-8") as f:
            lines = f.readlines()[-30:]
    except OSError:
        pass
    out.append("<h2>工作日志（最近 30 行，最新在最下面）</h2><pre>" + esc("".join(lines) or "（还没有）") + "</pre>")
    out.append("<script>setInterval(function(){var a=document.activeElement;"
               "if(!a||a.tagName!=='INPUT')location.reload();},10000);</script>")
    out.append("<p class='sub'>freqtrade 自带的英文界面（装上了才有）：<a href='http://127.0.0.1:8080'>http://127.0.0.1:8080</a>（账号 nfi，密码见黑窗口）</p></body></html>")
    return "".join(out)


def serve(watcher, run_log):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/index.html", "/work"):
                self.send_error(404)
                return
            try:
                if self.path == "/work":
                    import pairs, work_page
                    from common import read_settings
                    from nfi_run import PAIRS_EVERY
                    body = work_page.render_work(watcher, run_log, esc, f2, cls, CSS, read_settings, pairs.PATH, PAIRS_EVERY, cget, stale_note).encode("utf-8")
                else:
                    body = render(watcher, run_log).encode("utf-8")
            except Exception as e:  # noqa: BLE001
                body = f"<meta charset='utf-8'>页面出错：{html.escape(str(e))}".encode("utf-8")
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionError, OSError):     # 浏览器刷新 / 关页面时断开连接，正常现象，不打报错
                pass

        def do_POST(self):
            if self.path != "/settings":
                self.send_error(404)
                return
            n = int(self.headers.get("Content-Length") or 0)
            form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(min(n, 10000)).decode("utf-8")).items()}
            if form.get("token") != TOKEN:
                MSG["text"], MSG["t"] = "页面过期了，刷新后再改", time.time()
            else:
                try:
                    MSG["text"], MSG["t"] = watcher.apply_settings(form), time.time()
                except Exception as e:  # noqa: BLE001
                    MSG["text"], MSG["t"] = f"保存失败：{e}", time.time()
            self.send_response(303)                # 提交完跳回首页（刷新页面不会重复提交）
            self.send_header("Location", "/")
            self.end_headers()

        def log_message(self, *a):          # 不在黑窗口刷访问记录
            pass

    class Quiet(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):     # 网页连接出的任何小错都不刷到黑窗口（不影响交易）
            pass

    srv = Quiet(("127.0.0.1", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
