#!/bin/bash
# 上位机源码 → Windows 桌面 同步脚本
# 只同步顶层 .py / .txt / .bat 文件，忽略 __pycache__/.venv/dist
# 无变更时静默，有变更时打印变更文件
SRC="/home/myqx9112/BOOT2026/上位机源码"
DST="/mnt/e/Desktop/BOOT2026_上位机"

if [ ! -d "$DST" ]; then
    echo "[sync] 目标目录不存在: $DST"
    exit 1
fi

# -rlt: 保留递归/符号链接/时间戳；不保留权限/属主/属组（drvfs 挂载无法正确表示，
#        否则每次都会被误判为变更）。-i 输出变更项，过滤目录行；无变更则无输出
changes=$(rsync -rlti \
  --no-perms --no-owner --no-group \
  --include='*.py' \
  --include='*.txt' \
  --include='*.bat' \
  --exclude='*' \
  "$SRC/" "$DST/" 2>/dev/null | grep -vE '^\.d')

if [ -n "$changes" ]; then
    echo "[sync] $(date '+%H:%M:%S')"
    echo "$changes" | sed 's/^/  /'
fi

