# ALPHA-X V7.5 Pine 策略扩展

此包在 V7.4 基础上扩展 Pine 兼容子集和策略退出处理。它不是 TradingView Pine 的完整替代运行器，也没有通过交易所真实下单验收。程序仍使用 V7 的仓位管理、交易所保护单和退出控制器。

## 本次新增

- 补齐并修正常用 `ta.*` / `math.*` 函数，包括累计、统计、成交量加权、pivot、DMI/ADX、Supertrend 等；DMI 采用 Wilder 平滑。
- 支持 `plot` / `plotshape` 的整数 `offset`（-5000 至 5000）。图形可在确认后回画；策略事件的 `known_at` 不会回写到更早的 K 线。
- 支持 `strategy.close_all()`。
- 支持 `strategy.exit()` 的绝对价格 `stop` / `limit` 挂单模拟。订单从下一根 K 线开始检查；同一根 K 线同时触及止损和止盈时保守按止损处理。
- 修复 Pine 输出结果缓存、上升/下降判定和绘图偏移测试；脚本出错时拒绝执行，不静默忽略。

## 已支持范围

脚本声明与输入：`indicator`、`study`、`strategy`，`input.int/float/bool/string/source/timeframe/symbol`；基本 OHLCV、时间与 bar_index、历史索引、算术比较、if/else if、局部作用域、局部 `var`、元组解构。

绘图与信号：`plot`、`plotshape`、`hline`、`alertcondition`、`strategy.entry`、`strategy.close`、`strategy.close_all`；`strategy.exit` 仅支持 `from_entry` 加绝对 `stop` / `limit`，不支持部分仓位。

技术函数的实现清单以 `alpha_v7_pine.py` 的 `CALLS` 和对应运行分支为准。包括 SMA/EMA/RMA/WMA/HMA/RSI/ATR、MACD/Bollinger、最高最低、交叉与变化、Stochastic、线性回归、pivot、VWMA、DMI/Supertrend、常用统计函数与数学函数。

## 仍不兼容

- Pine v4/v5/v6 完整语言语义；用户函数、循环、数组/矩阵/map、库/import、`request.security()`/多周期请求、绘图对象和完整绘图样式仍未实现。
- TradingView 完整策略撮合器：金字塔、逐 tick、收盘撮合、部分退出、`profit/loss` tick 参数、跟踪止损、OCA 与佣金/滑点参数均不支持。
- Pine `strategy.exit` 的止损/止盈只是收盘 K 线 OHLC 的策略退出信号；实盘由 V7 的报价监控、交易所保护单和原生退出控制器执行，不能视为 Pine 订单已在交易所挂出或按 Pine 指定价格成交。
- `plot` 偏移只改图形显示位置，不改信号确认时间。实时策略仅使用已收盘 5 分钟 K 线和最近 288 根数据，递归变量/指标受固定窗口起点影响。
- 缠论仍是已标注规则集的量化实现，不代表完整覆盖所有解释流派；真实交易所订单、成交、断线恢复和浏览器实际渲染没有在本包中验收。

## 使用

用 `START_V7.bat` 启动，在 `/tv` 的 Pine 页面导入并检查脚本。先用模拟模式观察信号与 V7 原生风险控制；保存 Pine 配置不会自动下单。该程序可以保留用户 `.env` 中的真实交易配置，但请先确认 API 权限、合约、杠杆、仓位与保护单，再由页面显式启动。空白打包不包含用户密钥、账户数据库、持仓状态或本机 `.env`。

`examples/V75_PINE_STRATEGY_EXIT.pine` 演示最小策略入场和绝对价格退出。离线回归结果见 `V75_TEST_RESULTS.txt`，行情样例回放见 `V75_REPLAY_RESULTS.json`；回放样本是包内 OHLC 数据，不是交易所真实成交记录，不可据此推断胜率。
