#!/bin/bash
cd /home/user/ext
for k in 1 2 3 4 5; do
  nice -n 10 ./ftvenv/bin/freqtrade backtesting -c nfi/config_new.json --userdir ft --datadir ft/data_new/okx --strategy NostalgiaForInfinityX7 --timerange 20251101-20260929 --export trades --backtest-directory nfi/new_20251101-20260929 > nfi/log_new.txt 2>&1
  grep -q "Could not load markets" nfi/log_new.txt || break
  sleep 30
done
./ftvenv/bin/python nfi_mtm2.py /home/user/ext/ft/data_new/okx/futures 'nfi/new_*.zip' > nfi/mtm_new.txt 2>&1
touch nfi/new_done
