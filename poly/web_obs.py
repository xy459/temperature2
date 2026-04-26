"""
临时 Web 工具：多数据源城市温度折线图 + 后台拉取/轮询写 SQLite。
运行：python web_obs.py
访问：http://localhost:5050 与 http://localhost:5050/charts（根路径重定向到 /charts）

启动：先建库、立刻监听 HTTP:5050；随后在线程中全量拉取各渠道数据并启轮询（WU 对每城拉本地今+昨，避免与默认日期错位）。
折线图默认日期=各城「本地今天」的最早一天；不再用单一日 UTC 作为默认值，减少美洲时区与 UTC 日期不一致时整图无数据。
轮询：各渠道城市本地 11:00~17:00 活跃期自适应，其余时段降频。
"""
import logging
import re
import threading
import time
from datetime import datetime, timezone, timedelta

import requests
from flask import Flask, redirect, render_template_string, request, jsonify
from zoneinfo import ZoneInfo

import database as db
from cities import CITIES
from config import WU_API_KEY, WEATHERAPI_KEY, AVWX_TOKEN

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
_CITY_MAP = {c["icao"]: c for c in CITIES}

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "PolyTempBot/1.0"})

V1_BASE         = "https://api.weather.com/v1/location/{icao}:9:{country}/observations/historical.json"
NOAA_BASE       = "https://aviationweather.gov/api/data/metar?ids={icao}&format=raw&taf=false"
WEATHERAPI_BASE = "http://api.weatherapi.com/v1/current.json"
AVWX_BASE       = "https://avwx.rest/api/metar/{icao}"

# ── 自适应轮询配置 ────────────────────────────────────────────────────
# 活跃期：城市本地 11:00~17:00，HUNTING→COOLDOWN 状态机
# 非活跃期：城市本地 17:00~次日10:00，固定 8 分钟
ACTIVE_HOUR_START   = 11           # 活跃期开始（本地时间，含）
ACTIVE_HOUR_END     = 17           # 活跃期结束（本地时间，不含）
OFFPEAK_INTERVAL    = 8 * 60       # 非活跃期固定间隔（秒）
HUNT_STEPS          = [30, 30, 45, 45, 60]  # HUNTING 步进序列（秒），末位封顶
COOLDOWN_A_INTERVAL = 5 * 60       # COOLDOWN Phase A 轮询间隔（秒）
COOLDOWN_B_INTERVAL = 2 * 60       # COOLDOWN Phase B 轮询间隔（秒）
COOLDOWN_A_RATIO    = 2 / 3        # Phase A 占预期更新周期 T 的比例
POLL_TICK           = 10           # 主循环最小唤醒间隔（秒）

# 各渠道预期更新周期 T（秒）—— 用于计算 COOLDOWN Phase A/B 切换时刻
_CHANNEL_CYCLE: dict[str, int] = {
    "wu_metar":   30 * 60,
    "noaa":       30 * 60,
    "avwx":       30 * 60,
    "weatherapi": 15 * 60,
}
_ALL_CHANNELS = list(_CHANNEL_CYCLE.keys())


def _channels_for_city(city: dict) -> list[str]:
    """按城市去掉不可用渠道，避免反复 400 / 无效请求。"""
    chs = _ALL_CHANNELS
    if not city.get("wu_v1", True):
        chs = [c for c in chs if c != "wu_metar"]
    if not city.get("avwx", True):
        chs = [c for c in chs if c != "avwx"]
    return chs


# 匹配 METAR 温度/露点组，如 18/06、M02/M10、09/M03
_NOAA_TEMP_RE = re.compile(r'\b(M?\d{1,2})/(M?\d{1,2})\b')


def _rows_c_to_f_for_display(rows: list) -> list:
    """库内为摄氏，折线图需华氏时转为 °F 展示。"""
    if not rows:
        return []
    out = []
    for r in rows:
        t = r.get("temperature")
        if t is None:
            out.append({**r, "temperature": None})
        else:
            try:
                tf = round(float(t) * 9.0 / 5.0 + 32.0, 2)
            except (TypeError, ValueError):
                tf = None
            out.append({**r, "temperature": tf})
    return out


def _chart_series_for_display(city: dict, series: dict) -> dict:
    if not city.get("fahrenheit"):
        return series
    return {ch: _rows_c_to_f_for_display(rows) for ch, rows in series.items()}


