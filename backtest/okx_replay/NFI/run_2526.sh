#!/bin/bash
# 内存不够两个40币回测同时跑：等别的回测都结束再跑
cd /home/user/ext
while ps -eo args | grep -v grep | grep -q "freqtrade backtesting"; do sleep 30; done
nice -n 5 ./ftvenv/bin/freqtrade backtesting --userdir ft --strategy NostalgiaForInfinityX7 --export trades -c nfi/config_200.json --timerange 20251001-20260929 --backtest-directory nfi/u200_2526 > nfi/log_200_2526.txt 2>&1
touch nfi/u200_2526_done
