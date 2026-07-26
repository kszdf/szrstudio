#!/bin/bash
# HGTStudio 自动同步：仅在有改动时 commit + push 到 GitHub（SSH 免密）
REPO="/d/heygem_data/gpt_sovits"
cd "$REPO" || { echo "无法进入 $REPO"; exit 1; }
LOG="$REPO/auto_push.log"

# 没有任何改动则跳过，避免无意义提交/推送
if [ -z "$(git status --porcelain)" ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') 无改动，跳过" >> "$LOG"
  exit 0
fi

git add -A
MSG="auto sync: $(date '+%Y-%m-%d %H:%M')"
git commit -q -m "$MSG"
if git push -u origin main -q >> "$LOG" 2>&1; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') $MSG -> 已推送" >> "$LOG"
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') $MSG -> 推送失败，详见上方" >> "$LOG"
fi
