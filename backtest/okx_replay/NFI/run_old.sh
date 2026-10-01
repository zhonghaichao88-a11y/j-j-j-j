#!/bin/bash
# 等 2021-11~2023 数据下载完，跑 NFI 熊市/震荡市测试（不限仓位，每笔起始100U），再算逐小时盯市回撤
cd /home/user/ext
while ps -eo args | grep -v grep | grep -q "freqtrade download-data"; do sleep 60; done
for tr in 20220101-20230101 20230101-20240101; do
  nice -n 10 ./ftvenv/bin/freqtrade backtesting -c nfi/config_old.json --userdir ft --datadir ft/data_old/okx --strategy NostalgiaForInfinityX7 --timerange $tr --export trades --backtest-directory nfi/old_${tr} > nfi/log_old_${tr}.txt 2>&1
done
./ftvenv/bin/python nfi_mtm2.py /home/user/ext/ft/data_old/okx/futures 'nfi/old_*.zip' > nfi/mtm_old.txt 2>&1
touch nfi/old_done
