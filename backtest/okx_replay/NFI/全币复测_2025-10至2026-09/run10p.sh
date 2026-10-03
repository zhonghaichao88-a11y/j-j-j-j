cd /home/user/ext
export SSL_CERT_FILE=/root/.ccr/ca-bundle.crt
ulimit -v 11000000
for c in nfi/cfg10A_*.json nfi/cfg10B_*.json; do
  n=$(basename $c .json)
  mkdir nfi/all10/$n.lock 2>/dev/null || continue        # 别的进程已经在跑这一批
  nice -n 5 ./ftvenv/bin/freqtrade backtesting -c $c --userdir ft --datadir ft/data_all/okx --strategy NostalgiaForInfinityX7 \
    --timerange 20251001-20260929 --export trades --backtest-directory nfi/all10/$n > nfi/all10/$n.log 2>&1 && touch nfi/all10/$n.done
done
