#!/usr/bin/env bash
# 一键同步上位机源码到 Windows 桌面(BOOT2026_上位机)
# 用法: bash tools/sync_host_to_desktop.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)/上位机源码"
DST="/mnt/e/Desktop/BOOT2026_上位机"

if [ ! -d "$SRC" ]; then
    echo "源目录不存在: $SRC"
    exit 1
fi

mkdir -p "$DST"

cp -f "$SRC"/protocol.py \
      "$SRC"/flasher.py \
      "$SRC"/stm32bl.py \
      "$SRC"/stm32bl_gui.py \
      "$SRC"/stm32bl_debug_gui.py \
      "$SRC"/_fix_gui.py \
      "$SRC"/requirements.txt \
      "$DST"/

if [ ! -f "$DST/run_gui.bat" ]; then
    printf '@echo off\r\ncd /d %%%%~dp0\r\npython stm32bl_gui.py\r\nif errorlevel 1 (\r\n    echo.\r\n    echo Run failed. Install deps with: pip install -r requirements.txt\r\n    pause\r\n)\r\n' > "$DST/run_gui.bat"
fi

echo "已同步上位机源码到桌面: $DST"
