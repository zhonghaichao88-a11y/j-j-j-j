#!/bin/bash
# 把 /home/user/ext/bnk 的 K 线分批推到 j-j-j-j-kline（每批约 300MB；推完一批删掉本地副本再做下一批，硬盘不够放第二份）
# 只拉远端的目录结构（不下载已有文件内容），用 git 底层命令把新文件拼进目录树，推送时也不碰旧文件：
# 之前用普通 add/commit/push，git 会把远端已有的数据下载回来做比对，把硬盘写满了
set -u
URL=https://github.com/zhonghaichao88-a11y/j-j-j-j-kline
R=/home/user/j-j-j-j-kline; SRC=/home/user/ext/bnk; LIM=$((300*1024*1024))
MSG_TAIL=$'\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018DYsJzkLNSSvZHoqzL9FH8'
export GIT_AUTHOR_NAME=Claude GIT_AUTHOR_EMAIL=noreply@anthropic.com GIT_COMMITTER_NAME=Claude GIT_COMMITTER_EMAIL=noreply@anthropic.com
fresh() {
  cd / && rm -rf "$R"
  for i in 1 2 3 4; do git clone -q --depth 1 --filter=blob:none --no-checkout "$URL" "$R" && break; sleep $((2**i)); done
  cd "$R" || exit 1
  git config --unset extensions.partialClone; git config --unset remote.origin.promisor; git config --unset remote.origin.partialclonefilter
}
n=0
while true; do
  fresh
  git rev-parse -q --verify HEAD >/dev/null || { echo "远端还是空的，先用旧版推说明"; exit 1; }
  have=$(git ls-tree --name-only HEAD:k 2>/dev/null)
  batch=(); size=0
  for f in $(ls "$SRC" | grep '\.parquet$' | sort); do
    echo "$have" | grep -qx "$f" && continue
    s=$(stat -c %s "$SRC/$f"); [ $size -gt 0 ] && [ $((size+s)) -gt $LIM ] && break
    batch+=("$f"); size=$((size+s))
  done
  [ ${#batch[@]} -eq 0 ] && break
  free=$(df --output=avail -k / | tail -1); [ $free -lt $((size/1024 + 700000)) ] && { echo "$(date -u +%H:%M) 硬盘不够（剩 $((free/1024))MB），先停"; exit 1; }
  n=$((n+1))
  { git ls-tree HEAD:k
    for f in "${batch[@]}"; do printf '100644 blob %s\t%s\n' "$(git hash-object -w "$SRC/$f")" "$f"; done; } > /tmp/kline_k.txt
  kt=$(git mktree --missing < /tmp/kline_k.txt) || exit 1
  rt=$( { git ls-tree HEAD | grep -v $'\tk$'; printf '040000 tree %s\tk\n' "$kt"; } | git mktree --missing) || exit 1
  c=$(git commit-tree "$rt" -p HEAD -m "K 线第 $n 批：${#batch[@]} 个币（${batch[0]%.parquet} ~ ${batch[-1]%.parquet}），$((size/1048576))MB$MSG_TAIL") || exit 1
  ok=0; for i in 1 2 3 4 5; do git -c core.bigFileThreshold=1m -c pack.window=0 push --no-thin -q origin "$c:refs/heads/main" && { ok=1; break; }; sleep $((2**i)); done
  [ $ok = 1 ] || { echo "推送失败"; exit 1; }
  echo "$(date -u +%H:%M) 第 $n 批推完：${#batch[@]} 个币 $((size/1048576))MB，远端共 $(( $(echo "$have" | grep -c parquet) + ${#batch[@]} )) 个"
done
echo "全部推完：$(git ls-tree --name-only HEAD:k | wc -l) 个文件"; cd / && rm -rf "$R"; echo "KLINE PUSH DONE"
