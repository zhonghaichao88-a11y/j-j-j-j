#!/bin/bash
cd /home/user/ext
nice -n 5 ./ftvenv/bin/freqtrade backtesting -c pump/cfg_new.json --userdir ft --datadir ft/data/okx --strategy nasosv5 --timerange 20241101-20260929 --export trades --backtest-directory nasos/new > nasos/log_new.txt 2>&1
nice -n 5 ./ftvenv/bin/freqtrade backtesting -c pump/cfg_old.json --userdir ft --datadir ft/data_old/okx --strategy nasosv5 --timerange 20220101-20240101 --export trades --backtest-directory nasos/old > nasos/log_old.txt 2>&1
touch nasos/done
