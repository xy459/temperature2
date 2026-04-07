"""
Weather Underground / api.weather.com 数据延迟测试工具

测试目标：
1. v3 实时观测 API（缓存 ~26s）— 新数据多快出现？
2. v1 历史观测 API（缓存 ~1h）— 新观测多快被追加？
3. METAR 观测时间 vs API 可用时间的延迟

运行方式：
  python3 wu_latency_test.py

会持续运行，在每个整点/半点前后密集轮询，记录延迟。
按 Ctrl+C 停止并查看汇总统计。
"""

import requests
import time
import json
import os
from datetime import datetime, timezone, timedelta

API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"
STATION = "LEMD"
LOCATION_ID = "LEMD:9:ES"

V3_CURRENT_URL = "https://api.weather.com/v3/wx/observations/current"
V1_HISTORY_URL = f"https://api.weather.com/v1/location/{LOCATION_ID}/observations/historical.json"

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"wu_latency_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

MADRID_TZ = timezone(timedelta(hours=2))  # CEST (April)

latency_records = []


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def fetch_v3_current(units="m"):
    """v3 实时观测 API — 缓存约 26 秒"""
    t0 = time.monotonic()
    try:
        resp = requests.get(V3_CURRENT_URL, params={
            "apiKey": API_KEY,
            "language": "en-US",
            "units": units,
            "format": "json",
            "icaoCode": STATION,
        }, timeout=10)
        elapsed_ms = (time.monotonic() - t0) * 1000
        data = resp.json()
        cache_control = resp.headers.get("cache-control", "")
        age = resp.headers.get("age", "?")
        return {
            "source": "v3_current",
            "http_ms": round(elapsed_ms),
            "obs_time_utc": data.get("validTimeUtc"),
            "obs_time_local": data.get("validTimeLocal"),
            "temperature": data.get("temperature"),
            "temp_max_since_7am": data.get("temperatureMaxSince7Am"),
            "temp_max_24h": data.get("temperatureMax24Hour"),
            "cache_control": cache_control,
            "age": age,
            "status": resp.status_code,
        }
    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        return {"source": "v3_current", "error": str(e), "http_ms": round(elapsed_ms)}


def fetch_v1_history(date_str=None):
    """v1 历史观测 API — 缓存约 1 小时"""
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    t0 = time.monotonic()
    try:
        resp = requests.get(V1_HISTORY_URL, params={
            "apiKey": API_KEY,
            "units": "m",
            "startDate": date_str,
            "endDate": date_str,
        }, timeout=10)
        elapsed_ms = (time.monotonic() - t0) * 1000
        data = resp.json()
        observations = data.get("observations", [])
        cache_control = resp.headers.get("cache-control", "")
        age = resp.headers.get("age", "?")

        latest_obs = observations[-1] if observations else None
        latest_time = latest_obs["valid_time_gmt"] if latest_obs else None
        latest_temp = latest_obs["temp"] if latest_obs else None

        return {
            "source": "v1_history",
            "http_ms": round(elapsed_ms),
            "total_obs": len(observations),
            "latest_obs_utc": latest_time,
            "latest_temp": latest_temp,
            "cache_control": cache_control,
            "age": age,
            "status": resp.status_code,
        }
    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        return {"source": "v1_history", "error": str(e), "http_ms": round(elapsed_ms)}


def utc_ts_to_str(ts):
    if ts is None:
        return "N/A"
    return datetime.utcfromtimestamp(ts).strftime("%H:%M:%S UTC")


def seconds_until_next_boundary():
    """距离下一个整点/半点还有多少秒"""
    now = datetime.now(timezone.utc)
    minute = now.minute
    second = now.second
    if minute < 30:
        target_min = 30
    else:
        target_min = 60
    remaining = (target_min - minute) * 60 - second
    return remaining


