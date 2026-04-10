#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

mkdir -p logs

if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

nohup python web_obs.py >> logs/web_obs.log 2>&1 &
PID=$!

echo "✓ 已在后台启动，PID: $PID"
echo "  查看日志：tail -f logs/web_obs.log"
echo "  停止程序：./stop.sh"
