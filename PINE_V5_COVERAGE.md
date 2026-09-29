> **V7.6.1 修复说明优先于下面旧覆盖表。** 旧表中的完成百分比已过期。
> 本次实测：284个脚本，222个编译、209个运行成功（73.6%）；运行成功不等于TradingView结果一致。
> strategy.order净额订单、stop-limit组合单未实现，明确报错。追踪必须有trail_points和trail_offset。
> V7自动交易仅接市价方向/整仓退出信号，跟随主级别5m/15m/1h，由V7负责仓位和保护。
> Pine挂单、部分平仓、加仓、strategy.exit、依赖模拟权益/持仓的脚本可预览，但暂不能自动交易。
> 绘图对象已接前端；表格在图表下方显示。复杂样式与TradingView仍有差异。

# Pine v5 覆盖矩阵 (Coverage Matrix)

> 状态标记：✅ done / 🟡 partial / 🔴 blocked (附原因) / ⬜ not started
> 最后更新：架构重构完成，基线 148 测试全绿

## 1. 语言结构 (Language Constructs)

| 特性 | 状态 | 说明 |
|------|------|------|
| 变量声明 (float/int/bool/string) | ✅ | |
| var 持久变量 | ✅ | 每bar首次初始化 |
| varip | 🔴 | 需逐tick数据；本解释器收盘K线模式下显式报错，不静默降级为 var |
| 重新赋值 := | ✅ | |
| 复合赋值 +=,-=,*=,/=,%= | ✅ | 编译期展开 |
| 元组解构 [a,b] = ... | ✅ | |
| 三元表达式 ? : | ✅ | 双分支流式求值（与 and/or 一致），全局可含 ta.* 指标；if/while/switch 条件内仍禁止 ta.* |
| if/else/else if | ✅ | |
| for 循环 (to/by) | ✅ | 有界10000次 |
| break/continue | ✅ | |
| while 循环 | ✅ | 有界10000次，支持 break/continue |
| switch 语句 | ✅ | 表达式分支 + 条件分支两种形式，含 default |
| 单表达式用户函数 | ✅ | |
| 多语句用户函数 | ✅ | 缩进块体，var/if/for/while/赋值，末表达式返回；递归深度>32报错 |
| 递归函数 | 🟡 | 深度>32 fail-closed |
| method 调用语法 obj.method() | ✅ | 通用机制，array.* 已验证 |
| UDT (type/struct) | ✅ | type 声明、.new()/直接构造、字段访问 |
| import 本地库 | ✅ | 本地 .pine，支持 as 别名、循环导入检测 |
| import TradingView 云端库 | 🔴 | 网络不可达，显式报错 |
| export | ⬜ | |

## 2. 类型系统 (Type System)

| 类型 | 状态 | 说明 |
|------|------|------|
| int/float/bool/string/color | ✅ | |
| array<T> | ✅ | 常用操作 |
| matrix<T> | ⬜ | |
| map<K,V> | ⬜ | |
| UDT 实例 | ✅ | |
| line/label/box/table/polyline | ✅ | V8 对象状态模型（draw_objects/draw_events） |
| na 处理 | ✅ | nz/na |
| 自动类型转换 | 🟡 | 基本转换 |

## 3. 运算符 (Operators)

| 运算符 | 状态 |
|--------|------|
| + - * / % | ✅ |
| > < >= <= == != | ✅ |
| and or not | ✅ |
| ? : 三元 | ✅ |
| [] 历史引用 | ✅ |
| := 重新赋值 | ✅ |
| += -= *= /= %= | ✅ |
| . 成员访问 | 🟡 |

## 4. 内置变量 (Built-in Variables)

| 变量 | 状态 | 说明 |
|------|------|------|
| open/high/low/close/volume | ✅ | |
| time | ✅ | |
| bar_index | ✅ | |
| hl2/hlc3/ohlc4 | ✅ | |
| timeframe.period/multiplier | ✅ | |
| timeframe.isintraday/isdaily | ✅ | |
| timeframe.isweekly/ismonthly | ⬜ | |
| syminfo.ticker/tickerid | ✅ | |
| syminfo.* 其他 | 🔴 | 数据源未提供 |
| barstate.isconfirmed | ✅ | 始终true(收盘模式) |
| barstate.* 其他 | 🔴 | 逐tick模式不支持 |
| strategy.long/short | ✅ | |
| strategy.* 订单属性 | 🟡 | 部分 |
| currency.* | 🟡 | USD only |
| barmerge.* | ✅ | gaps/lookahead 常量 |

