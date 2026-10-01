#!/bin/bash
cd /home/user/ext
for cfg in nfiU nfi6; do
 for tr in 20241101-20251001 20251001-20260929; do
  nice -n 10 ./ftvenv/bin/freqtrade backtesting -c nfi/config_$cfg.json --userdir ft --strategy NostalgiaForInfinityX7 --timerange $tr --export trades --backtest-directory nfi/bt_${cfg}_${tr} > nfi/log_${cfg}_${tr}.txt 2>&1
 done
done
touch nfi/done
