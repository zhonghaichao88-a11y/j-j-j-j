"""中文状态网页 http://127.0.0.1:8090 （只在本机能打开）：账户、持仓、最近平仓、工作日志，每 10 秒自动刷新。
数据直接问本机的 freqtrade 接口；freqtrade 没起来时也能打开，会写明"正在启动"。只看不下单。"""
import html, json, os, threading, time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8090
CSS = """body{background:#0f1419;color:#d8dee9;font:14px/1.6 "Microsoft YaHei",system-ui,sans-serif;margin:0;padding:16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:22px 0 8px;color:#9fb3c8}
.sub{color:#7d8b99;font-size:13px}.cards{display:flex;flex-wrap:wrap;gap:10px;margin-top:12px}
.card{background:#1a222c;border-radius:8px;padding:10px 14px;min-width:130px}.card b{display:block;font-size:20px}
table{border-collapse:collapse;width:100%;background:#1a222c;border-radius:8px;overflow:hidden}
th,td{padding:6px 10px;text-align:left;border-bottom:1px solid #26313d;white-space:nowrap}th{color:#7d8b99;font-weight:normal}
.pos{color:#26a69a}.neg{color:#ef5350}.warn{color:#f0b90b}.wrap{overflow-x:auto}
pre{background:#1a222c;border-radius:8px;padding:10px;white-space:pre-wrap;font:13px/1.5 Consolas,monospace;margin:0}
a{color:#64b5f6}"""


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


def render(watcher, run_log):
    api = watcher.api
    data, err = {}, ""
    try:
        if api is None:
            raise RuntimeError("还在启动")
        for k, path in (("cfg", "show_config"), ("st", "status"), ("bal", "balance"), ("prof", "profit"),
                        ("day", "daily?timescale=1"), ("wl", "whitelist"), ("tr", "trades?limit=20&order_by_id=false")):
            data[k] = api.get(path)
    except Exception as e:  # noqa: BLE001
        err = f"freqtrade 还没准备好（{e}）。刚启动时加载数据要一两分钟，这个页面会自动刷新。"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out = [f"<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='10'>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>NFI 实盘状态</title><style>{CSS}</style></head><body>"]
    if err:
        out.append(f"<h1>NFI 实盘</h1><div class='sub'>{esc(now)} 刷新</div><p class='warn'>{esc(err)}</p>")
    else:
        cfg, st, bal, prof = data["cfg"], data["st"] if isinstance(data["st"], list) else [], data["bal"], data["prof"]
        day = (data["day"].get("data") or [{}])[0]
        state = "运行中" if cfg.get("state") == "running" else f"状态：{cfg.get('state')}"
        mode = "<span class='warn'>不下单的测试状态</span>" if cfg.get("dry_run") else "实盘"
        out.append(f"<h1>NFI 实盘 · {esc(state)}</h1><div class='sub'>{mode}｜只做多｜逐仓 3 倍｜每单最多补 {esc(cfg.get('max_entry_position_adjustment', '-'))} 次｜"
                   f"盯 {esc(data['wl'].get('length', 0))} 个币｜{esc(now)} 刷新（每 10 秒）</div>")
        out.append("<div class='cards'>")
        for name, val, c in (("账户 USDT", f2(bal.get("total")), ""),
                             ("今日（北京 8 点起）", f2(day.get("abs_profit"), "{:+.2f}"), cls(day.get("abs_profit"))),
                             ("累计盈亏", f2(prof.get("profit_all_coin"), "{:+.2f}"), cls(prof.get("profit_all_coin"))),
                             ("持仓", f"{len(st)} / {int(cfg.get('max_open_trades') or 0)}", ""),
                             ("已平仓", f"{prof.get('closed_trade_count', 0)}（赢 {prof.get('winning_trades', 0)} 输 {prof.get('losing_trades', 0)}）", ""),
                             ("最近一单", ago(prof.get("latest_trade_timestamp")) if prof.get("latest_trade_timestamp") else "还没有", "")):
            out.append(f"<div class='card'><span class='sub'>{esc(name)}</span><b class='{c}'>{esc(val)}</b></div>")
        out.append("</div><h2>持仓</h2>")
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
    lines = []
    try:
        with open(run_log, encoding="utf-8") as f:
            lines = f.readlines()[-30:]
    except OSError:
        pass
    out.append("<h2>工作日志（最近 30 行，最新在最下面）</h2><pre>" + esc("".join(lines) or "（还没有）") + "</pre>")
    out.append("<p class='sub'>freqtrade 自带的英文界面（装上了才有）：<a href='http://127.0.0.1:8080'>http://127.0.0.1:8080</a>（账号 nfi，密码见黑窗口）</p></body></html>")
    return "".join(out)


def serve(watcher, run_log):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/index.html"):
                self.send_error(404)
                return
            try:
                body = render(watcher, run_log).encode("utf-8")
            except Exception as e:  # noqa: BLE001
                body = f"<meta charset='utf-8'>页面出错：{html.escape(str(e))}".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):          # 不在黑窗口刷访问记录
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
