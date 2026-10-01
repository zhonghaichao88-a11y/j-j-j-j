#!/bin/bash
cd /home/user/ext
B="./ftvenv/bin/freqtrade backtesting --userdir ft --strategy NostalgiaForInfinityX7 --export trades"
( nice -n 5 $B -c nfi/config_200part.json --datadir ft/data_part/okx --timerange 20220101-20230101 --backtest-directory nfi/u200_2022 > nfi/log_200_2022.txt 2>&1
  nice -n 5 $B -c nfi/config_200part.json --datadir ft/data_part/okx --timerange 20230101-20240101 --backtest-directory nfi/u200_2023 > nfi/log_200_2023.txt 2>&1 ) &
( nice -n 5 $B -c nfi/config_200.json --timerange 20241101-20251001 --backtest-directory nfi/u200_2425 > nfi/log_200_2425.txt 2>&1
  nice -n 5 $B -c nfi/config_200.json --timerange 20251001-20260929 --backtest-directory nfi/u200_2526 > nfi/log_200_2526.txt 2>&1 ) &
wait
touch nfi/u200_done
