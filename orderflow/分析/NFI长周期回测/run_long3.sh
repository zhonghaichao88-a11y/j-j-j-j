#!/bin/bash
# 整年 60 个币一次跑要 14GB 内存，被系统杀掉了；没跑出来的年份拆成上下半年跑（每半年都从 100U 开始）
cd /home/user/ext
B="./ftvenv/bin/freqtrade backtesting --userdir ft --strategy NostalgiaForInfinityX7 --export trades --datadir ft/data_long/okx"
run() { y=$1; tr=$2
  [ -f nfi/long_$y.done ] && return
  mkdir -p nfi/long_$y
  nice -n 5 $B -c nfi/cfg_long_${y:0:4}.json --timerange $tr --backtest-directory nfi/long_$y > nfi/log_long_$y.txt 2>&1 && ls nfi/long_$y/*.zip >/dev/null 2>&1 && touch nfi/long_$y.done
}
for i in 1 2; do
run 2021a 20210101-20210701; run 2021b 20210701-20220101
run 2024a 20240101-20240701; run 2024b 20240701-20250101
run 2025a 20250101-20250701; run 2025b 20250701-20260101
run 2026a 20260101-20260515; run 2026b 20260515-20260930
done
touch nfi/long3_all.done
