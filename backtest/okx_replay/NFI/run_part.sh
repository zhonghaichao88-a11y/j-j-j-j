#!/bin/bash
cd /home/user/ext
for tr in 20220101-20230101 20230101-20240101; do
  nice -n 5 ./ftvenv/bin/freqtrade backtesting -c nfi/config_part.json --userdir ft --datadir ft/data_part/okx --strategy NostalgiaForInfinityX7 --timerange $tr --export trades --backtest-directory nfi/part_${tr} > nfi/log_part_${tr}.txt 2>&1
done
./ftvenv/bin/python nfi_mtm2.py /home/user/ext/ft/data_part/okx/futures 'nfi/part_*.zip' > nfi/mtm_part.txt 2>&1
touch nfi/part_done
