"""详细工作状态页 http://127.0.0.1:8090/work ：程序到底在不在干活、每个持仓的细节（模式、成交记录、下次补仓价、离强平多远）、
挂单、资金分布、近 7 天盈亏、关键事件。只看不下单，每 30 秒自动刷新。"""
import json, os, time
from datetime import datetime

MODES = [(range(1, 14), "正常模式"), (range(21, 27), "拉升模式"), (range(41, 54), "快速模式"), (range(61, 69), "抄底补仓模式"),
         (range(81, 83), "高收益模式"), (range(101, 111), "急跌模式"), (range(120, 121), "网格模式"), (range(141, 146), "大币模式"),
         (range(161, 174), "短线模式")]
REBUY_TAGS = {"61", "62", "63", "64", "65", "68"}          # 策略里 long_rebuy_mode_tags
REBUY_TH = [-0.08, -0.12, -0.16, -0.20]                    # 策略里 system_v3_rebuy_mode_thresholds_futures：比上一次买入价再跌多少补
KEY_WORDS = ("开仓", "补仓", "平仓", "报错", "⚠", "✅", "❌", "改了设置", "新的一次启动", "已启动", "已停止", "选币名单")


def mode_name(tag):
    names = []
    for t in str(tag or "").split():
        try:
            n = int(t)
        except ValueError:
            continue
        for r, nm in MODES:
            if n in r and nm not in names:
                names.append(nm)
    return "、".join(names) or "未知"


def dur(sec):
    sec = max(int(sec), 0)
    d, h, m = sec // 86400, sec % 86400 // 3600, sec % 3600 // 60
    return (f"{d} 天 " if d else "") + (f"{h} 小时 " if h or d else "") + f"{m} 分"


def hm(ts):
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "-"


def rebuy_plan(t, max_adj):
    """抄底补仓模式：按策略规则算下一次（和之后几次）补仓价。返回 (说明文字, [价格...])"""
    tags = set(str(t.get("enter_tag") or "").split())
    if not tags or not tags <= REBUY_TAGS:
        return "不是抄底补仓模式：补仓/减仓由策略的分批逻辑按行情决定，没有固定价格", []
    orders = [o for o in t.get("orders") or [] if float(o.get("filled") or 0) > 0 and not o.get("is_open")]
    if any(o.get("ft_order_side") == "sell" for o in orders):
        return "这单已经部分卖出过，之后的补仓规则会变，按策略判断", []
    buys = [o for o in orders if o.get("ft_order_side") == "buy"]
    if not buys:
        return "-", []
    done = len(buys) - 1
    limit = min(int(max_adj), len(REBUY_TH))
    if done >= limit:
        return f"已经补满 {done} 次（上限 {limit}），不会再补", []
    last = float(buys[-1].get("safe_price") or 0)
    prices = []
    for k in range(done, limit):                # 之后几次按"上一次补在触发价"估算
        last = last * (1 + REBUY_TH[k])
        prices.append(last)
    return f"已补 {done} 次，最多 {limit} 次", prices


