#!/usr/bin/env bash
cd "$(dirname "$0")"

echo "=== web_obs 进程 ==="
ps aux | grep "[w]eb_obs.py" || true
