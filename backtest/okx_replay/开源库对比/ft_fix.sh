#!/bin/bash
# 补跑因内存不够/偶发错误没跑成的策略：一次只跑一个；1分钟策略只用 10 个主流币（全部 40 个内存不够）
cd /home/user/ext; export SSL_CERT_FILE=/root/.ccr/ca-bundle.crt
ALL=$(python3 -c "import json;print(' '.join(i.split('-')[0]+'/USDT' for i in json.load(open('/home/user/okx_data/universe_5m40.json'))))")
TOP10="BTC/USDT ETH/USDT SOL/USDT XRP/USDT DOGE/USDT BNB/USDT ADA/USDT LINK/USDT AVAX/USDT LTC/USDT"
run() {  # 策略 段 时间 币
  d=ft/bt_results/$1__$2; rm -rf $d $d.tmp; mkdir -p $d.tmp
  nice -n 5 ftvenv/bin/freqtrade backtesting -c ft/config.json --userdir ft --strategy $1 --timerange $3 --export trades --backtest-directory $d.tmp -p $4 > $d.tmp/log.txt 2>&1
  mv $d.tmp $d
}
for s in MultiRSI Strategy004 Strategy001_custom_exit InformativeSample ReinforcedAverageStrategy AwesomeMacd; do
  run $s 第一年 20240929-20250929 "$ALL"; run $s 第二年 20250929-20260930 "$ALL"
done
for s in BinHV45 CCIStrategy Low_BB Scalp SmoothScalp ReinforcedSmoothScalp; do
  run $s 第一年 20240929-20250929 "$TOP10"; run $s 第二年 20250929-20260930 "$TOP10"
done
python3 ft_eval.py > ft_eval.txt 2>&1
touch ft_fix.done
