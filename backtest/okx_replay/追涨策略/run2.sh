#!/bin/bash
cd /home/user/ext
for s in VB_A VB_T PD_A PD_T VS_A VS_T; do
  case $s in VB_*) D="";; *) D="--timeframe-detail 5m";; esac
  nice -n 5 ./ftvenv/bin/freqtrade backtesting -c pump/cfg_new.json --userdir ft --datadir ft/data/okx --strategy $s $D --timerange 20241101-20260929 --export trades --backtest-directory pump/v2new_$s > pump/log2_new_$s.txt 2>&1
  nice -n 5 ./ftvenv/bin/freqtrade backtesting -c pump/cfg_old.json --userdir ft --datadir ft/data_old/okx --strategy $s $D --timerange 20220101-20240101 --export trades --backtest-directory pump/v2old_$s > pump/log2_old_$s.txt 2>&1
done
touch pump/done2
