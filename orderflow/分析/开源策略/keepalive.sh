#!/bin/bash
# 看门程序：每分钟检查一次，停了就拉起来（用 setsid 单独起，不跟着这个脚本一起被停）
#   1) 开源策略总流程 pipeline.py（日志最后是 PIPELINE DONE 就不再起）
#   2) 策略机模拟盘 A：94 个币，端口 8020
#   3) 策略机模拟盘 B：成交额前 150，端口 8021
#   4) 硬盘快满（<700MB）：删掉最近一天产生的 >200MB 的回测大文件，免得写满后全部卡住
#   5) 总流程日志 2 小时没动：记一条警告（每小时守护会看）
PY=/home/user/ext/ftvenv/bin/python
LOG=/home/user/ext/keepalive.log
running() { ps -eo args | grep -q "^$PY $1"; }
port_ok() { curl -s -m 5 "127.0.0.1:$1/api/ready" | grep -q ready || lsof -nP -iTCP:$1 -sTCP:LISTEN -t >/dev/null 2>&1; }   # 端口有人在听就算活着（可能只是忙）
while true; do
  if ! running "pipeline.py" && ! tail -5 /home/user/ext/ftrun/pipeline.log | grep -q "PIPELINE DONE"; then
    echo "$(date -u +%m-%d\ %H:%M) 重启 pipeline" >> $LOG
    (cd /home/user/j-j-j-j/orderflow/分析/开源策略 && setsid nohup $PY pipeline.py >> /home/user/ext/ftrun/pipeline.log 2>&1 < /dev/null &)
  fi
  if ! ps -eo args | grep -q "^/bin/bash /home/user/ext/run_full9.sh" && ! tail -3 /home/user/ext/ftrun/full9.log 2>/dev/null | grep -q "FULL9 DONE"; then
    echo "$(date -u +%m-%d\ %H:%M) 启动/重启 9 个候选的 freqtrade 按年复测" >> $LOG
    setsid nohup /home/user/ext/run_full9.sh >> /home/user/ext/ftrun/full9.log 2>&1 < /dev/null &
  fi
  for spec in "8020:/home/user/sbpaper/策略机" "8021:/home/user/sbpaper150"; do
    port=${spec%%:*}; dir=${spec#*:}
    if ! port_ok $port; then
      sleep 20                                   # 刚启动的还在加载：再看一次
      if ! port_ok $port; then
        echo "$(date -u +%m-%d\ %H:%M) 重启模拟盘 $port" >> $LOG
        (cd "$dir" && SB_PORT=$port setsid nohup $PY sb_app.py >> run.out 2>&1 < /dev/null &)
        sleep 40
      fi
    fi
  done
  avail=$(df --output=avail -k / | tail -1)
  if [ "$avail" -lt 700000 ]; then
    echo "$(date -u +%m-%d\ %H:%M) 硬盘只剩 $((avail/1024))MB，清理大文件：" >> $LOG
    find /home/user/ext/ftrun /home/user/ext/ft /home/user/ext/bnm -xdev -type f -size +200M -mmin -1440 -print -delete >> $LOG 2>&1
  fi
  if [ -n "$(find /home/user/ext/ftrun/pipeline.log -mmin +120)" ] && ! tail -5 /home/user/ext/ftrun/pipeline.log | grep -q "PIPELINE DONE"; then
    echo "$(date -u +%m-%d\ %H:%M) 警告：总流程日志 2 小时没更新" >> $LOG
  fi
  sleep 60
done
