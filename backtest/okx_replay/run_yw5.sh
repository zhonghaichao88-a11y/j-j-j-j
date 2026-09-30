#!/bin/bash
cd /home/user/okx_data
PROCS=2 python3 yuanwen5.py 5m > run_yw5_5m.log 2>&1
PROCS=2 python3 yuanwen5.py 5mnew > run_yw5_new.log 2>&1
python3 yw5_eval.py > yw5_eval.txt 2>&1
touch yw5.done