def get_poll_interval():
    """
    根据距离整点/半点的时间动态调整轮询间隔：
    - 距离 ≤ 2 分钟：每 10 秒（密集监控新数据何时出现）
    - 距离 ≤ 5 分钟：每 15 秒
    - 距离 ≤ 10 分钟：每 20 秒
    - 其他：每 30 秒
    """
    secs = seconds_until_next_boundary()
    # 也考虑刚过整点/半点的情况（数据通常在观测时间后几分钟出现）
    now = datetime.now(timezone.utc)
    mins_past = now.minute % 30
    secs_past = mins_past * 60 + now.second

    if secs_past <= 600:  # 刚过整点/半点 10 分钟内
        if secs_past <= 120:
            return 5   # 最密集
        elif secs_past <= 300:
            return 10
        else:
            return 15
    elif secs <= 120:  # 快到下一个整点/半点
        return 10
    elif secs <= 300:
        return 15
    else:
        return 30


def run_test():
    last_v3_obs_time = None
    last_v1_obs_count = None
    last_v1_latest_time = None
    poll_count = 0

    log("=" * 70)
    log("Weather Underground 数据延迟测试启动")
    log(f"站点: {STATION} (马德里巴拉哈斯机场)")
    log(f"日志文件: {LOG_FILE}")
    log(f"测试 API:")
    log(f"  1) v3 实时观测 (缓存 ~26s)")
    log(f"  2) v1 历史观测 (缓存 ~1h)")
    log("=" * 70)

    # 初始查询
    v3 = fetch_v3_current()
    v1 = fetch_v1_history()

    if "error" not in v3:
        last_v3_obs_time = v3["obs_time_utc"]
        log(f"[初始] v3 当前观测: {utc_ts_to_str(last_v3_obs_time)} | "
            f"{v3['temperature']}°C | Max7AM: {v3['temp_max_since_7am']}°C | "
            f"HTTP: {v3['http_ms']}ms | Cache: {v3['cache_control']}")
    if "error" not in v1:
        last_v1_obs_count = v1["total_obs"]
        last_v1_latest_time = v1["latest_obs_utc"]
        log(f"[初始] v1 历史观测: {v1['total_obs']} 条 | "
            f"最新: {utc_ts_to_str(last_v1_latest_time)} | "
            f"{v1['latest_temp']}°C | HTTP: {v1['http_ms']}ms | Cache: {v1['cache_control']}")

    log("-" * 70)
    log("开始持续轮询... (Ctrl+C 停止)")
    log("")

    try:
        while True:
            interval = get_poll_interval()
            now_utc = datetime.now(timezone.utc)
            now_madrid = now_utc.astimezone(MADRID_TZ)
            mins_past = now_utc.minute % 30
            secs_past = mins_past * 60 + now_utc.second
            next_boundary = seconds_until_next_boundary()

            time.sleep(interval)
            poll_count += 1

            # 同时请求两个 API
            v3 = fetch_v3_current()
            v1 = fetch_v1_history()

            now_ts = int(time.time())
            changes = []

            # 检测 v3 新观测
            if "error" not in v3:
                if v3["obs_time_utc"] != last_v3_obs_time:
                    delay = now_ts - v3["obs_time_utc"]
                    record = {
                        "type": "v3_new_obs",
                        "detected_at": now_ts,
                        "obs_time": v3["obs_time_utc"],
                        "delay_seconds": delay,
                        "temperature": v3["temperature"],
                        "http_ms": v3["http_ms"],
                    }
                    latency_records.append(record)
                    obs_time_str = utc_ts_to_str(v3["obs_time_utc"])
                    changes.append(
                        f"⚡ v3 新观测! 观测时间: {obs_time_str} | "
                        f"延迟: {delay}s ({delay/60:.1f}min) | "
                        f"温度: {v3['temperature']}°C | "
                        f"Max7AM: {v3['temp_max_since_7am']}°C | "
                        f"HTTP: {v3['http_ms']}ms"
                    )
                    last_v3_obs_time = v3["obs_time_utc"]

            # 检测 v1 新观测
            if "error" not in v1:
                if v1["total_obs"] != last_v1_obs_count or v1["latest_obs_utc"] != last_v1_latest_time:
                    if v1["latest_obs_utc"]:
                        delay = now_ts - v1["latest_obs_utc"]
                    else:
                        delay = -1
                    record = {
                        "type": "v1_new_obs",
                        "detected_at": now_ts,
                        "obs_time": v1["latest_obs_utc"],
                        "delay_seconds": delay,
                        "total_obs": v1["total_obs"],
                        "temperature": v1["latest_temp"],
                        "http_ms": v1["http_ms"],
                    }
                    latency_records.append(record)
                    obs_time_str = utc_ts_to_str(v1["latest_obs_utc"])
                    changes.append(
                        f"📊 v1 新观测! 观测时间: {obs_time_str} | "
                        f"延迟: {delay}s ({delay/60:.1f}min) | "
                        f"总条数: {v1['total_obs']} | "
                        f"温度: {v1['latest_temp']}°C | "
                        f"HTTP: {v1['http_ms']}ms"
                    )
                    last_v1_obs_count = v1["total_obs"]
                    last_v1_latest_time = v1["latest_obs_utc"]

            # 输出
            if changes:
                for c in changes:
                    log(c)
            else:
                if poll_count % 10 == 0:  # 每 10 次轮询输出一次心跳
                    status_parts = []
                    if "error" not in v3:
                        status_parts.append(
                            f"v3: {utc_ts_to_str(v3['obs_time_utc'])} {v3['temperature']}°C "
                            f"({v3['http_ms']}ms)"
                        )
                    if "error" not in v1:
                        status_parts.append(
                            f"v1: {v1['total_obs']}条 最新{utc_ts_to_str(v1['latest_obs_utc'])} "
                            f"({v1['http_ms']}ms)"
                        )
                    log(f"[心跳 #{poll_count}] 无变化 | 间隔{interval}s | "
                        f"下一整/半点: {next_boundary}s | {' | '.join(status_parts)}")

    except KeyboardInterrupt:
        log("")
        log("=" * 70)
        log("测试结束 — 汇总统计")
        log("=" * 70)

        if not latency_records:
            log("未检测到任何新观测数据变化。")
        else:
            v3_records = [r for r in latency_records if r["type"] == "v3_new_obs"]
            v1_records = [r for r in latency_records if r["type"] == "v1_new_obs"]

            if v3_records:
                delays = [r["delay_seconds"] for r in v3_records]
                log(f"\nv3 实时观测 API:")
                log(f"  检测到 {len(v3_records)} 次新数据")
                log(f"  平均延迟: {sum(delays)/len(delays):.0f}s ({sum(delays)/len(delays)/60:.1f}min)")
                log(f"  最小延迟: {min(delays)}s ({min(delays)/60:.1f}min)")
                log(f"  最大延迟: {max(delays)}s ({max(delays)/60:.1f}min)")
                log(f"  HTTP 平均耗时: {sum(r['http_ms'] for r in v3_records)/len(v3_records):.0f}ms")
                log(f"  详细记录:")
                for r in v3_records:
                    log(f"    观测 {utc_ts_to_str(r['obs_time'])} → "
                        f"检测 {utc_ts_to_str(r['detected_at'])} = "
                        f"{r['delay_seconds']}s | {r['temperature']}°C")

            if v1_records:
                delays = [r["delay_seconds"] for r in v1_records if r["delay_seconds"] >= 0]
                log(f"\nv1 历史观测 API:")
                log(f"  检测到 {len(v1_records)} 次新数据")
                if delays:
                    log(f"  平均延迟: {sum(delays)/len(delays):.0f}s ({sum(delays)/len(delays)/60:.1f}min)")
                    log(f"  最小延迟: {min(delays)}s ({min(delays)/60:.1f}min)")
                    log(f"  最大延迟: {max(delays)}s ({max(delays)/60:.1f}min)")
                log(f"  HTTP 平均耗时: {sum(r['http_ms'] for r in v1_records)/len(v1_records):.0f}ms")
                log(f"  详细记录:")
                for r in v1_records:
                    log(f"    观测 {utc_ts_to_str(r['obs_time'])} → "
                        f"检测 {utc_ts_to_str(r['detected_at'])} = "
                        f"{r['delay_seconds']}s | 总{r['total_obs']}条 | {r['temperature']}°C")

        log(f"\n总轮询次数: {poll_count}")
        log(f"日志已保存: {LOG_FILE}")
        log("=" * 70)


if __name__ == "__main__":
    run_test()
