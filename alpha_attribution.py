"""ALPHA-X 10.0 trade attribution and online diagnostics.
Keeps entry/exit economics separate from model research labels.
"""
from __future__ import annotations
import math, statistics, time
from typing import Any, Dict, Iterable


def _clean(xs):
    return [float(x) for x in xs if x is not None and math.isfinite(float(x))]


def trade_metrics(side: str, entry: float, path: Iterable[float], exit: float, qty: float,
                  entry_fee: float = 0.0, exit_fee: float = 0.0, funding: float = 0.0,
                  slippage: float = 0.0) -> Dict[str, Any]:
    p=_clean(path); e=float(entry); x=float(exit); q=float(qty)
    if e<=0 or x<=0 or q<=0: raise ValueError("invalid trade inputs")
    favorable=max(p+[e]) if side=="long" else min(p+[e])
    adverse=min(p+[e]) if side=="long" else max(p+[e])
    mfe=((favorable-e)/e) if side=="long" else ((e-favorable)/e)
    mae=((adverse-e)/e) if side=="long" else ((e-adverse)/e)
    gross=(x-e)*q if side=="long" else (e-x)*q
    net=gross-float(entry_fee)-float(exit_fee)-float(funding)-float(slippage)
    return {"entry":e,"exit":x,"side":side,"qty":q,"gross_pnl":gross,"net_pnl":net,
            "entry_fee":float(entry_fee),"exit_fee":float(exit_fee),"funding":float(funding),
            "slippage":float(slippage),"mfe_pct":mfe*100,"mae_pct":mae*100,
            "holding_path_points":len(p),"timestamp":time.time()}


def summary(trades):
    rows=[t for t in trades if isinstance(t,dict) and math.isfinite(float(t.get("net_pnl",0)))]
    p=[float(t["net_pnl"]) for t in rows]
    if not p:return {"trades":0,"net_pnl":0.0,"profit_factor":0.0,"win_rate":0.0,"mean":0.0,"median":0.0}
    wins=[x for x in p if x>0]; losses=[x for x in p if x<0]
    pf=sum(wins)/max(-sum(losses),1e-12) if losses else (99.0 if wins else 0.0)
    return {"trades":len(p),"net_pnl":sum(p),"profit_factor":pf,"win_rate":len(wins)/len(p),
            "mean":statistics.mean(p),"median":statistics.median(p),
            "avg_mfe_pct":statistics.mean([float(t.get("mfe_pct",0)) for t in rows]),
            "avg_mae_pct":statistics.mean([float(t.get("mae_pct",0)) for t in rows])}


def _num(v, default=0.0):
    try:
        x=float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _bucket(rows, key):
    out={}
    for r in rows:
        k=str(r.get(key) or "UNKNOWN")
        x=out.setdefault(k, {"trades":0,"net_pnl":0.0,"wins":0,"losses":0})
        pnl=_num(r.get("net_pnl"))
        x["trades"]+=1; x["net_pnl"]+=pnl
        if pnl>0: x["wins"]+=1
        elif pnl<0: x["losses"]+=1
    for x in out.values():
        x["win_rate"]=x["wins"]/x["trades"] if x["trades"] else 0.0
    return sorted(out.items(), key=lambda kv: kv[1]["net_pnl"], reverse=True)


def _label_cn(value, kind="generic"):
    """把归因中常见的机器字段转换成用户可读中文；未知值保留原值，避免丢失实盘事实。"""
    v=str(value or "").strip()
    maps={
        "side":{"long":"做多","short":"做空","buy":"买入","sell":"卖出","LONG":"做多","SHORT":"做空"},
        "reason":{
            "TP":"止盈","TP1":"止盈1","TP2":"止盈2","SL":"止损","TRAILING":"移动止盈/追踪止损",
            "TRAIL":"移动止盈/追踪止损","TIME":"时间退出","TIMEOUT":"超时退出","KILL":"风控强制退出",
            "RISK":"风险退出","MANUAL":"手动平仓","CLOSE":"平仓","RECONCILE":"交易所持仓消失/对账平仓",
            "EXCHANGE":"交易所平仓","UNKNOWN":"未知原因"
        },
        "source":{"state_attribution":"本地实盘归因","ledger":"实盘账本","live_trade":"实盘交易记录","exchange":"OKX真实成交"},
    }
    return maps.get(kind,{}).get(v, maps.get(kind,{}).get(v.upper(), v or "未知"))