## 5. ta 命名空间 (Technical Analysis)

| 函数 | 状态 |
|------|------|
| ta.sma/ema/rma/wma/hma | ✅ |
| ta.rsi | ✅ |
| ta.atr/tr | ✅ |
| ta.macd | ✅ |
| ta.bb (Bollinger) | ✅ |
| ta.stoch | ✅ |
| ta.dmi/adx | ✅ |
| ta.supertrend | ✅ |
| ta.highest/lowest | ✅ |
| ta.stdev/variance/dev | ✅ |
| ta.sum/cum | ✅ |
| ta.avg/median/mode | ✅ |
| ta.crossover/crossunder/cross | ✅ |
| ta.change/roc | ✅ |
| ta.rising/falling | ✅ |
| ta.vwma | ✅ |
| ta.correlation/covariance | ✅ |
| ta.pivothigh/pivotlow | ✅ |
| ta.barssince/valuewhen | ✅ |
| ta.linreg | ✅ |
| ta.wpr (Williams %R) | ✅ | (highest-high − close)/(highest−low)×−100 |
| ta.cci | ✅ | (TP−SMA)/(0.015×mean dev) |
| ta.mfi | ✅ | 用 typical_price×volume 的资金流比率 |
| ta.obv | ✅ | 状态ful累积，synthetic存储 |
| ta.sar (Parabolic SAR) | ✅ | Wilder SAR，状态ful |
| ta.kc (Keltner Channels) | ✅ | [basis,upper,lower]，EMA±mult×RMA(TR) |
| ta.donchian | ✅ | [upper,lower,basis] |
| ta.highestbars/lowestbars | ✅ | 返回负偏移量 |
| ta.momentum | ✅ | close − close[length] |
| ta.percentile_linear_interpolation | ⬜ |
| ta.percentrank | ✅ | 窗口内低于当前值的百分比 |
| ta.slope | ✅ | 线性回归斜率 np.polyfit |
| ta.ema 已含 | ✅ |

## 6. math 命名空间

| 函数 | 状态 |
|------|------|
| math.abs/max/min/avg/sum | ✅ |
| math.sign | ✅ |
| math.sqrt/pow | ✅ |
| math.log/log10/exp | ✅ |
| math.tanh | ✅ |
| math.round/floor/ceil | ✅ |
| math.sin/cos/tan | ✅ |
| math.asin/acos/atan | ✅ |
| math.sinh/cosh | ✅ |
| math.atan2 | ✅ |
| math.mod | ✅ |
| math.random | ✅ | bar_index 种子，可复现 |
| math.avg (多参数) | ✅ |
| math.max/min (多参数) | ✅ |

## 7. str 命名空间

| 函数 | 状态 |
|------|------|
| str.tostring | ✅ |
| str.tonumber | ✅ |
| str.length | ✅ |
| str.substring | ✅ |
| str.contains | ✅ |
| str.startswith/endswith | ✅ |
| str.replace | ✅ |
| str.lower/upper | ✅ |
| str.split | ✅ |
| str.format | ✅ | {0}/{1} 索引替换 |
| str.concat | ✅ |

## 8. array 命名空间

| 函数 | 状态 |
|------|------|
| array.new/new_* | ✅ |
| array.from | ✅ |
| array.size/get/set | ✅ |
| array.push/pop/shift/unshift | ✅ |
| array.clear/copy/concat/reverse | ✅ |
| array.sum/avg/max/min | ✅ |
| array.indexof/includes | ✅ |
| array.remove | ✅ |
| array.insert | ✅ |
| array.fill | ✅ |
| array.join | ✅ |
| array.sort | ✅ | order.ascending/descending 常量已注入 |
| array.slice | ✅ |
| array.first/last | ✅ |

## 9. matrix 命名空间

| 函数 | 状态 |
|------|------|
| matrix.new / new_float/int/bool/string/color | ✅ |
| matrix.get/set/rows/cols | ✅ |
| matrix.fill/copy/clear | ✅ |
| matrix.reshape/transpose | ✅ |
| matrix.add/sub/mul (元素级) | ✅ |
| matrix.sum/avg/max/min | ✅ |

