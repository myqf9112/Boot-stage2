#!/bin/bash
# 上位机源码 → Windows 桌面 同步脚本
# 同步顶层源码文件，并把打包后的 STM32BL_Tool.exe 放到目标目录根部
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

# dist 目录不整体同步，只发布最终可执行文件，桌面上可以直接双击新版工具。
exe_changes=""
if [ -f "$SRC/dist/STM32BL_Tool.exe" ]; then
    exe_changes=$(rsync -lti \
      --no-perms --no-owner --no-group \
      "$SRC/dist/STM32BL_Tool.exe" "$DST/" 2>/dev/null)
fi

if [ -n "$exe_changes" ]; then
    changes="${changes}${changes:+$'\n'}${exe_changes}"
fi

if [ -n "$changes" ]; then
    echo "[sync] $(date '+%H:%M:%S')"
    echo "$changes" | sed 's/^/  /'
fi
