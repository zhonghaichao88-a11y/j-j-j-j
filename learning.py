"""
系统学习交易模块
- 记录每笔交易结果到本地JSON
- 统计胜率、平均盈亏、最佳/最差信号
- 参数自适应优化(止损止盈、置信度阈值)
- 信号权重学习(胜率高的形态提高权重)
"""
import json
import os
import time
from typing import Dict, List, Any, Optional
from loguru import logger


class TradeLearner:
    def __init__(self, data_file: str = "trade_learning.json"):
        self.data_file = data_file
        self.trades: List[Dict[str, Any]] = []
        self.signal_stats: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self):
        """加载历史学习数据"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.trades = data.get("trades", [])
                    self.signal_stats = data.get("signal_stats", {})
                logger.info(f"[学习系统] 加载历史数据: {len(self.trades)}笔交易, {len(self.signal_stats)}种信号统计")
        except Exception as e:
            logger.warning(f"[学习系统] 加载数据失败: {e}, 从零开始")

    def _save(self):
        """保存学习数据到本地"""
        try:
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump({
                    "trades": self.trades[-500:],  # 最多保留500笔
                    "signal_stats": self.signal_stats,
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[学习系统] 保存数据失败: {e}")

    def record_open(self, trade_id: str, symbol: str, strategy: str, signal: str,
                    signal_reason: str, direction: str, entry_price: float,
                    amount_usdt: float, confidence: int = 0):
        """记录开仓信号(挂起,等平仓时更新结果)"""
        try:
            trade = {
                "trade_id": trade_id,
                "symbol": symbol,
                "strategy": strategy,
                "signal": signal,
                "signal_reason": signal_reason,
                "direction": direction,
                "entry_price": entry_price,
                "amount_usdt": amount_usdt,
                "confidence": confidence,
                "open_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "close_price": 0,
                "close_time": "",
                "pnl": 0,
                "pnl_pct": 0,
                "close_reason": "",
                "status": "open",
            }
            self.trades.append(trade)
            self._save()
            logger.info(f"[学习系统] 记录开仓: {symbol} {direction} @ {entry_price} | 信号: {signal_reason[:30]}")
        except Exception as e:
            logger.warning(f"[学习系统] 记录开仓失败: {e}")

    def record_close(self, trade_id: str, close_price: float, close_reason: str = ""):
        """记录平仓结果,计算盈亏,更新统计"""
        try:
            # 找到对应的开仓记录
            target = None
            for t in reversed(self.trades):
                if t.get("trade_id") == trade_id and t.get("status") == "open":
                    target = t
                    break
            if not target:
                logger.warning(f"[学习系统] 未找到开仓记录: {trade_id}")
                return

            target["close_price"] = close_price
            target["close_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
            target["close_reason"] = close_reason
            target["status"] = "closed"

            # 计算盈亏
            entry = target["entry_price"]
            if target["direction"] == "long":
                pnl_pct = (close_price - entry) / entry * 100
            else:
                pnl_pct = (entry - close_price) / entry * 100
            target["pnl_pct"] = round(pnl_pct, 2)
            target["pnl"] = round(target["amount_usdt"] * pnl_pct / 100, 2)

            # 更新信号统计
            signal_key = f"{target['strategy']}|{target['signal']}|{target['direction']}"
            if signal_key not in self.signal_stats:
                self.signal_stats[signal_key] = {
                    "strategy": target["strategy"],
                    "signal": target["signal"],
                    "direction": target["direction"],
                    "count": 0, "win": 0, "loss": 0,
                    "total_pnl": 0, "avg_pnl_pct": 0,
                    "win_rate": 0,
                }
            stat = self.signal_stats[signal_key]
            stat["count"] += 1
            if pnl_pct > 0:
                stat["win"] += 1
            else:
                stat["loss"] += 1
            stat["total_pnl"] = round(stat["total_pnl"] + target["pnl"], 2)
            stat["avg_pnl_pct"] = round(
                (stat["avg_pnl_pct"] * (stat["count"] - 1) + pnl_pct) / stat["count"], 2
            )
            stat["win_rate"] = round(stat["win"] / stat["count"] * 100, 1)

            self._save()
            result = "盈利" if pnl_pct > 0 else "亏损"
            logger.info(f"[学习系统] 记录平仓: {target['symbol']} {target['direction']} | {result}{abs(pnl_pct):.2f}% | 信号: {target['signal']} | 累计胜率: {stat['win_rate']}%")
        except Exception as e:
            logger.warning(f"[学习系统] 记录平仓失败: {e}")

    def get_recent_trades(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取最近交易记录"""
        return self.trades[-limit:]

    def get_win_rate(self, strategy: Optional[str] = None, symbol: Optional[str] = None) -> Dict[str, Any]:
        """统计胜率"""
        trades = self.trades
        if strategy:
            trades = [t for t in trades if t.get("strategy") == strategy]
        if symbol:
            trades = [t for t in trades if t.get("symbol") == symbol]
        closed = [t for t in trades if t.get("status") == "closed"]
        if not closed:
            return {"count": 0, "win": 0, "loss": 0, "win_rate": 0, "avg_pnl_pct": 0, "total_pnl": 0}
        wins = [t for t in closed if t.get("pnl_pct", 0) > 0]
        losses = [t for t in closed if t.get("pnl_pct", 0) <= 0]
        avg_pnl = sum(t.get("pnl_pct", 0) for t in closed) / len(closed)
        total_pnl = sum(t.get("pnl", 0) for t in closed)
        return {
            "count": len(closed),
            "win": len(wins),
            "loss": len(losses),
            "win_rate": round(len(wins) / len(closed) * 100, 1),
            "avg_pnl_pct": round(avg_pnl, 2),
            "total_pnl": round(total_pnl, 2),
        }

    def get_best_signals(self, top_n: int = 5) -> List[Dict[str, Any]]:
        """获取胜率最高的信号(至少交易3次)"""
        valid = [s for s in self.signal_stats.values() if s["count"] >= 3]
        valid.sort(key=lambda x: (x["win_rate"], x["avg_pnl_pct"]), reverse=True)
        return valid[:top_n]

    def get_worst_signals(self, top_n: int = 5) -> List[Dict[str, Any]]:
        """获取胜率最低的信号(至少交易3次)"""
        valid = [s for s in self.signal_stats.values() if s["count"] >= 3]
        valid.sort(key=lambda x: (x["win_rate"], x["avg_pnl_pct"]))
        return valid[:top_n]

    def optimize_params(self, current_stop_loss: float, current_take_profit: float,
                        current_confidence_threshold: float) -> Dict[str, Any]:
        """根据近期交易结果自适应优化参数
        返回建议的参数调整
        """
        recent = [t for t in self.trades if t.get("status") == "closed"][-30:]  # 最近30笔
        if len(recent) < 10:
            return {
                "stop_loss_pct": current_stop_loss,
                "take_profit_pct": current_take_profit,
                "confidence_threshold": current_confidence_threshold,
                "reason": "交易样本不足(<10笔), 保持当前参数",
                "adjusted": False,
            }

        wins = [t for t in recent if t.get("pnl_pct", 0) > 0]
        losses = [t for t in recent if t.get("pnl_pct", 0) <= 0]
        win_rate = len(wins) / len(recent) * 100
        avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(abs(t["pnl_pct"]) for t in losses) / len(losses) if losses else 0

        new_sl = current_stop_loss
        new_tp = current_take_profit
        new_conf = current_confidence_threshold
        reasons = []

        # 1. 止损优化: 亏损平均幅度大→放宽止损; 小亏多→收紧止损
        if avg_loss > current_stop_loss * 100 * 1.2:
            new_sl = min(current_stop_loss * 1.2, 0.05)  # 最多5%
            reasons.append(f"平均亏损{avg_loss:.1f}%超过止损{current_stop_loss*100:.0f}%, 放宽止损到{new_sl*100:.0f}%")
        elif avg_loss < current_stop_loss * 100 * 0.5 and len(losses) > len(wins):
            new_sl = max(current_stop_loss * 0.8, 0.01)  # 最少1%
            reasons.append(f"小亏多且平均亏损{avg_loss:.1f}%较小, 收紧止损到{new_sl*100:.0f}%")

        # 2. 止盈优化: 盈利经常没到止盈就回调→收紧止盈; 经常到止盈后还涨→放宽止盈
        near_tp = [t for t in wins if 0 < t["pnl_pct"] < current_take_profit * 100 * 0.8]
        if len(near_tp) > len(wins) * 0.5:
            new_tp = max(current_take_profit * 0.8, 0.02)  # 最少2%
            reasons.append(f"超过一半盈利未到止盈就回调({len(near_tp)}/{len(wins)}), 收紧止盈到{new_tp*100:.0f}%")
        elif all(t["pnl_pct"] >= current_take_profit * 100 for t in wins) and len(wins) >= 3:
            new_tp = min(current_take_profit * 1.2, 0.08)  # 最多8%
            reasons.append(f"盈利经常超过止盈, 放宽止盈到{new_tp*100:.0f}%")

        # 3. 置信度阈值优化: 胜率低→提高阈值(更谨慎); 胜率高→适当降低
        if win_rate < 40:
            new_conf = min(current_confidence_threshold + 10, 90)
            reasons.append(f"近期胜率{win_rate:.0f}%偏低, 提高置信度阈值到{new_conf:.0f}分")
        elif win_rate > 65:
            new_conf = max(current_confidence_threshold - 5, 40)
            reasons.append(f"近期胜率{win_rate:.0f}%较高, 降低置信度阈值到{new_conf:.0f}分")

        adjusted = len(reasons) > 0
        return {
            "stop_loss_pct": round(new_sl, 4),
            "take_profit_pct": round(new_tp, 4),
            "confidence_threshold": round(new_conf, 1),
            "reason": "; ".join(reasons) if reasons else "参数合理, 保持不变",
            "adjusted": adjusted,
            "recent_win_rate": round(win_rate, 1),
            "recent_avg_win": round(avg_win, 2),
            "recent_avg_loss": round(avg_loss, 2),
            "recent_trades": len(recent),
        }

    def get_learning_report(self) -> Dict[str, Any]:
        """生成学习报告"""
        overall = self.get_win_rate()
        best = self.get_best_signals(5)
        worst = self.get_worst_signals(5)
        return {
            "overall": overall,
            "best_signals": best,
            "worst_signals": worst,
            "total_trades": len(self.trades),
            "open_trades": len([t for t in self.trades if t.get("status") == "open"]),
        }


# 全局单例
learner = TradeLearner()