## 10. map 命名空间

| 函数 | 状态 |
|------|------|
| map.new / put/get/remove/contains | ✅ |
| map.size/keys/values/clear/copy | ✅ |

## 11. color 命名空间

| 功能 | 状态 |
|------|------|
| color 常量 (color.red 等) | ✅ | 作为字符串透传，可被 color.new/rgb 解析 |
| color.new | ✅ | #RRGGBBAA，transp 0..100 |
| color.rgb | ✅ | |
| color.from_gradient | 🟡 | 简化实现：返回第一个色标 |
| color.tostring | ✅ | |

## 12. request 命名空间

| 函数 | 状态 | 说明 |
|------|------|------|
| request.security 同品种高周期 | ✅ | |
| gaps_on/off | ✅ | |
| lookahead_off | ✅ | |
| lookahead_on/barmerge | 🔴 | 未来数据，安全考虑 |
| 整数倍周期聚合 | ✅ | |
| 跨品种请求 | 🔴 | 需外部数据源 |
| 低周期请求 | 🔴 | 需低周期K线数据 |
| 嵌套 request | 🔴 | 复杂度高 |
| request.security_lower_tf | 🔴 | 低周期数据不可得 |
| request.economic | 🔴 | 外部数据 |
| request.quandl | 🔴 | 外部数据 |

## 13. strategy 命名空间 / Broker

| 功能 | 状态 | 说明 |
|------|------|------|
| strategy.entry | ✅ | V8 Broker，支持 qty/limit/stop |
| strategy.order | ✅ | 裸订单 |
| strategy.close/close_all | ✅ | 支持 qty / qty_percent 部分平仓 |
| strategy.exit (stop/limit) | ✅ | 绝对价 + OCA 互斥 |
| strategy.exit (profit/loss ticks) | ✅ | 基于 tick_size |
| strategy.exit (trailing) | ✅ | trail_points/trail_offset，棘轮向上 |
| strategy.exit (qty/percent) | ✅ | 部分退出 |
| 金字塔 (pyramiding) | ✅ | strategy 声明 pyramiding，同向加仓均价 |
| OCA 订单组 | ✅ | stop/limit 一触即撤 |
| commission/slippage 模型 | ✅ | percent/cash 佣金 + tick 滑点（运行时配置 broker） |
| 同bar保守撮合 | ✅ | 信号次 bar 开盘成交；同 bar stop 优先于 limit；exit 先于 entry |
| calc_on_every_tick | 🔴 | 逐tick不支持 |
| strategy.risk.* | ⬜ | |
| strategy.position_size | ✅ | 内置变量 |
| strategy.position_avg_price | ✅ | |
| strategy.opentrades | ✅ | |
| strategy.closedtrades | ✅ | |
| strategy.equity | ✅ | |
| strategy.gross_loss/profit/net_* /win/loss/event trades | ✅ | |

## 14. 绘图对象 (Drawing Objects)

| 对象 | 状态 | 说明 |
|------|------|------|
| plot/plotshape/hline | ✅ | |
| line.new/set_*/get_*/delete | ✅ | V8 状态模型 |
| label.new/set_*/get_*/delete | ✅ | |
| box.new/set_*/get_*/delete | ✅ | |
| table.new/cell/set_cell/get_cell/delete | ✅ | cells 状态字典 |
| polyline.new/set_points/delete | ✅ | |
| linefill.new | ✅ | |
| draw_events 回放 | ✅ | create/set/delete 事件序列 |
| alertcondition | ✅ | |

## 15. input 命名空间

| 函数 | 状态 |
|------|------|
| input/int/float/bool/string | ✅ |
| input.source | ✅ |
| input.timeframe | ✅ |
| input.symbol | ✅ |
| input.color | ⬜ |
| input.resolution | ⬜ |
| input.session | ⬜ |

## 量化目标追踪

| 维度 | 目标 | 当前 |
|------|------|------|
| 核心语法结构 | 100% | ~50% |
| ta/math/str/array 纯计算 | ≥95% | ~60% |
| matrix/map/color | 尽量全 | 0% |
| request.security | 完善 | 基础 |
| strategy broker | 完整模拟器 | ✅ V8 保守撮合 |
| 绘图对象 | 状态模型完整 | ✅ V8 |
| import | 本地多文件 | 0% |