# ── V1 API 拉取 ───────────────────────────────────────────────────────

def _fetch_v1(city: dict, date_str: str):
    """
    调用 WU V1 历史 API，返回 (obs_list, error_msg)。
    obs_list 每项：{"obs_time": "YYYY-MM-DD HH:MM:SS", "temperature": float|None}，温度**统一为摄氏**再入库。
    美国城市用 units=e（华氏）并在本函数内转为摄氏。
    """
    icao = city["icao"]
    country = city["country"]
    use_imperial = city.get("fahrenheit", False)
    url = V1_BASE.format(icao=icao, country=country)
    date_compact = date_str.replace("-", "")
    try:
        resp = _SESSION.get(
            url,
            params={
                "apiKey": WU_API_KEY,
                "units": "e" if use_imperial else "m",
                "startDate": date_compact,
                "endDate": date_compact,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return [], str(e)

    result = []
    for o in data.get("observations", []):
        ts = o.get("valid_time_gmt")
        temp = o.get("temp")
        if ts is None:
            continue
        if temp is not None and use_imperial:
            try:
                temp = round((float(temp) - 32.0) * 5.0 / 9.0, 2)
            except (TypeError, ValueError):
                temp = None
        obs_time = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        result.append({"obs_time": obs_time, "temperature": temp})
    return result, ""


def fetch_and_store(city: dict, date_str: str) -> tuple:
    """拉取指定城市指定日期的 WU METAR 并写库，返回 (新增条数, error_msg)。"""
    if not city.get("wu_v1", True):
        return 0, ""
    obs_list, err = _fetch_v1(city, date_str)
    if err:
        return 0, err
    inserted = db.insert_metar_observations(city["icao"], obs_list)
    return inserted, ""


# ── NOAA METAR 拉取 ───────────────────────────────────────────────────

def _parse_noaa_temp(raw: str):
    """从原始 METAR 报文中提取温度（°C），负温以 M 前缀表示。"""
    m = _NOAA_TEMP_RE.search(raw)
    if not m:
        return None
    val = m.group(1)
    return -int(val[1:]) if val.startswith("M") else int(val)


def _parse_noaa_obs_time(raw: str) -> str | None:
    """
    从原始 METAR 报文中提取观测时间，返回 "YYYY-MM-DD HH:MM:00" (UTC)。
    METAR 时间格式为 DDHHMMZ，如 081250Z → 第8天 12:50 UTC。
    """
    m = re.search(r'\b(\d{2})(\d{2})(\d{2})Z\b', raw)
    if not m:
        return None
    day, hour, minute = int(m.group(1)), int(m.group(2)), int(m.group(3))
    now = datetime.now(timezone.utc)
    try:
        dt = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
        # 若解析日期比当前大（跨月边界），回退到上个月
        if dt > now + timedelta(hours=1):
            if now.month == 1:
                dt = dt.replace(year=now.year - 1, month=12)
            else:
                dt = dt.replace(month=now.month - 1)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def fetch_and_store_noaa(icao: str) -> tuple:
    """拉取 NOAA 最新 METAR 并写库，返回 (is_new: bool, error_msg: str)。"""
    url = NOAA_BASE.format(icao=icao)
    try:
        resp = _SESSION.get(url, timeout=10)
        resp.raise_for_status()
        body = resp.text
    except Exception as e:
        return False, str(e)

    lines = [l.strip() for l in body.strip().splitlines() if l.strip() and icao in l]
    if not lines:
        return False, "no METAR line"

    raw      = lines[0]
    obs_time = _parse_noaa_obs_time(raw)
    temp     = _parse_noaa_temp(raw)

    if not obs_time:
        return False, f"cannot parse time: {raw}"

    inserted = db.insert_noaa_metar(icao, obs_time, temp)
    return inserted, ""


# ── WeatherAPI 拉取 ───────────────────────────────────────────────────

def fetch_and_store_weatherapi(icao: str) -> tuple:
    """拉取 WeatherAPI 最新观测并写库，返回 (is_new: bool, error_msg: str)。"""
    if not WEATHERAPI_KEY:
        return False, "WEATHERAPI_KEY 未配置"
    try:
        resp = _SESSION.get(
            WEATHERAPI_BASE,
            params={"key": WEATHERAPI_KEY, "q": f"metar:{icao}", "aqi": "no"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return False, str(e)

    cur        = data.get("current", {})
    temp_c     = cur.get("temp_c")
    last_epoch = cur.get("last_updated_epoch")

    if temp_c is None or last_epoch is None:
        return False, "无有效数据"

    obs_time = datetime.fromtimestamp(last_epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    inserted = db.insert_multi_channel_obs(icao, "weatherapi", obs_time, temp_c)
    return inserted, ""


# ── AVWX 拉取 ────────────────────────────────────────────────────────

def fetch_and_store_avwx(city: dict) -> tuple:
    """拉取 AVWX 最新 METAR 并写库，返回 (is_new: bool, error_msg: str)。"""
    icao = city["icao"]
    if not city.get("avwx", True):
        return False, ""
    if not AVWX_TOKEN:
        return False, "AVWX_TOKEN 未配置"
    try:
        resp = _SESSION.get(
            AVWX_BASE.format(icao=icao),
            headers={"Authorization": f"BEARER {AVWX_TOKEN}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return False, str(e)

    temp_info = data.get("temperature", {})
    temp_c    = temp_info.get("value") if isinstance(temp_info, dict) else None

    time_info = data.get("time", {})
    time_dt   = time_info.get("dt", "") if isinstance(time_info, dict) else ""

    if temp_c is None or not time_dt:
        return False, "无有效数据"

    try:
        dt = datetime.fromisoformat(time_dt.replace("Z", "+00:00"))
        obs_time = dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return False, f"时间解析失败: {time_dt}"

    inserted = db.insert_multi_channel_obs(icao, "avwx", obs_time, temp_c)
    return inserted, ""


# ── 启动全量初始化 ────────────────────────────────────────────────────

def init_metar_all():
    """程序启动时，为所有城市各拉取「本地今、昨」两日的 WU METAR 并写库。"""
    logger.info("[metar] 启动全量初始化（每城本地今+昨），共 %d 个城市", len(CITIES))
    total_new = 0
    for city in CITIES:
        for d in (0, 1):
            ds = _city_local_date_minus(city, d)
            inserted, err = fetch_and_store(city, ds)
            if err:
                logger.error(
                    "[metar] 初始化失败 %s (%s) %s: %s",
                    city["name"], city["icao"], ds, err,
                )
            else:
                total_new += inserted
                logger.info(
                    "[metar] %s (%s) %s → +%d 条",
                    city["name"], city["icao"], ds, inserted,
                )
    logger.info("[metar] 初始化完成，共新增 %d 条", total_new)


def init_noaa_metar_all():
    """程序启动时，为所有城市拉取最新 NOAA METAR 并写库。"""
    logger.info("[noaa] 启动初始化，共 %d 个城市", len(CITIES))
    for city in CITIES:
        is_new, err = fetch_and_store_noaa(city["icao"])
        if err:
            logger.error("[noaa] 初始化失败 %s (%s): %s", city["name"], city["icao"], err)
        else:
            logger.info("[noaa] %s (%s) → %s", city["name"], city["icao"],
                        "新数据" if is_new else "已存在")
    logger.info("[noaa] 初始化完成")


def init_weatherapi_all():
    """程序启动时，为所有城市拉取最新 WeatherAPI 数据并写库。"""
    if not WEATHERAPI_KEY:
        logger.warning("[weatherapi] WEATHERAPI_KEY 未配置，跳过初始化")
        return
    logger.info("[weatherapi] 启动初始化，共 %d 个城市", len(CITIES))
    for city in CITIES:
        is_new, err = fetch_and_store_weatherapi(city["icao"])
        if err:
            logger.error("[weatherapi] 初始化失败 %s (%s): %s", city["name"], city["icao"], err)
        else:
            logger.info("[weatherapi] %s (%s) → %s", city["name"], city["icao"],
                        "新数据" if is_new else "已存在")
    logger.info("[weatherapi] 初始化完成")


def init_avwx_all():
    """程序启动时，为所有城市拉取最新 AVWX METAR 并写库。"""
    if not AVWX_TOKEN:
        logger.warning("[avwx] AVWX_TOKEN 未配置，跳过初始化")
        return
    logger.info("[avwx] 启动初始化，共 %d 个城市", len(CITIES))
    for city in CITIES:
        if not city.get("avwx", True):
            continue
        is_new, err = fetch_and_store_avwx(city)
        if err:
            logger.error("[avwx] 初始化失败 %s (%s): %s", city["name"], city["icao"], err)
        else:
            logger.info("[avwx] %s (%s) → %s", city["name"], city["icao"],
                        "新数据" if is_new else "已存在")
    logger.info("[avwx] 初始化完成")


# ── 后台轮询线程 ──────────────────────────────────────────────────────

def _city_local_date(city: dict) -> str:
    return datetime.now(ZoneInfo(city["timezone"])).strftime("%Y-%m-%d")


def _city_local_date_minus(city: dict, day_offset: int) -> str:
    d = datetime.now(ZoneInfo(city["timezone"])).date() - timedelta(days=day_offset)
    return d.isoformat()


def _default_charts_date() -> str:
    """与日期选择器联动：各城「查询日」是各自本地日历上的同一天；默认用 CITIES 中「本地今天」的最早一天。
    这样 UTC 已跨日、美洲仍为「昨天」时，不会默认到美洲尚未有入库的「本地今天」而整图空数据。"""
    dmin = min(
        datetime.now(ZoneInfo(c["timezone"])).date() for c in CITIES
    )
    return dmin.isoformat()


# ── 轮询状态机 ───────────────────────────────────────────────────────
# 每个 (icao, channel) 对独立维护一个状态字典：
#   mode         : "offpeak" | "hunting" | "cooldown_a" | "cooldown_b"
#   hunt_step    : HUNT_STEPS 当前索引
#   next_poll_at : time.monotonic() 时间戳，到达时才执行下次轮询
#   phase_b_at   : COOLDOWN_A → COOLDOWN_B 的切换时刻
#   phase_end_at : COOLDOWN 结束（→ HUNTING）的时刻

def _make_state() -> dict:
    """创建初始状态：HUNTING，立即可轮询（next_poll_at=0）。"""
    return {
        "mode":         "hunting",
        "hunt_step":    0,
        "next_poll_at": 0.0,
        "phase_b_at":   0.0,
        "phase_end_at": 0.0,
    }


def _enter_offpeak(state: dict, now: float) -> None:
    state["mode"]         = "offpeak"
    state["next_poll_at"] = now + OFFPEAK_INTERVAL


def _enter_hunting(state: dict, now: float) -> None:
    state["mode"]         = "hunting"
    state["hunt_step"]    = 0
    state["next_poll_at"] = now + HUNT_STEPS[0]


def _enter_cooldown(state: dict, channel: str, now: float) -> None:
    """进入（或重置）COOLDOWN，从 now 重新计算 Phase A/B 边界。"""
    T = _CHANNEL_CYCLE[channel]
    state["mode"]         = "cooldown_a"
    state["hunt_step"]    = 0
    state["phase_b_at"]   = now + T * COOLDOWN_A_RATIO
    state["phase_end_at"] = now + T
    state["next_poll_at"] = now + COOLDOWN_A_INTERVAL


def _advance_state(state: dict, channel: str, is_new: bool, now: float) -> None:
    """根据本次轮询结果推进状态机，更新 next_poll_at。"""
    mode = state["mode"]

    if mode == "offpeak":
        state["next_poll_at"] = now + OFFPEAK_INTERVAL
        return

    if mode == "hunting":
        if is_new:
            _enter_cooldown(state, channel, now)
        else:
            step = min(state["hunt_step"] + 1, len(HUNT_STEPS) - 1)
            state["hunt_step"]    = step
            state["next_poll_at"] = now + HUNT_STEPS[step]
        return

    # cooldown_a 或 cooldown_b
    if is_new:
        _enter_cooldown(state, channel, now)   # 重置 COOLDOWN 计时
        return

    if now >= state["phase_end_at"]:           # COOLDOWN 自然耗尽 → HUNTING
        _enter_hunting(state, now)
    elif now >= state["phase_b_at"]:           # Phase A 结束 → Phase B
        state["mode"]         = "cooldown_b"
        state["next_poll_at"] = now + COOLDOWN_B_INTERVAL
    else:                                      # 继续 Phase A
        state["next_poll_at"] = now + COOLDOWN_A_INTERVAL


# ── 渠道调度层 ───────────────────────────────────────────────────────

def _do_poll(city: dict, channel: str, today: str) -> tuple:
    """
    执行单次轮询，返回 (is_new: bool, error_msg: str)。
    统一各渠道返回格式，屏蔽内部差异。
    """
    icao = city["icao"]
    if channel == "wu_metar":
        inserted, err = fetch_and_store(city, today)
        return inserted > 0, err
    if channel == "noaa":
        return fetch_and_store_noaa(icao)
    if channel == "weatherapi":
        if not WEATHERAPI_KEY:
            return False, ""
        return fetch_and_store_weatherapi(icao)
    if channel == "avwx":
        if not AVWX_TOKEN:
            return False, ""
        return fetch_and_store_avwx(city)
    return False, f"未知渠道: {channel}"


def _poll_loop():
    """
    自适应轮询：每 POLL_TICK 秒唤醒，按各 (城市×渠道) 状态机决定是否执行轮询。

    时段划分（城市本地时间）：
      活跃期  11:00~17:00 — HUNTING→COOLDOWN_A→COOLDOWN_B 状态机
      非活跃期 17:00~10:00 — 固定 8 分钟

    HUNTING 步进：[30, 30, 45, 45, 60]s，找到新数据后进入 COOLDOWN。
    COOLDOWN_A：前 2/3×T，每 5 分钟；COOLDOWN_B：后 1/3×T，每 2 分钟。
    Phase B 自然耗尽 → 重回 HUNTING。找到新数据随时重置 COOLDOWN。
    """
    # 初始化所有城市×渠道状态（next_poll_at=0 → 启动后立即执行首次轮询）
    _state: dict[tuple, dict] = {}
    for city in CITIES:
        for ch in _channels_for_city(city):
            _state[(city["icao"], ch)] = _make_state()
    last_date: dict[str, str] = {}

    logger.info("[poll] 自适应轮询线程启动 (tick=%ds, 活跃期 %d:00~%d:00)",
                POLL_TICK, ACTIVE_HOUR_START, ACTIVE_HOUR_END)

    while True:
        now = time.monotonic()

        for city in CITIES:
            icao     = city["icao"]
            tz       = ZoneInfo(city["timezone"])
            local_dt = datetime.now(tz)
            today    = local_dt.strftime("%Y-%m-%d")
            is_active = ACTIVE_HOUR_START <= local_dt.hour < ACTIVE_HOUR_END

            if last_date.get(icao) != today:
                logger.info("[poll] %s 切换到新的一天 %s", city["name"], today)
                last_date[icao] = today

            for ch in _channels_for_city(city):
                key   = (icao, ch)
                state = _state[key]
                mode  = state["mode"]

                # ── 时段切换检测 ────────────────────────────────────
                if is_active and mode == "offpeak":
                    _enter_hunting(state, now)
                    logger.info("[poll] %s/%s 进入活跃期 → HUNTING", icao, ch)
                    continue   # 刚重置，等下一 tick 再轮询

                elif not is_active and mode != "offpeak":
                    _enter_offpeak(state, now)
                    logger.info("[poll] %s/%s 进入非活跃期 → OFFPEAK(%ds)", icao, ch, OFFPEAK_INTERVAL)
                    continue

                # ── 未到轮询时刻，跳过 ──────────────────────────────
                if now < state["next_poll_at"]:
                    continue

                # ── 执行轮询 ────────────────────────────────────────
                is_new, err = _do_poll(city, ch, today)

                if err:
                    log_fn = logger.error if ch == "wu_metar" else logger.debug
                    log_fn("[%s] 轮询失败 %s: %s", ch, icao, err)
                elif is_new:
                    logger.info("[%s] %s → 新数据 | %s → cooldown",
                                ch, icao, state["mode"])

                # ── 推进状态机 ──────────────────────────────────────
                prev_mode = state["mode"]
                _advance_state(state, ch, is_new, now)
                new_mode  = state["mode"]

                if prev_mode != new_mode:
                    logger.debug("[%s] %s 状态切换 %s → %s (next +%ds)",
                                 ch, icao, prev_mode, new_mode,
                                 int(state["next_poll_at"] - now))

        time.sleep(POLL_TICK)


def start_poll_thread():
    t = threading.Thread(target=_poll_loop, daemon=True, name="metar-poller")
    t.start()
    return t


# ── HTML 模板 ─────────────────────────────────────────────────────────

CHARTS_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>温度折线图</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #f5f5f5; color: #333; padding: 24px; }

  h1 { font-size: 1.2rem; font-weight: 600; margin-bottom: 16px; color: #111; }

  .card { background: #fff; border-radius: 8px; padding: 16px;
          margin-bottom: 20px; border: 1px solid #ddd; }

  .form-row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
  label { font-size: 0.8rem; color: #666; display: block; margin-bottom: 4px; }
  input[type=date] { border: 1px solid #ccc; border-radius: 4px;
    color: #333; padding: 6px 10px; font-size: 0.9rem; background: #fff; }
  button { background: #2563eb; color: #fff; border: none; border-radius: 4px;
           padding: 7px 18px; font-size: 0.9rem; cursor: pointer; }
  button:hover { background: #1d4ed8; }

  .charts-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 16px;
  }
  @media (max-width: 960px)  { .charts-grid { grid-template-columns: repeat(2, 1fr); } }
  @media (max-width: 600px)  { .charts-grid { grid-template-columns: 1fr; } }

  .chart-card {
    background: #fff;
    border-radius: 8px;
    border: 1px solid #ddd;
    padding: 12px 14px 14px;
  }

  .chart-title {
    font-size: 0.88rem;
    font-weight: 600;
    color: #111;
    margin-bottom: 6px;
    line-height: 1.35;
    white-space: normal;
  }
  .chart-subtitle {
    font-size: 0.72rem;
    color: #999;
    font-weight: 400;
    margin-left: 5px;
  }
  .chart-local-time {
    font-size: 0.68rem;
    color: #64748b;
    font-weight: 400;
    margin-left: 0.35rem;
  }

  .chart-wrap {
    position: relative;
    height: 240px;
  }

  .no-data {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: #bbb;
    font-size: 0.82rem;
  }

  .loading-state {
    text-align: center;
    padding: 64px 0;
    color: #999;
    font-size: 0.9rem;
  }
  .error-state {
    text-align: center;
    padding: 64px 0;
    color: #ef4444;
    font-size: 0.9rem;
  }
</style>
</head>
<body>

<h1>🌡 城市温度折线图（多数据源；美国城市为 °F）</h1>

<div class="card">
  <div class="form-row">
    <div>
      <label>日期（每城各自本地日历上的一天；未指定时默认取各城「本地今天」的最早日）</label>
      <input type="date" id="date-picker" value="{{ selected_date }}">
    </div>
    <div><button onclick="loadCharts()">查询</button></div>
  </div>
</div>

<div id="charts-container">
  <div class="loading-state">加载中…</div>
</div>

<script>
/* UTC 字符串 → 该城市本地时间，以"浏览器本地时间"形式返回 Date。
   原理：将 UTC 转成城市本地时间字符串，再不加 Z 解析为 Date——
   浏览器将其视为本地时区时刻，Chart.js 标签就会显示该字符串对应的时:分。 */
function toFakeLocal(utcStr, tz) {
  const d = new Date(utcStr.replace(' ', 'T') + 'Z');
  const localStr = d.toLocaleString('sv-SE', { timeZone: tz });
  return new Date(localStr.replace(' ', 'T'));
}

/* 根据温度值决定显示精度；美国城市为华氏度 */
function fmtTemp(v, useF) {
  if (v === null || v === undefined) return '—';
  if (useF) {
    return (v % 1 !== 0) ? v.toFixed(1) + '°F' : Math.round(v) + '°F';
  }
  return (v % 1 !== 0) ? v.toFixed(1) + '°C' : Math.round(v) + '°C';
}

/** 机场 IANA 时区当前本地时间：月日时分（无年、秒），与 DST 一致 */
function formatCityLocalNow(tz) {
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: tz,
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(new Date());
  const g = (type) => {
    const p = parts.find((x) => x.type === type);
    return p ? p.value : '';
  };
  return g('month') + '月' + g('day') + '日 ' + g('hour') + ':' + g('minute');
}

function updateChartLocalTimes() {
  document.querySelectorAll('.chart-local-time[data-tz]').forEach((el) => {
    const tz = el.getAttribute('data-tz');
    try {
      el.textContent = formatCityLocalNow(tz);
    } catch (e) {
      el.textContent = '';
    }
  });
}

let _localTimeIntervalId = null;
function stopChartLocalTimeTicker() {
  if (_localTimeIntervalId) {
    clearInterval(_localTimeIntervalId);
    _localTimeIntervalId = null;
  }
}
function startChartLocalTimeTicker() {
  stopChartLocalTimeTicker();
  updateChartLocalTimes();
  _localTimeIntervalId = setInterval(updateChartLocalTimes, 60000);
}

const SERIES_CONFIG = [
  { key: 'noaa',       label: 'NOAA METAR', color: '#2563eb', width: 1.8, radius: 2.5 },
  { key: 'wu_metar',   label: 'WU METAR',   color: '#ea580c', width: 1.5, radius: 2   },
  { key: 'weatherapi', label: 'WeatherAPI', color: '#9333ea', width: 1.5, radius: 2   },
  { key: 'avwx',       label: 'AVWX',       color: '#0891b2', width: 1.5, radius: 2   },
];

const _chartInstances = [];

async function loadCharts() {
  const date = document.getElementById('date-picker').value;
  if (!date) return;

  stopChartLocalTimeTicker();

  const container = document.getElementById('charts-container');
  container.innerHTML = '<div class="loading-state">加载中…</div>';

  _chartInstances.forEach(c => c.destroy());
  _chartInstances.length = 0;

  let json;
  try {
    const resp = await fetch('/api/charts_data?date=' + date);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    json = await resp.json();
  } catch (e) {
    container.innerHTML = '<div class="error-state">加载失败：' + e.message + '</div>';
    return;
  }

  const grid = document.createElement('div');
  grid.className = 'charts-grid';
  container.innerHTML = '';
  container.appendChild(grid);

  // 无 Z → 浏览器本地时间，与 toFakeLocal 保持同一时区基准
  const dayStart = new Date(date + 'T00:00:00').getTime();
  const dayEnd   = new Date(date + 'T23:59:59').getTime();

  json.cities.forEach((city, idx) => {
    const useF = !!city.fahrenheit;
    const card = document.createElement('div');
    card.className = 'chart-card';

    // 构建每个数据源的数据点
    const datasets = SERIES_CONFIG.map(cfg => {
      const raw = (city.series && city.series[cfg.key]) || [];
      const pts = raw
        .filter(d => d.temperature !== null && d.temperature !== undefined)
        .map(d => ({ x: toFakeLocal(d.obs_time, city.timezone).getTime(), y: d.temperature }));
      return {
        label: cfg.label,
        data: pts,
        borderColor: cfg.color,
        backgroundColor: 'transparent',
        borderWidth: cfg.width,
        pointRadius: cfg.radius,
        pointHoverRadius: cfg.radius + 2,
        pointBackgroundColor: cfg.color,
        tension: 0.35,
        fill: false,
      };
    });

    const hasAnyData = datasets.some(ds => ds.data.length > 0);
    const bodyHtml = hasAnyData
      ? '<canvas id="cv-' + idx + '"></canvas>'
      : '<div class="no-data">暂无数据</div>';

    card.innerHTML =
      '<div class="chart-title">' + city.name_cn +
      (useF ? ' <span style="font-size:0.72rem;color:#64748b;">°F</span>' : '') +
      '<span class="chart-subtitle">' + city.name + ' · ' + city.icao +
      ' <span style="color:#2563eb;font-size:0.68rem;">' + city.utc_offset + '</span>' +
      '<span class="chart-local-time" data-tz="' + city.timezone + '"></span></span></div>' +
      '<div class="chart-wrap">' + bodyHtml + '</div>';
    grid.appendChild(card);

    if (!hasAnyData) return;

    const ctx = document.getElementById('cv-' + idx).getContext('2d');
    const chart = new Chart(ctx, {
      type: 'line',
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            display: true,
            position: 'bottom',
            labels: {
              boxWidth: 12,
              boxHeight: 2,
              padding: 8,
              font: { size: 10 },
              color: '#555',
              usePointStyle: true,
              pointStyle: 'line',
            },
          },
          tooltip: {
            callbacks: {
              title: (items) => {
                const dt = new Date(items[0].parsed.x);
                const h  = String(dt.getHours()).padStart(2, '0');
                const m  = String(dt.getMinutes()).padStart(2, '0');
                return h + ':' + m + ' 本地';
              },
              label: (item) => ' ' + item.dataset.label + ': ' + fmtTemp(item.parsed.y, useF),
            }
          }
        },
        scales: {
          x: {
            type: 'time',
            min: dayStart,
            max: dayEnd,
            time: {
              unit: 'hour',
              displayFormats: { hour: 'HH:mm' },
            },
            ticks: { maxTicksLimit: 7, font: { size: 10 }, color: '#999' },
            grid:  { color: '#f0f0f0' },
            border: { color: '#e5e5e5' },
          },
          y: {
            ticks: {
              callback: (v) => (useF ? (v + '°F') : (v + '°C')),
              font: { size: 10 },
              color: '#999',
            },
            grid:  { color: '#f0f0f0' },
            border: { color: '#e5e5e5' },
          }
        }
      }
    });
    _chartInstances.push(chart);
  });

  startChartLocalTimeTicker();
}

document.addEventListener('DOMContentLoaded', loadCharts);
</script>
</body>
</html>
"""


# ── Flask 路由 ────────────────────────────────────────────────────────

@app.route("/charts")
def charts():
    selected_date = request.args.get("date", _default_charts_date())
    return render_template_string(CHARTS_TEMPLATE, selected_date=selected_date)


@app.route("/api/charts_data")
def charts_data():
    date_str = request.args.get("date", _default_charts_date())

    try:
        year  = int(date_str[0:4])
        month = int(date_str[5:7])
        day   = int(date_str[8:10])
    except (ValueError, IndexError):
        return jsonify({"error": "invalid date"}), 400

    result = []
    for city in CITIES:
        tz = ZoneInfo(city["timezone"])
        local_start = datetime(year, month, day, 0, 0, 0, tzinfo=tz)
        local_end   = datetime(year, month, day, 23, 59, 59, tzinfo=tz)
        utc_start   = local_start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        utc_end     = local_end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # 用当天正午计算该城市的 UTC 偏移（避免夏令时边界干扰）
        noon       = datetime(year, month, day, 12, 0, 0, tzinfo=tz)
        offset_sec = noon.utcoffset().total_seconds()
        sign = "+" if offset_sec >= 0 else "-"
        total = int(abs(offset_sec))
        offset_h = total // 3600
        offset_m = (total % 3600) // 60
        utc_offset = (
            f"UTC{sign}{offset_h}:{offset_m:02d}"
            if offset_m else
            f"UTC{sign}{offset_h}"
        )

        icao = city["icao"]
        series = {
            "noaa":       db.get_noaa_metar_by_utc_range(icao, utc_start, utc_end),
            "wu_metar":   db.get_metar_by_utc_range(icao, utc_start, utc_end),
            "weatherapi": db.get_multi_channel_by_utc_range(icao, "weatherapi", utc_start, utc_end),
            "avwx":       db.get_multi_channel_by_utc_range(icao, "avwx", utc_start, utc_end),
        }
        series = _chart_series_for_display(city, series)
        result.append(
            (
                offset_sec,
                {
                    "icao":       icao,
                    "name":       city["name"],
                    "name_cn":    city["name_cn"],
                    "timezone":   city["timezone"],
                    "utc_offset": utc_offset,
                    "fahrenheit": bool(city.get("fahrenheit")),
                    "series":     series,
                },
            )
        )

    # 从东向西：当日 UTC 偏移从大到小（与所选图表日期、夏令时一致）
    result.sort(key=lambda x: x[0], reverse=True)
    cities_out = [item[1] for item in result]

    return jsonify({"date": date_str, "cities": cities_out})


@app.route("/")
def index():
    return redirect("/charts", code=302)


# ── 入口 ──────────────────────────────────────────────────────────────

def _background_data_bootstrap() -> None:
    """在 HTTP 已监听后执行：全量拉取 + 轮询。各 init_* 内部已对单城失败打日志。"""
    try:
        init_metar_all()
        init_noaa_metar_all()
        init_weatherapi_all()
        init_avwx_all()
    except Exception:
        logger.exception("后台全量初始化未完整完成，将依赖轮询补数")
    try:
        start_poll_thread()
    except Exception:
        logger.exception("轮询线程启动失败")


if __name__ == "__main__":
    db.init_db()

    # 过滤外部扫描机器人发来的 TLS 握手包产生的 werkzeug 噪声日志
    class _TLSProbeFilter(logging.Filter):
        def filter(self, record):
            return "Bad request version" not in record.getMessage()

    logging.getLogger("werkzeug").addFilter(_TLSProbeFilter())

    threading.Thread(
        target=_background_data_bootstrap,
        name="data-bootstrap",
        daemon=True,
    ).start()

    print("已监听 http://0.0.0.0:5050（/ → /charts），后台全量拉取与轮询已排队启动…")
    app.run(host="0.0.0.0", port=5050, debug=False, use_reloader=False)
