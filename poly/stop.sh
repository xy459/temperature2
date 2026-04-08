#!/usr/bin/env bash
cd "$(dirname "$0")"

PIDS=$(ps aux | grep "[m]ain.py" | awk '{print $2}')

if [ -z "$PIDS" ]; then
    echo "未找到运行中的进程"
    exit 0
fi

echo "正在停止进程：$PIDS"
echo "$PIDS" | xargs kill
echo "✓ 已发送终止信号"
