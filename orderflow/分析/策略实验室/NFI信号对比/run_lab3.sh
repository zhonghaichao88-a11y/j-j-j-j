#!/bin/bash
# 断了自动重跑（跳过已算完的币）
out=${@: -1}; name=$(basename $out)${PART//\//_}; prev=-1
while true; do
  /home/user/ext/ftvenv/bin/python /home/user/ext/nfisig/lab3.py "$@" >> /home/user/ext/nfisig/lab3_$name.log 2>&1
  n=$(ls $out 2>/dev/null | wc -l); [ "$n" = "$prev" ] && break; prev=$n
done
echo DONE >> /home/user/ext/nfisig/lab3_$name.log