def _cn_key(key):
    maps={
        "model":"模型", "pipeline":"执行前检查链", "signal":"信号", "confidence":"置信度",
        "raw_confidence":"原始置信度", "signal_tier":"信号等级", "committee":"策略委员会",
        "committee_score":"委员会评分", "agreement":"一致度", "market_context":"市场环境",
        "regime":"市场状态", "stress":"压力水平", "no_trade":"禁止交易", "reasons":"原因",
        "quality":"数据质量", "liquidity":"流动性", "capacity":"容量", "impact":"冲击成本",
        "risk":"风险检查", "route":"执行路由", "gate":"总闸门", "checks":"检查项",
        "reconciliation":"持仓对账", "recovery":"恢复状态", "clock":"时钟检查", "audit":"审计检查",
        "planned_notional":"计划下单金额", "stop_pct":"止损比例", "tp":"止盈价格", "sl":"止损价格",
        "entry":"入场价格", "exit":"出场价格", "qty":"成交数量", "gross_pnl":"毛盈亏",
        "net_pnl":"净盈亏", "entry_fee":"开仓手续费", "exit_fee":"平仓手续费", "funding":"资金费",
        "slippage":"滑点", "mfe_pct":"最大有利波动", "mae_pct":"最大不利波动",
        "source":"数据来源", "reason":"退出原因", "symbol":"交易对", "side":"方向",
    }
    return maps.get(str(key), str(key))

def _cn_value(v, key=""):
    if isinstance(v, bool): return "是" if v else "否"
    if isinstance(v, dict): return {_cn_key(k):_cn_value(x,k) for k,x in v.items()}
    if isinstance(v, list): return [_cn_value(x,key) for x in v]
    if key in ("signal","model_sig","committee_sig"):
        return _label_cn(v,"side") if str(v).upper() in ("LONG","SHORT","FLAT","BUY","SELL") else str(v)
    if key in ("regime",):
        return {"TREND":"趋势","RANGE":"震荡","UNKNOWN":"未知","BULL":"偏多","BEAR":"偏空"}.get(str(v).upper(),str(v))
    return v

def _decision_cn(open_row):
    """把开仓时已记录的模型/委员会/执行检查快照转成中文；不补造缺失数据。"""
    if not isinstance(open_row,dict): return {"available":False,"reason":"没有找到对应的开仓记录"}
    pipe=open_row.get("pipeline") or open_row.get("pretrade_pipeline") or {}
    out={"available":True,"模型":{},"执行前检查":{}}
    for k in ("model","signal","confidence","raw_confidence","signal_tier","entry_threshold","directional_margin"):
        if k in open_row: out["模型"][_cn_key(k)]=_cn_value(open_row.get(k),k)
    # 兼容旧记录：模型字段没有单独存时，使用 pipeline/关联字段中已有事实。
    for k in ("signal","confidence","raw_confidence","signal_tier"):
        if k not in out["模型"] and k in pipe: out["模型"][_cn_key(k)]=_cn_value(pipe.get(k),k)
    committee=open_row.get("strategy_committee") or pipe.get("strategy_committee") or {}
    if isinstance(committee,dict) and committee:
        out["模型"]["策略委员会"]=_cn_value(committee,"committee")
    ctx=open_row.get("market_context") or pipe.get("market_context") or {}
    if isinstance(ctx,dict) and ctx:
        out["模型"]["市场环境"]=_cn_value(ctx,"market_context")
    for k in ("quality","liquidity","capacity","impact","risk","route","gate","reconciliation","recovery","clock","audit"):
        if k in pipe: out["执行前检查"][_cn_key(k)]=_cn_value(pipe.get(k),k)
    if "planned_notional" in pipe: out["执行前检查"]["计划下单金额"]=_cn_value(pipe.get("planned_notional"),"planned_notional")
    out["数据完整性"]="仅展示开仓时实际记录的字段；缺失字段不推测"
    return out

