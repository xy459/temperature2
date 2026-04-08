#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

mkdir -p logs

if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

read -r -s -p "请输入钱包私钥解密密码：" POLY_MASTER_PASSWORD
echo
export POLY_MASTER_PASSWORD

nohup python main.py >> logs/app.log 2>&1 &
PID=$!
unset POLY_MASTER_PASSWORD

echo "✓ 已在后台启动，PID: $PID"
echo "  查看日志：tail -f logs/app.log"
echo "  停止程序：./stop.sh"
