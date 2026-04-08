"""
WU 观测数据拉取线程。
每 60 秒轮询所有城市的 WU v3 API，新数据写入 SQLite observations 表（自动去重）。
"""
import logging
import threading
import time
from datetime import datetime, timezone

import requests

import database as db
from cities import CITIES
from config import WU_API_KEY, WU_API_BASE, WU_POLL_INTERVAL_SECONDS

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "PolyTempBot/1.0"})


def _poll_city(city: dict) -> None:
    """拉取单个城市的最新观测，写入数据库。"""
    icao = city["icao"]
    name = city["name"]

    try:
        resp = _SESSION.get(
            WU_API_BASE,
            params={
                "apiKey":   WU_API_KEY,
                "language": "en-US",
                "units":    "m",
                "format":   "json",
                "icaoCode": icao,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.HTTPError as e:
        logger.error("[obs] %s (%s) HTTP 错误: %s", name, icao, e)
        return
    except Exception as e:
        logger.error("[obs] %s (%s) 请求失败: %s", name, icao, e)
        return

    valid_ts = data.get("validTimeUtc")
    temp     = data.get("temperature")

    if valid_ts is None or temp is None:
        logger.warning("[obs] %s (%s) 返回数据缺少字段: validTimeUtc=%s temperature=%s",
                       name, icao, valid_ts, temp)
        return

    obs_time  = datetime.fromtimestamp(valid_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    poll_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    max7am    = data.get("temperatureMaxSince7Am")

    is_new = db.insert_observation(
        city_icao          = icao,
        obs_time           = obs_time,
        poll_time          = poll_time,
        temperature        = float(temp),
        temp_max_since_7am = float(max7am) if max7am is not None else None,
    )

    if is_new:
        logger.info("[obs] ⚡ 新数据 %s (%s)  obs=%s  temp=%.1f°C", name, icao, obs_time, temp)
    else:
        logger.debug("[obs] 重复数据 %s (%s)  obs=%s", name, icao, obs_time)


def _poll_loop() -> None:
    """观测数据拉取主循环，顺序遍历所有城市。"""
    logger.info("[obs] 拉取线程启动，共 %d 个城市，间隔 %ds", len(CITIES), WU_POLL_INTERVAL_SECONDS)
    while True:
        start = time.monotonic()
        for city in CITIES:
            _poll_city(city)

        elapsed = time.monotonic() - start
        sleep_time = max(0, WU_POLL_INTERVAL_SECONDS - elapsed)
        logger.debug("[obs] 本轮完成，耗时 %.1fs，等待 %.1fs", elapsed, sleep_time)
        time.sleep(sleep_time)


def start() -> threading.Thread:
    """启动 obs 拉取后台线程，返回线程对象。"""
    t = threading.Thread(target=_poll_loop, daemon=True, name="obs-poller")
    t.start()
    return t