def _diagnose(rows, summary_data, cost_breakdown):
    """基于真实已记录事实生成可解释的赚/亏原因摘要，不虚构模型判断。"""
    if not rows:
        return {"overall":"当前没有足够的已完成交易数据，暂时无法总结为什么赚或亏。", "earn_reasons":[], "loss_reasons":[], "limitations":[]}
    winners=[r for r in rows if _num(r.get("net_pnl"))>0]
    losers=[r for r in rows if _num(r.get("net_pnl"))<0]
    reason={k:v for k,v in _bucket(rows,"reason")}
    side={k:v for k,v in _bucket(rows,"side")}
    earn=[]; loss=[]
    if summary_data.get("net_pnl",0)>0:
        overall=f"这段统计期整体盈利 {summary_data['net_pnl']:.2f} USDT，主要由盈利交易贡献；下面列出贡献最大的方向和退出方式。"
    elif summary_data.get("net_pnl",0)<0:
        overall=f"这段统计期整体亏损 {abs(summary_data['net_pnl']):.2f} USDT，亏损主要来自亏损交易与交易成本；下面列出最大的拖累来源。"
    else:
        overall="这段统计期基本持平。"
    for name,val in sorted(reason.items(), key=lambda kv: kv[1]["net_pnl"], reverse=True):
        cn=_label_cn(name,"reason")
        if val["net_pnl"]>0: earn.append(f"{cn}贡献 {val['net_pnl']:.2f} USDT（{val['trades']}笔，胜率 {val['win_rate']*100:.1f}%）")
        elif val["net_pnl"]<0: loss.append(f"{cn}拖累 {abs(val['net_pnl']):.2f} USDT（{val['trades']}笔，胜率 {val['win_rate']*100:.1f}%）")
    for name,val in sorted(side.items(), key=lambda kv: kv[1]["net_pnl"], reverse=True)[:2]:
        cn=_label_cn(name,"side")
        if val["net_pnl"]>0: earn.append(f"{cn}方向净贡献 {val['net_pnl']:.2f} USDT")
        elif val["net_pnl"]<0: loss.append(f"{cn}方向净亏损 {abs(val['net_pnl']):.2f} USDT")
    costs=cost_breakdown.get("total_cost",0)
    if costs>0: loss.append(f"交易成本合计 {costs:.2f} USDT（手续费/资金费/滑点中有实际记录的部分）")
    if winners:
        best=max(winners,key=lambda r:_num(r.get("net_pnl")))
        earn.append(f"单笔最大盈利 {best.get('net_pnl',0):.2f} USDT，交易对 {best.get('symbol') or '未知'}，退出方式 {_label_cn(best.get('reason'),'reason')}")
    if losers:
        worst=min(losers,key=lambda r:_num(r.get("net_pnl")))
        loss.append(f"单笔最大亏损 {abs(_num(worst.get('net_pnl'))):.2f} USDT，交易对 {worst.get('symbol') or '未知'}，退出方式 {_label_cn(worst.get('reason'),'reason')}")
    limitations=[]
    if not any(r.get("funding") not in (None,"") for r in rows): limitations.append("资金费没有完整记录，未做推测")
    if not any(r.get("slippage") not in (None,"") for r in rows): limitations.append("滑点没有完整记录，未做推测")
    if not any(r.get("model") or r.get("pipeline") for r in rows): limitations.append("逐笔模型名称/置信度未完整进入平仓归因，因此不虚构模型结论")
    return {"overall":overall,"earn_reasons":earn[:8],"loss_reasons":loss[:8],"limitations":limitations}


