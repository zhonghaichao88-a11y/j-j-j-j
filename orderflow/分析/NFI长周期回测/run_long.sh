#!/bin/bash
# NFI 长周期回测：每年一段，当年成交额前 60 的币，100U，最多 6 单
cd /home/user/ext
B="./ftvenv/bin/freqtrade backtesting --userdir ft --strategy NostalgiaForInfinityX7 --export trades --datadir ft/data_long/okx"
run() { y=$1; tr=$2
  [ -f nfi/long_$y.done ] && return
  mkdir -p nfi/long_$y
  nice -n 5 $B -c nfi/cfg_long_$y.json --timerange $tr --backtest-directory nfi/long_$y > nfi/log_long_$y.txt 2>&1 && ls nfi/long_$y/*.zip >/dev/null 2>&1 && touch nfi/long_$y.done
}
run 2020 20200401-20210101; run 2021 20210101-20220101; run 2022 20220101-20230101; run 2023 20230101-20240101; run 2024 20240101-20250101; run 2025 20250101-20260101; run 2026 20260101-20260930
touch nfi/long_all.done
