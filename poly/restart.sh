#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

PIDS=$(ps aux | grep "[w]eb_obs.py" | awk '{print $2}')
if [ -n "$PIDS" ]; then
    echo "正在停止旧进程：$PIDS"
    echo "$PIDS" | xargs kill
    sleep 2
    echo "✓ 旧进程已停止"
else
    echo "未发现运行中的旧进程，直接启动"
fi

mkdir -p logs

if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

nohup python web_obs.py >> logs/web_obs.log 2>&1 &
PID=$!

echo "✓ 已在后台重新启动，PID: $PID"
echo "  查看日志：tail -f logs/web_obs.log"
echo "  停止程序：./stop.sh"