def full_report(attributions=None, live_trades=None, ledger_events=None, exchange_fills=None, limit=300):
    """Read-only production attribution report.

    Sources are exchange-confirmed close attribution, ALPHA-X live trade journal,
    immutable ledger events, and optional OKX fills. This function never places,
    cancels, amends, or blocks an order.
    """
    attributions=list(attributions or [])
    live=list(live_trades or [])
    events=list(ledger_events or [])
    fills=list(exchange_fills or [])

    # Prefer explicit close attribution. Fall back to ledger attribution events.
    rows=[]; seen=set()
    def add(r, source):
        if not isinstance(r, dict): return
        pnl=r.get("net_pnl")
        if pnl is None and isinstance(r.get("attribution"), dict): pnl=r["attribution"].get("net_pnl")
        if pnl is None: return
        rr=dict(r.get("attribution") or r)
        rr["source"]=source
        rr["symbol"]=str(r.get("symbol") or rr.get("symbol") or "")
        rr["side"]=str(rr.get("side") or r.get("side") or "")
        rr["reason"]=str(rr.get("reason") or r.get("reason") or "")
        rr["time"]=_num(r.get("time") or r.get("ts") or rr.get("timestamp"), 0)
        rr["order_id"]=str(r.get("order_id") or rr.get("order_id") or "")
        rr["qty"]=_num(rr.get("qty") or r.get("filled"), 0)
        rr["entry"]=_num(rr.get("entry") or r.get("average"), 0)
        rr["exit"]=_num(rr.get("exit") or r.get("average"), 0)
        key=(rr["symbol"], rr["order_id"], rr["time"], round(_num(rr.get("net_pnl")), 10))
        if key in seen: return
        seen.add(key); rows.append(rr)
    for r in attributions: add(r, "state_attribution")
    for e in events:
        if str(e.get("kind")) in ("TRADE_ATTRIBUTION", "LIVE_CLOSE", "LIVE_CLOSE_RECONCILED"):
            add(e.get("payload") or {}, "ledger")
    # live_trades close rows may contain attribution not yet copied to STATE.attribution.
    for r in live:
        if str(r.get("action"))=="CLOSE" and isinstance(r.get("attribution"),dict): add(r, "live_trade")
    # 关联同一交易对最近一次已记录的开仓快照，让平仓归因能回答“当时为什么做”。
    opens=[]
    for t in live:
        if str(t.get("action")) in ("OPEN","OPEN_RECOVERED"):
            opens.append(t)
    opens.sort(key=lambda x:_num(x.get("time"),0))
    for r in rows:
        candidates=[o for o in opens if str(o.get("symbol") or "")==str(r.get("symbol") or "") and _num(o.get("time"),0)<=_num(r.get("time"),0)]
        if candidates:
            o=candidates[-1]
            r["decision_attribution"]=_decision_cn(o)
            r["entry_order_id"]=str(o.get("order_id") or "")
        else:
            r["decision_attribution"]={"available":False,"reason":"没有找到对应的历史开仓决策快照，未虚构模型原因"}
    rows=sorted(rows, key=lambda r:r.get("time",0), reverse=True)[:max(1,int(limit))]

    # 交易所成交核验：按订单号汇总真实成交价格/数量/手续费；不覆盖原有归因，只增加核验字段。
    fills_by_order={}
    for f in fills:
        oid=str(f.get("order_id") or f.get("order") or f.get("ord_id") or "")
        if not oid: continue
        g=fills_by_order.setdefault(oid,{"qty":0.0,"cost":0.0,"fee":0.0,"weighted_px":0.0,"fills":0})
        qty=_num(f.get("amount") or f.get("qty") or f.get("fill_sz"))
        px=_num(f.get("price") or f.get("fill_px"))
        g["qty"]+=abs(qty); g["cost"]+=abs(_num(f.get("cost"))) ; g["fee"]+=_num(f.get("fee")); g["fills"]+=1
        g["weighted_px"]+=px*abs(qty)
    for r in rows:
        oid=str(r.get("order_id") or "")
        if oid and oid in fills_by_order:
            g=fills_by_order[oid]
            r["exchange_truth"]={"available":True,"成交笔数":g["fills"],"成交数量":g["qty"],"成交金额":g["cost"],"手续费":g["fee"],"成交均价":(g["weighted_px"]/g["qty"] if g["qty"] else 0.0),"说明":"来自OKX真实成交记录；仅用于核验，不覆盖原有归因"}
        else:
            r["exchange_truth"]={"available":False,"说明":"未找到与该平仓订单号直接匹配的OKX成交记录，未推测"}

    s=summary(rows)
    gross=sum(_num(r.get("gross_pnl")) for r in rows)
    fees=sum(_num(r.get("entry_fee"))+_num(r.get("exit_fee")) for r in rows)
    funding=sum(_num(r.get("funding")) for r in rows)
    slippage=sum(_num(r.get("slippage")) for r in rows)
    winners=[r for r in rows if _num(r.get("net_pnl"))>0]
    losers=[r for r in rows if _num(r.get("net_pnl"))<0]
    peak=equity=0.0; max_dd=0.0
    for r in sorted(rows, key=lambda x:x.get("time",0)):
        equity += _num(r.get("net_pnl")); peak=max(peak,equity); max_dd=max(max_dd,peak-equity)

    reason=_bucket(rows,"reason")
    symbols=_bucket(rows,"symbol")
    sides=_bucket(rows,"side")
    # Pipeline/model fields are nested in open records and therefore kept as labels where present.
    models={}
    for t in live:
        if str(t.get("action")) not in ("OPEN","OPEN_RECOVERED"): continue
        pipe=t.get("pipeline") or t.get("pretrade_pipeline") or {}
        label=str((pipe.get("model") if isinstance(pipe,dict) else "") or t.get("model") or "UNKNOWN")
        models.setdefault(label,0); models[label]+=1

    exchange_fee=sum(_num(f.get("fee")) for f in fills)
    exchange_cost=sum(abs(_num(f.get("cost"))) for f in fills)
    diagnosis=_diagnose(rows, {**s,"gross_pnl":gross,"fees":fees,"funding":funding,"slippage":slippage,"max_drawdown":max_dd}, {"fees":fees,"funding":funding,"slippage":slippage,"total_cost":fees+funding+slippage})
    for r in rows:
        r["side_cn"]=_label_cn(r.get("side"),"side")
        r["reason_cn"]=_label_cn(r.get("reason"),"reason")
        r["source_cn"]=_label_cn(r.get("source"),"source")
    return {
        "version":"ALPHA-X-TRADE-ATTRIBUTION-FULL-1.2",
        "read_only":True,
        "data_source":{"state_attribution":len(attributions),"live_trade_records":len(live),"ledger_events":len(events),"exchange_fills":len(fills)},
        "summary":{**s,"gross_pnl":gross,"fees":fees,"funding":funding,"slippage":slippage,"max_drawdown":max_dd,
                   "avg_win":sum(_num(x.get("net_pnl")) for x in winners)/len(winners) if winners else 0.0,
                   "avg_loss":sum(_num(x.get("net_pnl")) for x in losers)/len(losers) if losers else 0.0},
        "cost_breakdown":{"fees":fees,"funding":funding,"slippage":slippage,"total_cost":fees+funding+slippage},
        "by_exit_reason":[{"name":k,"name_cn":_label_cn(k,"reason"),**v} for k,v in reason],
        "by_symbol":[{"name":k,"name_cn":k,**v} for k,v in symbols],
        "by_side":[{"name":k,"name_cn":_label_cn(k,"side"),**v} for k,v in sides],
        "diagnosis":diagnosis,
        "model_open_counts":models,
        "model_open_counts_cn":[{"name":k,"name_cn":k,"count":v} for k,v in models.items()],
        "trades":rows,
        "exchange":{"fills":len(fills),"fee_total":exchange_fee,"notional_cost_total":exchange_cost,
                     "recent_fills":fills[:100]},
        "completeness":{"has_closed_attribution":bool(rows),
                         "has_exchange_fills":bool(fills),
                         "warning":None if rows else "当前没有可核验的已完成交易归因；实盘启动后由成交/平仓事件自动生成。"},
    }
