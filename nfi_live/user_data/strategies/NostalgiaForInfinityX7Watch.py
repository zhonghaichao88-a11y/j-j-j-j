"""NFI 记录器：继承原版 NostalgiaForInfinityX7，买卖逻辑一行都不改。
原版每次做「卖不卖」(custom_exit) 和「补不补 / 分批卖不卖」(adjust_trade_position) 判断时，先让原版算完，
再把这次的输入和结论原样记下来，写到 user_data/nfi_watch.json，给中文详细工作状态页显示。
记录过程中出任何错都直接忽略，返回值永远是原版的返回值。"""
import json
import os
import time

from NostalgiaForInfinityX7 import NostalgiaForInfinityX7

COLS = ("close", "RSI_14", "RSI_14_1h", "RSI_3", "RSI_3_15m", "AROONU_14", "AROONU_14_15m",
        "EMA_26", "EMA_200", "BBU_20_2.0", "protections_long_global")
KEEP = 24               # 每单保留最近 24 次（按 5 分钟K线算，约 2 小时）的判断记录
FLUSH_EVERY = float(os.environ.get("NFIW_FLUSH", 15))   # 最多每 15 秒写一次文件（NFIW_FLUSH 只在开发验证时用）


class NostalgiaForInfinityX7Watch(NostalgiaForInfinityX7):
  _nfiw = None
  _nfiw_flushed = 0.0

  # ------------------------------------------------------------------ 原版判断 + 记录
  def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
    res = super().custom_exit(pair, trade, current_time, current_rate, current_profit, **kwargs)
    try:
      self._nfiw_note(trade, current_time, current_rate, "exit", res)
    except Exception:  # noqa: BLE001  记录出错不影响交易
      if os.environ.get("NFIW_DEBUG"):
        import traceback; traceback.print_exc()
    return res

  def adjust_trade_position(self, trade, current_time, current_rate, current_profit, min_stake, max_stake,
                            current_entry_rate, current_exit_rate, current_entry_profit, current_exit_profit, **kwargs):
    res = super().adjust_trade_position(trade, current_time, current_rate, current_profit, min_stake, max_stake,
                                        current_entry_rate, current_exit_rate, current_entry_profit,
                                        current_exit_profit, **kwargs)
    try:
      self._nfiw_note(trade, current_time, current_rate, "adjust", res)
    except Exception:  # noqa: BLE001
      if os.environ.get("NFIW_DEBUG"):
        import traceback; traceback.print_exc()
    return res

  # ------------------------------------------------------------------ 记录
  def _nfiw_enabled(self):
    if os.environ.get("NFIW_BACKTEST"):          # 只在开发验证时打开：回测里也走一遍记录代码
      return True
    return self.dp is not None and self.dp.runmode.value in ("live", "dry_run")

  def _nfiw_note(self, trade, current_time, current_rate, kind, res):
    if not self._nfiw_enabled():
      return
    if self._nfiw is None:
      self._nfiw = {}
    df, _ = self.dp.get_analyzed_dataframe(trade.pair, self.timeframe)
    if df is None or len(df) < 1:
      return
    last = df.iloc[-1]
    candle = str(last["date"])[:16]
    ind = {}
    for c in COLS:
      if c in last.index:
        v = last[c]
        try:
          ind[c] = bool(v) if c == "protections_long_global" else round(float(v), 8)
        except (TypeError, ValueError):
          ind[c] = None
    _, entries, exits = self.filled_order_snapshot(trade)
    prof = None
    if entries:
      p_stake, p_ratio, p_cur, p_init = self.calc_total_profit(trade, entries, exits, current_rate)
      prof = dict(stake=round(p_stake, 6), ratio=round(p_ratio, 6), cur_stake_ratio=round(p_cur, 6), init_ratio=round(p_init, 6))
    if kind == "exit":
      decision = res if res else None
    elif res is None:
      decision = None
    elif isinstance(res, tuple):
      decision = {"amount": float(res[0]) if res[0] is not None else None, "tag": res[1] if len(res) > 1 else None}
    else:
      decision = {"amount": float(res), "tag": None}
    rec = self._nfiw.setdefault(str(trade.id), {"pair": trade.pair, "exit": [], "adjust": []})
    rec["pair"] = trade.pair
    rec["enter_tag"] = trade.enter_tag
    rec["seen"] = time.time()
    hist = rec[kind]
    row = {"t": current_time.isoformat()[:19], "candle": candle, "rate": current_rate, "profit": prof,
           "max_rate": float(trade.max_rate or 0), "ind": ind, "decision": decision}
    # 同一根K线、结论没变 → 只更新最后一条（每几秒都会判断一次，别把记录刷满）
    if hist and hist[-1]["candle"] == candle and hist[-1]["decision"] == decision:
      hist[-1] = row
    else:
      hist.append(row)
      del hist[:-KEEP]
    if kind == "adjust" and decision is not None:
      acts = rec.setdefault("actions", [])
      acts.append(row)
      del acts[:-50]
    if kind == "exit" and decision is not None:
      rec["exit_signal"] = row
    self._nfiw_flush()

  def _nfiw_flush(self):
    now = time.time()
    if now - self._nfiw_flushed < FLUSH_EVERY or (os.environ.get("NFIW_BACKTEST") and not os.environ.get("NFIW_DUMP")):
      return
    self._nfiw_flushed = now
    for k in [k for k, v in self._nfiw.items() if now - v.get("seen", now) > 86400]:
      del self._nfiw[k]                         # 一天没再判断过的（已平仓）清掉
    out = {
      "updated": now,
      "trades": self._nfiw,
      "rules": {                                # 直接从策略里读，不写死
        "rebuy_tags": list(self.long_rebuy_mode_tags),
        "rebuy_thresholds": list(self.system_v3_rebuy_mode_thresholds_futures),
        "rebuy_stakes": list(self.system_v3_rebuy_mode_stakes_futures),
        "mode_tags": {
          "正常模式": list(self.long_normal_mode_tags), "拉升模式": list(self.long_pump_mode_tags),
          "快速模式": list(self.long_quick_mode_tags), "抄底补仓模式": list(self.long_rebuy_mode_tags),
          "高收益模式": list(self.long_high_profit_mode_tags), "急跌模式": list(self.long_rapid_mode_tags),
          "网格模式": list(self.long_grind_mode_tags), "大币模式": list(self.long_top_coins_mode_tags),
          "短线模式": list(self.long_scalp_mode_tags),
        },
      },
    }
    path = os.path.join(str(self.config["user_data_dir"]), "nfi_watch.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
      json.dump(out, f, ensure_ascii=True, default=str)
    os.replace(tmp, path)
