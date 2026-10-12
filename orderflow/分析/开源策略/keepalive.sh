#!/bin/bash
# 看门程序：每分钟检查一次，停了就拉起来（用 setsid 单独起，不跟着这个脚本一起被停）
#   1) 开源策略总流程 pipeline.py（日志最后是 PIPELINE DONE 就不再起）
#   2) 策略机模拟盘 A：94 个币，端口 8020
#   3) 策略机模拟盘 B：成交额前 150，端口 8021；模拟盘 C：E0V1E_DCA3（会加仓），94 个币，端口 8022
#   4) 硬盘快满（<700MB）：删掉最近一天产生的 >200MB 的回测大文件，免得写满后全部卡住
#   5) 总流程日志 2 小时没动：记一条警告（每小时守护会看）
PY=/home/user/ext/ftvenv/bin/python
LOG=/home/user/ext/keepalive.log
running() { ps -eo args | grep -q "^$PY $1"; }
port_ok() { curl -s -m 5 "127.0.0.1:$1/api/ready" | grep -q ready || lsof -nP -iTCP:$1 -sTCP:LISTEN -t >/dev/null 2>&1; }   # 端口有人在听就算活着（可能只是忙）
# 容器重启后网络代理端口会变：还在用旧代理的程序（连不上网）杀掉，下面会用新代理重新拉起
stale_kill() {
  for pid in $(ps -eo pid,args | grep -E "^ *[0-9]+ ($PY (pipeline.py|sb_app.py|allcoins_bn.py|fulltest.py|engine_years.py|parity_stratbot.py)|/bin/bash /home/user/ext/(run_full9.sh|jobs/))" | awk '{print $1}'); do
    px=$(tr '\0' '\n' < /proc/$pid/environ 2>/dev/null | grep '^HTTPS_PROXY=' | cut -d= -f2-)
    if [ -n "$px" ] && [ -n "$HTTPS_PROXY" ] && [ "$px" != "$HTTPS_PROXY" ]; then
      echo "$(date -u +%m-%d\ %H:%M) 进程 $pid 用的是旧代理 $px（现在是 $HTTPS_PROXY），重启它" >> $LOG
      kill $pid
    fi
  done
}
while true; do
  stale_kill
  if ! running "pipeline.py" && ! tail -5 /home/user/ext/ftrun/pipeline.log | grep -q "PIPELINE DONE"; then
    echo "$(date -u +%m-%d\ %H:%M) 重启 pipeline" >> $LOG
    (cd /home/user/j-j-j-j/orderflow/分析/开源策略 && setsid nohup $PY pipeline.py >> /home/user/ext/ftrun/pipeline.log 2>&1 < /dev/null &)
  fi
  if ! ps -eo args | grep -q "^/bin/bash /home/user/ext/run_full9.sh" && ! tail -3 /home/user/ext/ftrun/full9.log 2>/dev/null | grep -q "FULL9 DONE"; then
    echo "$(date -u +%m-%d\ %H:%M) 启动/重启 9 个候选的 freqtrade 按年复测" >> $LOG
    (setsid nohup /home/user/ext/run_full9.sh >> /home/user/ext/ftrun/full9.log 2>&1 < /dev/null &)   # 套一层子 shell：不挂在看门程序下面，换看门程序时不会被带走
  fi
  for j in /home/user/ext/jobs/*.sh; do                   # 一次性任务：没跑完（日志最后没有 JOB DONE）又不在跑就拉起来
    [ -e "$j" ] || continue
    if ! ps -eo args | grep -q "^/bin/bash $j" && ! tail -2 "$j.log" 2>/dev/null | grep -q "JOB DONE"; then
      echo "$(date -u +%m-%d\ %H:%M) 启动/重启任务 $(basename $j)" >> $LOG
      (setsid nohup "$j" >> "$j.log" 2>&1 < /dev/null &)
    fi
  done
  for spec in "8020:/home/user/sbpaper/策略机" "8021:/home/user/sbpaper150" "8022:/home/user/sbpaper_e0v1e" "8023:/home/user/sbpaper_elliotv8"; do
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
