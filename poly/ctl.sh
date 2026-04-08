#!/usr/bin/env bash
# 用法：./ctl.sh {start|stop|restart|status}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="${SCRIPT_DIR}/../venv/bin/python"   # 根据实际 venv 路径调整
MAIN="${SCRIPT_DIR}/main.py"
PID_FILE="${SCRIPT_DIR}/main.pid"
LOG_FILE="${SCRIPT_DIR}/logs/main.log"

# 若系统 python 即目标解释器，可改为：VENV_PYTHON=python
if [ ! -f "$VENV_PYTHON" ]; then
    VENV_PYTHON="$(which python3)"
fi

mkdir -p "${SCRIPT_DIR}/logs"

_pid() {
    [ -f "$PID_FILE" ] && cat "$PID_FILE"
}

_running() {
    local pid
    pid=$(_pid)
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

start() {
    if _running; then
        echo "[info] main.py 已在运行 (PID $(_pid))"
        return 0
    fi
    echo "[info] 启动 main.py ..."
    cd "$SCRIPT_DIR" || exit 1
    nohup "$VENV_PYTHON" "$MAIN" >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 1
    if _running; then
        echo "[ok]   启动成功 (PID $(_pid))，日志：$LOG_FILE"
    else
        echo "[err]  启动失败，请检查日志：$LOG_FILE"
        rm -f "$PID_FILE"
        return 1
    fi
}

stop() {
    if ! _running; then
        echo "[info] main.py 未在运行"
        rm -f "$PID_FILE"
        return 0
    fi
    local pid
    pid=$(_pid)
    echo "[info] 停止 main.py (PID $pid) ..."
    kill -TERM "$pid"
    local i=0
    while kill -0 "$pid" 2>/dev/null && [ $i -lt 10 ]; do
        sleep 1; i=$((i+1))
    done
    if kill -0 "$pid" 2>/dev/null; then
        echo "[warn] 未响应 SIGTERM，强制 SIGKILL ..."
        kill -KILL "$pid"
    fi
    rm -f "$PID_FILE"
    echo "[ok]   已停止"
}

status() {
    if _running; then
        echo "[ok]   main.py 运行中 (PID $(_pid))"
        echo "       日志：$LOG_FILE"
        echo "       最近 5 行："
        tail -n 5 "$LOG_FILE" 2>/dev/null | sed 's/^/         /'
    else
        echo "[info] main.py 未在运行"
        rm -f "$PID_FILE"
    fi
}

case "$1" in
    start)   start   ;;
    stop)    stop    ;;
    restart) stop; start ;;
    status)  status  ;;
    log)     tail -f "$LOG_FILE" ;;
    *)
        echo "用法：$0 {start|stop|restart|status|log}"
        exit 1
        ;;
esac
