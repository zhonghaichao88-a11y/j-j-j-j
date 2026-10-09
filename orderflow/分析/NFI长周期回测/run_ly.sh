#!/bin/bash
# 断了自动重跑（已算完的币会跳过）
for i in 1 2 3 4 5; do /home/user/ext/ftvenv/bin/python /home/user/ext/nfisig/dip_sig_long.py "$@" >> /home/user/ext/nfisig/ly_$1.log 2>&1; done