def render_work(watcher, run_log, esc, f2, cls, css, read_settings, pairs_path, pairs_every):
    api = watcher.api
    now = time.time()
    out = [f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>NFI 详细工作状态</title><style>{css}.ok{{color:#26a69a}}.bad{{color:#ef5350}}td.l{{white-space:normal}}</style></head><body>"
           f"<h1>NFI 详细工作状态</h1><div class='sub'><a href='/'>← 回到首页</a>｜{datetime.now():%Y-%m-%d %H:%M:%S} 刷新（每 30 秒）</div>"]
    d, err = {}, ""
    try:
        if api is None:
            raise RuntimeError("还在启动")
        for k, path in (("cfg", "show_config"), ("st", "status"), ("bal", "balance"), ("health", "health"),
                        ("wl", "whitelist"), ("days", "daily?timescale=7"), ("prof", "profit")):
            d[k] = api.get(path)
    except Exception as e:  # noqa: BLE001
        err = str(e)

    # ---------- 1 程序在不在干活
    out.append("<h2>1. 程序在不在干活</h2><div class='wrap'><table>")
    rows = []
    if err:
        rows.append(("freqtrade", f"<span class='bad'>连不上（{esc(err)}）</span>，刚启动或改设置时 1~3 分钟内是正常的"))
    else:
        cfg, h = d["cfg"], d["health"]
        st = cfg.get("state")
        rows.append(("freqtrade 状态", f"<span class='{'ok' if st == 'running' else 'bad'}'>{esc({'running': '运行中', 'stopped': '已停止', 'reload_config': '正在重新读取设置'}.get(st, st))}</span>"))
        lp = h.get("last_process_ts")
        if lp:
            age = now - float(lp)
            c = "ok" if age < 180 else ("warn" if age < 600 else "bad")
            tip = "正常（每几秒就会转一圈）" if age < 180 else ("有点慢，可能电脑忙" if age < 600 else "超过 10 分钟没动，可能卡住了，截图发我")
            rows.append(("主循环最后一次转动", f"<span class='{c}'>{int(age)} 秒前 · {tip}</span>"))
        if h.get("bot_start_ts"):
            rows.append(("这次已连续运行", dur(now - float(h["bot_start_ts"]))))
    starts = [x for x in watcher.starts if now - x < 3600]
    rows.append(("1 小时内启动 freqtrade 次数", f"<span class='{'ok' if len(starts) <= 1 else 'warn'}'>{len(starts)}</span>（含刚启动那一次；大于 1 说明中途退出过、被自动重启）"))
    fr = watcher.fresh
    if fr:
        good = not fr["late"]
        txt = (f"{fr['ok']}/{fr['total']} 个币算到了最新K线（{hm(fr['newest'] / 1000) if fr['newest'] else '-'}）" +
               ("" if good else f"，落后：{esc('、'.join(fr['late'][:15]))}"))
        rows.append(("实算检查（每 15 分钟）", f"<span class='{'ok' if good else 'bad'}'>{txt}</span>，{int((now - fr['t']) / 60)} 分钟前查的"))
    else:
        rows.append(("实算检查（每 15 分钟）", "启动或改设置后 15 分钟才开始查，稍等"))
    slow = [s for t, s in watcher.slow if now - t < 3600]
    rows.append(("最近 1 小时算得太慢", f"<span class='{'ok' if len(slow) <= 1 else 'warn'}'>{len(slow)} 次</span>" +
                 (f"，最慢 {max(slow):.0f} 秒" if slow else "") + "（超过 75 秒算一次；改设置、刚启动后出现一次是正常的）"))
    try:
        pj = json.load(open(pairs_path, encoding="utf-8"))
        nxt = pairs_every - (now - watcher.last_pairs)
        rows.append(("选币名单", f"{len(pj.get('pairs', []))} 个币，{esc(pj.get('updated', '-'))} 更新；下次自动更新约 {dur(max(nxt, 0))} 后"))
    except Exception:  # noqa: BLE001
        rows.append(("选币名单", "<span class='bad'>读不到 user_data/pairs.json</span>"))
    if not err:
        rows.append(("freqtrade 实际在盯", f"{d['wl'].get('length', 0)} 个币"))
    try:
        n, o, a = read_settings()
        rows.append(("设置", f"扫 {n} 个币｜最多同时 {o} 单｜每单最多补 {a} 次｜只做多｜逐仓 3 倍"))
    except SystemExit:
        pass
    out += [f"<tr><th>{esc(k)}</th><td class='l'>{v}</td></tr>" for k, v in rows]
    out.append("</table></div>")
    if err:
        out.append("</body></html>")
        return "".join(out)

    cfg, bal = d["cfg"], d["bal"]
    allst = d["st"] if isinstance(d["st"], list) else []
    st = [t for t in allst if int(t.get("nr_of_successful_entries") or 0) > 0]

    # ---------- 2 资金
    usdt = next((x for x in bal.get("currencies", []) if x.get("currency") == "USDT"), {})
    used = sum(float(t.get("stake_amount") or 0) for t in st)
    total = float(bal.get("total") or 0)
    out.append("<h2>2. 资金</h2><div class='cards'>")
    for name, val in (("账户总额", f"{total:.2f}U"), ("可用", f"{float(usdt.get('free') or 0):.2f}U"),
                      ("持仓占用保证金", f"{used:.2f}U（{used / total * 100 if total else 0:.0f}%）"),
                      ("持仓浮盈亏", f"{sum(float(t.get('profit_abs') or 0) for t in st):+.2f}U"),
                      ("累计已平仓盈亏", f"{float(d['prof'].get('profit_closed_coin') or 0):+.2f}U")):
        out.append(f"<div class='card'><span class='sub'>{esc(name)}</span><b>{esc(val)}</b></div>")
    out.append("</div>")

    # ---------- 3 持仓详情
    max_adj = int(cfg.get("max_entry_position_adjustment") or 0)
    out.append(f"<h2>3. 持仓详情（{len(st)} / {int(cfg.get('max_open_trades') or 0)}）</h2>")
    if not st:
        out.append("<p class='sub'>现在没有持仓。NFI 只在跌多了、指标到位时才买，几天没单是正常的。</p>")
    for t in st:
        pair = str(t.get("pair")).split("/")[0]
        cur, avg = float(t.get("current_rate") or 0), float(t.get("open_rate") or 0)
        liq = float(t.get("liquidation_price") or 0)
        move = (cur / avg - 1) * 100 if avg else 0
        note, plan = rebuy_plan(t, max_adj)
        out.append(f"<h3 style='margin:14px 0 6px'>{esc(pair)} · {esc(mode_name(t.get('enter_tag')))}"
                   f"<span class='sub'>（信号编号 {esc(t.get('enter_tag'))}）</span></h3><div class='wrap'><table>")
        info = [("开仓时间", f"{esc(str(t.get('open_date'))[:16])}（已拿 {dur(now - int(t.get('open_timestamp') or 0) / 1000)}）"),
                ("均价 → 现价", f"{f2(avg, '{:.6g}')} → {f2(cur, '{:.6g}')}（币价 <span class='{cls(move)}'>{move:+.2f}%</span>）"),
                ("浮盈亏", f"<span class='{cls(t.get('profit_abs'))}'>{f2(t.get('profit_abs'), '{:+.2f}')}U（按保证金 {f2(t.get('profit_pct'), '{:+.2f}')}%，3 倍杠杆）</span>"),
                ("投入保证金", f"{f2(t.get('stake_amount'))}U（仓位价值约 {float(t.get('stake_amount') or 0) * float(t.get('leverage') or 1):.2f}U）"),
                ("资金费累计", f"{f2(t.get('funding_fees'), '{:+.4f}')}U"),
                ("强平价", f"{f2(liq, '{:.6g}')}（离现价还要跌 {(1 - liq / cur) * 100 if cur and liq else 0:.1f}%）· 交易所上唯一的保底，逐仓只亏这一单"),
                ("止盈", "没有固定价格：策略每 5 分钟看一次，涨到合适就卖（回测里每单平均赚保证金的 5~6%）"),
                ("补仓", esc(note))]
        if plan:
            first = plan[0]
            info.append(("下一次补仓", f"币价跌到约 <b>{first:.6g}</b> 以下（离现价还要跌 {(1 - first / cur) * 100 if cur else 0:.1f}%），"
                                   "而且要同时满足跌势放缓的指标条件，到了价格不一定马上补"))
            if len(plan) > 1:
                info.append(("之后几次（估算）", "、".join(f"{p:.6g}" for p in plan[1:]) + "（假设上一次正好补在触发价）。每补一次均价会降低、保证金变多，强平价也会跟着往下移，所以这里的价格低于现在的强平价是正常的"))
        out += [f"<tr><th>{esc(k)}</th><td class='l'>{v}</td></tr>" for k, v in info]
        out.append("</table></div>")
        orders = [o for o in t.get("orders") or [] if float(o.get("filled") or 0) > 0]
        if orders:
            out.append("<div class='wrap'><table><tr><th>成交记录</th><th>方向</th><th>价格</th><th>数量</th><th>金额(U)</th></tr>")
            for i, o in enumerate(orders):
                side = "买入" if o.get("ft_order_side") == "buy" else "卖出"
                lab = "首次买入" if i == 0 and side == "买入" else side
                out.append(f"<tr><td>{hm(int(o.get('order_filled_timestamp') or o.get('order_timestamp') or 0) / 1000)}</td><td>{lab}</td>"
                           f"<td>{f2(o.get('safe_price'), '{:.6g}')}</td><td>{f2(o.get('filled'), '{:g}')}</td><td>{f2(o.get('cost'))}</td></tr>")
            out.append("</table></div>")

    # ---------- 4 挂单
    out.append("<h2>4. 正在挂着的单</h2>")
    pend = [(t, o) for t in allst for o in t.get("orders") or [] if o.get("is_open")]
    if pend:
        out.append("<div class='wrap'><table><tr><th>币</th><th>方向</th><th>价格</th><th>挂了多久</th></tr>")
        for t, o in pend:
            out.append(f"<tr><td>{esc(str(t.get('pair')).split('/')[0])}</td><td>{'买入' if o.get('ft_order_side') == 'buy' else '卖出'}</td>"
                       f"<td>{f2(o.get('safe_price'), '{:.6g}')}</td><td>{dur(now - int(o.get('order_timestamp') or 0) / 1000)}</td></tr>")
        out.append("</table></div><p class='sub'>买单 3 分钟、卖单 2 分钟没成交会自动撤掉重下</p>")
    else:
        out.append("<p class='sub'>没有挂单（正常：下单基本马上成交）</p>")

    # ---------- 5 近 7 天
    out.append("<h2>5. 近 7 天每天盈亏（北京时间 8 点为一天的开始）</h2><div class='wrap'><table><tr><th>日期</th><th>平仓笔数</th><th>盈亏</th></tr>")
    for x in d["days"].get("data", []):
        out.append(f"<tr><td>{esc(x.get('date'))}</td><td>{esc(x.get('trade_count'))}</td>"
                   f"<td class='{cls(x.get('abs_profit'))}'>{f2(x.get('abs_profit'), '{:+.2f}')}U</td></tr>")
    out.append("</table></div>")

    # ---------- 6 关键事件
    lines = []
    try:
        with open(run_log, encoding="utf-8") as f:
            lines = [l for l in f.readlines()[-3000:] if any(k in l for k in KEY_WORDS)][-40:]
    except OSError:
        pass
    out.append("<h2>6. 关键事件（开仓、补仓、平仓、报错、检查、改设置，最近 40 条）</h2><pre>" + esc("".join(lines) or "（还没有）") + "</pre>")
    out.append("<script>setTimeout(function(){location.reload()},30000);</script></body></html>")
    return "".join(out)
