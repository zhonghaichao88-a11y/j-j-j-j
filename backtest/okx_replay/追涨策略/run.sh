#!/bin/bash
cd /home/user/ext
for s in VolumeBreakoutStrategy PumpDetector VolumeSpikeTrend; do
  nice -n 5 ./ftvenv/bin/freqtrade backtesting -c pump/cfg_new.json --userdir ft --datadir ft/data/okx --strategy $s --timerange 20241101-20260929 --export trades --backtest-directory pump/new_$s > pump/log_new_$s.txt 2>&1
  nice -n 5 ./ftvenv/bin/freqtrade backtesting -c pump/cfg_old.json --userdir ft --datadir ft/data_old/okx --strategy $s --timerange 20220101-20240101 --export trades --backtest-directory pump/old_$s > pump/log_old_$s.txt 2>&1
done
touch pump/done
