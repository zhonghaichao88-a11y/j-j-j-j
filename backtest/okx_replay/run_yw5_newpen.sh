#!/bin/bash
# 方案二 8 项改法：新笔版本（等老笔版本跑完再开始，避免抢 CPU）
cd /home/user/okx_data
until [ -f yw5.done ]; do sleep 60; done
PEN=1 OUTTAG=_newpen PROCS=2 python3 yuanwen5.py 5m > run_yw5_5m_np.log 2>&1
PEN=1 OUTTAG=_newpen PROCS=2 python3 yuanwen5.py 5mnew > run_yw5_new_np.log 2>&1
sed -e "s/yuanwen5_5m.json/yuanwen5_5m_newpen.json/; s/yuanwen5_5mnew.json/yuanwen5_5mnew_newpen.json/; s/yuanwen5_summary.csv/yuanwen5_newpen_summary.csv/" yw5_eval.py > yw5_eval_np.py
python3 yw5_eval_np.py > yw5_eval_np.txt 2>&1
touch yw5_newpen.done
