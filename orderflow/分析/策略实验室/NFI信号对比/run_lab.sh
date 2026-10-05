#!/bin/bash
# 断了自动重跑（脚本会跳过已经算完的币），直到这一份连续两次没有新文件
cd /home/user/ext/nfi
args="$@"; name=$(basename ${@: -1})${PART//\//_}
prev=-1
while true; do
  /home/user/ext/ftvenv/bin/python /home/user/ext/nfisig/lab.py $args >> /home/user/ext/nfisig/lab_$name.log 2>&1
  n=$(ls ${@: -1} 2>/dev/null | wc -l)
  [ "$n" = "$prev" ] && break
  prev=$n
done
echo DONE >> /home/user/ext/nfisig/lab_$name.log
