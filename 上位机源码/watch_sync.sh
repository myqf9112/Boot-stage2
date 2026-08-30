#!/bin/bash
# 自动同步守护：每 2 秒检测一次变化并同步
# 用法: bash watch_sync.sh
SYNC="/home/myqx9112/BOOT2026/上位机源码/sync_to_windows.sh"
echo "[watch] 自动同步守护已启动（每 2 秒检测），Ctrl+C 退出"
while true; do
    "$SYNC" 2>/dev/null
    sleep 2
done
