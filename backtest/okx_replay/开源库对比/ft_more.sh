#!/bin/bash
# 3 个两年都赚的网友策略，在 181 个新币上验证（合约模式，只做多的策略照样只做多）；分两段：2024-10~2025-09、2025-10~2026-09
cd /home/user/ext; export SSL_CERT_FILE=/root/.ccr/ca-bundle.crt
PAIRS=$(python3 -c "import json;print(' '.join(json.load(open('pairs_more.json'))))")
mkdir -p ft/bt_more
for s in ADXMomentum AverageStrategy MultiMa FSupertrendStrategy; do
  for tr in "第一年 20240929-20250929" "第二年 20250929-20260930"; do
    set -- $tr; d=ft/bt_more/${s}__$1
    [ -d $d ] && continue
    mkdir -p $d.tmp
    nice -n 5 ftvenv/bin/freqtrade backtesting -c ft/config_futures.json --userdir ft --strategy $s --timerange $2 --export trades --backtest-directory $d.tmp -p $PAIRS > $d.tmp/log.txt 2>&1
    mv $d.tmp $d
  done
done
touch ft_more.done
