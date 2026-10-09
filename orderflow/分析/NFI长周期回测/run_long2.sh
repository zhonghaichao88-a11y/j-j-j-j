#!/bin/bash
# 等第一轮跑完，再补跑没跑成的年份（2021 因为仓位超过欧易最高档报错），最后出汇总
cd /home/user/ext
while [ ! -f nfi/long_all.done ]; do sleep 60; done
while pgrep -f "long_summary.py" >/dev/null; do sleep 10; done
bash nfi/run_long.sh
./ftvenv/bin/python nfi/long_summary.py > nfi/long_summary.txt 2>&1
touch nfi/long_final.done
