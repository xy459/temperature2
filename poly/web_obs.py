"""
临时 Web 工具：查看各城市 obs + METAR 观测数据。
运行：python web_obs.py
访问：http://localhost:5050

启动时：全量拉取所有城市当天 METAR 数据存入 SQLite。
后台线程：每 5 分钟轮询一次，自动感知城市本地跨天。
切换日期：若目标日期无数据则即时补拉，有数据则直接读库。
"""
import logging
import math
import sqlite3
import threading
import time
from datetime import datetime, timezone

import requests
from flask import Flask, render_template_string, request
from zoneinfo import ZoneInfo

import database as db
from cities import CITIES
from config import WU_API_KEY

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

V1_BASE    = "https://api.weather.com/v1/location/{icao}:9:{country}/observations/historical.json"
POLL_INTERVAL = 5 * 60  # 秒


# ── V1 API 拉取 ───────────────────────────────────────────────────────

def _fetch_v1(icao: str, country: str, date_str: str):
    """
    调用 WU V1 历史 API，返回 (obs_list, error_msg)。
    obs_list 每项：{"obs_time": "YYYY-MM-DD HH:MM:SS", "temperature": float|None}
    """
    url          = V1_BASE.format(icao=icao, country=country)
    date_compact = date_str.replace("-", "")
    try:
        resp = _SESSION.get(
            url,
            params={"apiKey": WU_API_KEY, "units": "m",
                    "startDate": date_compact, "endDate": date_compact},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return [], str(e)

    result = []
    for o in data.get("observations", []):
        ts   = o.get("valid_time_gmt")
        temp = o.get("temp")
        if ts is None:
            continue
        obs_time = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        result.append({"obs_time": obs_time, "temperature": temp})
    return result, ""


def fetch_and_store(city: dict, date_str: str) -> tuple:
    """拉取指定城市指定日期的 METAR 并写库，返回 (新增条数, error_msg)。"""
    obs_list, err = _fetch_v1(city["icao"], city["country"], date_str)
    if err:
        return 0, err
    inserted = db.insert_metar_observations(city["icao"], obs_list)
    return inserted, ""


# ── 启动全量初始化 ────────────────────────────────────────────────────

def init_metar_all():
    """程序启动时，为所有城市拉取当天 METAR 并写库。"""
    logger.info("[metar] 启动全量初始化，共 %d 个城市", len(CITIES))
    total_new = 0
    for city in CITIES:
        today = _city_local_date(city)
        inserted, err = fetch_and_store(city, today)
        if err:
            logger.error("[metar] 初始化失败 %s (%s): %s", city["name"], city["icao"], err)
        else:
            total_new += inserted
            logger.info("[metar] %s (%s) %s → +%d 条",
                        city["name"], city["icao"], today, inserted)
    logger.info("[metar] 初始化完成，共新增 %d 条", total_new)


# ── 后台轮询线程 ──────────────────────────────────────────────────────

def _city_local_date(city: dict) -> str:
    return datetime.now(ZoneInfo(city["timezone"])).strftime("%Y-%m-%d")


def _poll_loop():
    """
    每 5 分钟对所有城市拉取当天 METAR。
    自动感知城市本地跨天：当城市本地日期变化时，切换到新的一天继续拉取。
    """
    # 记录每个城市上次轮询使用的本地日期
    last_date: dict = {}

    logger.info("[metar] 后台轮询线程启动，间隔 %d 秒", POLL_INTERVAL)
    while True:
        time.sleep(POLL_INTERVAL)
        for city in CITIES:
            icao  = city["icao"]
            today = _city_local_date(city)

            if last_date.get(icao) != today:
                logger.info("[metar] %s 切换到新的一天 %s", city["name"], today)

            inserted, err = fetch_and_store(city, today)
            if err:
                logger.error("[metar] 轮询失败 %s (%s): %s", city["name"], icao, err)
            elif inserted > 0:
                logger.info("[metar] %s (%s) %s → +%d 条新数据",
                            city["name"], icao, today, inserted)

            last_date[icao] = today


def start_poll_thread():
    t = threading.Thread(target=_poll_loop, daemon=True, name="metar-poller")
    t.start()
    return t


# ── DB 查询 ───────────────────────────────────────────────────────────

def query_obs(icao: str, date_str: str):
    conn = sqlite3.connect(db.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        """
        SELECT obs_time, poll_time, temperature, temp_max_since_7am
        FROM observations
        WHERE city_icao = ? AND date(obs_time) = ?
        ORDER BY obs_time DESC
        """,
        (icao, date_str),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def query_metar(icao: str, date_str: str):
    """从库中读取 METAR，不足时即时补拉一次（仅当该日期完全无数据）。"""
    if not db.has_metar_data(icao, date_str):
        city = _CITY_MAP.get(icao)
        if city:
            logger.info("[metar] %s %s 无数据，即时补拉", city["name"], date_str)
            fetch_and_store(city, date_str)

    return db.get_metar_observations(icao, date_str)


# ── METAR 时段解析 ────────────────────────────────────────────────────

def parse_metar_periods(metar_rows: list):
    """
    从数据库返回的 METAR 列表（降序）中确定当前时段和前一时段。

    当前 30 分钟窗口起始 = floor(now_utc / 30min) * 30min。
    - 最新记录 >= 当前窗口起始 → 当前时段已到
    - 否则 → 当前时段未到，显示 "—"
    """
    if not metar_rows:
        return None, None

    now     = datetime.now(timezone.utc)
    minutes = (now.hour * 60 + now.minute) // 30 * 30
    window_start = now.replace(
        hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0
    )

    latest    = metar_rows[0]   # 降序，最新在前
    latest_dt = datetime.strptime(
        latest["obs_time"], "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=timezone.utc)

    def _fmt(row):
        return {
            "temp":     row["temperature"],
            "time_str": datetime.strptime(
                row["obs_time"], "%Y-%m-%d %H:%M:%S"
            ).strftime("%H:%M UTC"),
        }

    if latest_dt >= window_start:
        curr = {**_fmt(latest), "arrived": True}
        prev = _fmt(metar_rows[1]) if len(metar_rows) >= 2 else None
    else:
        curr = {
            "temp": None,
            "time_str": window_start.strftime("%H:%M UTC"),
            "arrived": False,
        }
        prev = _fmt(latest)

    return curr, prev


# ── 均温质量校验 ──────────────────────────────────────────────────────

def calc_avg(rows):
    if len(rows) < 2:
        return None, "数据不足（< 2 条）"

    now   = datetime.now(timezone.utc)
    t1_dt = datetime.strptime(rows[0]["obs_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    t2_dt = datetime.strptime(rows[1]["obs_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

    t2_age = (now - t2_dt).total_seconds() / 60
    gap    = (t1_dt - t2_dt).total_seconds() / 60
    avg    = math.floor((rows[0]["temperature"] + rows[1]["temperature"]) / 2)

    warnings = []
    if t2_age > 23:
        warnings.append(f"⚠️ 较老数据已 {t2_age:.1f} 分钟（超过23分钟阈值）")
    if gap <= 9:
        warnings.append(f"⚠️ 两条间隔仅 {gap:.1f} 分钟（需 > 9 分钟）")

    return avg, ("、".join(warnings) if warnings else "✅ 数据质量正常")


# ── HTML 模板 ─────────────────────────────────────────────────────────

TEMPLATE = """
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Obs 数据查看</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #f5f5f5; color: #333; padding: 24px; }
  h1 { font-size: 1.2rem; font-weight: 600; margin-bottom: 16px; color: #111; }
  h2 { font-size: 0.85rem; font-weight: 600; color: #555; margin-bottom: 10px;
       text-transform: uppercase; letter-spacing: .04em; }

  .card { background: #fff; border-radius: 8px; padding: 16px;
          margin-bottom: 16px; border: 1px solid #ddd; }

  .form-row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
  label { font-size: 0.8rem; color: #666; display: block; margin-bottom: 4px; }
  select, input[type=date] { border: 1px solid #ccc; border-radius: 4px;
    color: #333; padding: 6px 10px; font-size: 0.9rem; background: #fff; }
  button { background: #2563eb; color: #fff; border: none; border-radius: 4px;
           padding: 7px 18px; font-size: 0.9rem; cursor: pointer; }
  button:hover { background: #1d4ed8; }
  .city-tz { font-size: 0.78rem; color: #888; margin-top: 8px; }

  .stats-row { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
  .stat { background: #fff; border: 1px solid #ddd; border-radius: 6px;
          padding: 10px 14px; min-width: 120px; }
  .stat-label { font-size: 0.7rem; color: #888; margin-bottom: 2px; }
  .stat-value { font-size: 1.4rem; font-weight: 700; color: #111; }
  .stat-sub   { font-size: 0.72rem; color: #aaa; margin-top: 2px; }
  .stat.metar-ok   { border-color: #2563eb; }
  .stat.metar-wait { border-color: #d1d5db; }
  .stat.metar-ok   .stat-value { color: #2563eb; }
  .stat.metar-wait .stat-value { color: #9ca3af; }
  .divider { width: 1px; background: #e5e7eb; margin: 0 4px; align-self: stretch; }

  .quality { font-size: 0.82rem; padding: 7px 12px; border-radius: 4px;
             margin-bottom: 12px; border: 1px solid #ccc; color: #555; background: #fff; }
  .quality.ok   { border-color: #16a34a; color: #15803d; }
  .quality.warn { border-color: #d97706; color: #b45309; }

  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th { text-align: left; padding: 8px 12px; color: #666; font-size: 0.75rem;
       border-bottom: 2px solid #e5e5e5; white-space: nowrap; }
  td { padding: 8px 12px; border-bottom: 1px solid #f0f0f0; color: #333; white-space: nowrap; }
  tr:first-child td { font-weight: 600; }
  tr:hover td { background: #f9f9f9; }

  .badge { display: inline-block; padding: 1px 8px; border-radius: 4px;
           font-size: 0.7rem; font-weight: 600; }
  .badge-new    { background: #dbeafe; color: #1d4ed8; }
  .badge-used   { background: #f3f4f6; color: #6b7280; }
  .badge-curr   { background: #dbeafe; color: #1d4ed8; }
  .badge-prev   { background: #fef3c7; color: #92400e; }

  .empty { color: #aaa; text-align: center; padding: 32px; }
</style>
</head>
<body>
<h1>🌡 Obs 数据查看</h1>

<div class="card">
  <form method="get" action="/">
    <div class="form-row">
      <div>
        <label>城市</label>
        <select name="icao">
          {% for c in cities %}
          <option value="{{ c.icao }}" {% if c.icao == selected_icao %}selected{% endif %}>
            {{ c.name_cn }} ({{ c.name }}) — {{ c.icao }}
          </option>
          {% endfor %}
        </select>
      </div>
      <div>
        <label>日期（城市本地）</label>
        <input type="date" name="date" value="{{ selected_date }}">
      </div>
      <div><button type="submit">查询</button></div>
    </div>
    {% if city %}
    <div class="city-tz">时区：{{ city.timezone }} &nbsp;|&nbsp; 城市今日：{{ local_today }}</div>
    {% endif %}
  </form>
</div>

{% if city %}

{# ── 统计卡片 ── #}
<div class="stats-row">
  <div class="stat">
    <div class="stat-label">obs 记录条数</div>
    <div class="stat-value">{{ rows | length }}</div>
  </div>
  {% if rows %}
  <div class="stat">
    <div class="stat-label">最新 WU 温度</div>
    <div class="stat-value">{{ rows[0].temperature }}°C</div>
    <div class="stat-sub">{{ rows[0].obs_time }}</div>
  </div>
  {% if rows | length >= 2 %}
  <div class="stat">
    <div class="stat-label">均温 ⌊avg⌋</div>
    <div class="stat-value">{{ avg_temp }}°C</div>
  </div>
  {% endif %}
  {% endif %}

  <div class="divider"></div>

  {# METAR 卡片 #}
  {% if prev_period %}
  <div class="stat">
    <div class="stat-label">METAR 前一时段</div>
    <div class="stat-value">{{ prev_period.temp if prev_period.temp is not none else "—" }}{% if prev_period.temp is not none %}°C{% endif %}</div>
    <div class="stat-sub">{{ prev_period.time_str }}</div>
  </div>
  {% endif %}
  {% if curr_period %}
  <div class="stat {% if curr_period.arrived %}metar-ok{% else %}metar-wait{% endif %}">
    <div class="stat-label">METAR 当前时段</div>
    <div class="stat-value">
      {% if curr_period.arrived %}{{ curr_period.temp }}°C{% else %}—{% endif %}
    </div>
    <div class="stat-sub">
      {{ curr_period.time_str }}{% if not curr_period.arrived %}（未到）{% endif %}
    </div>
  </div>
  {% endif %}
  {% if not prev_period and not curr_period %}
  <div class="stat">
    <div class="stat-label">METAR</div>
    <div class="stat-value" style="font-size:0.9rem; color:#aaa;">暂无数据</div>
  </div>
  {% endif %}
</div>

{% if rows | length >= 2 %}
<div class="quality {% if '⚠️' in quality_status %}warn{% else %}ok{% endif %}">
  {{ quality_status }}
</div>
{% endif %}

{# ── WU obs 表格 ── #}
<div class="card">
  {% if rows %}
  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>obs_time (UTC)</th>
        <th>温度 (°C)</th>
        <th>当日最高 (°C)</th>
        <th>poll_time (UTC)</th>
        <th>均值参与</th>
      </tr>
    </thead>
    <tbody>
      {% for r in rows %}
      <tr>
        <td>{{ loop.index }}</td>
        <td>{{ r.obs_time }}</td>
        <td>{{ r.temperature }}</td>
        <td>{{ r.temp_max_since_7am if r.temp_max_since_7am is not none else "—" }}</td>
        <td style="color:#aaa; font-size:0.8rem;">{{ r.poll_time }}</td>
        <td>
          {% if loop.index <= 2 %}
          <span class="badge badge-new">参与均值</span>
          {% else %}
          <span class="badge badge-used">历史</span>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <div class="empty">该城市该日期暂无 obs 数据</div>
  {% endif %}
</div>

{# ── METAR 历史表格 ── #}
<div class="card">
  <h2>METAR 当日历史（{{ metar_rows | length }} 条）</h2>
  {% if metar_rows %}
  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>obs_time (UTC)</th>
        <th>温度 (°C)</th>
        <th>时段</th>
      </tr>
    </thead>
    <tbody>
      {% for m in metar_rows %}
      <tr>
        <td>{{ loop.index }}</td>
        <td>{{ m.obs_time }}</td>
        <td>{{ m.temperature if m.temperature is not none else "—" }}</td>
        <td>
          {% if loop.index == 1 and curr_period and curr_period.arrived %}
            <span class="badge badge-curr">当前时段</span>
          {% elif (loop.index == 2 and curr_period and curr_period.arrived) or
                  (loop.index == 1 and curr_period and not curr_period.arrived) %}
            <span class="badge badge-prev">前一时段</span>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <div class="empty">暂无 METAR 数据</div>
  {% endif %}
</div>

{% endif %}
</body>
</html>
"""


# ── Flask 路由 ────────────────────────────────────────────────────────

@app.route("/")
def index():
    icao = request.args.get("icao", CITIES[0]["icao"])
    city = _CITY_MAP.get(icao)
    if not city:
        city = CITIES[0]
        icao = city["icao"]

    local_today = datetime.now(ZoneInfo(city["timezone"])).strftime("%Y-%m-%d")
    date_str    = request.args.get("date", local_today)

    rows              = query_obs(icao, date_str)
    avg_temp, quality = calc_avg(rows)
    metar_rows        = query_metar(icao, date_str)   # 无数据时自动补拉
    curr_period, prev_period = parse_metar_periods(metar_rows)

    return render_template_string(
        TEMPLATE,
        cities         = CITIES,
        selected_icao  = icao,
        selected_date  = date_str,
        city           = city,
        local_today    = local_today,
        rows           = rows,
        avg_temp       = avg_temp,
        quality_status = quality,
        metar_rows     = metar_rows,
        curr_period    = curr_period,
        prev_period    = prev_period,
    )


# ── 入口 ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db.init_db()

    # 启动全量初始化（同步，确保首次访问前数据就绪）
    init_metar_all()

    # 启动后台轮询线程
    start_poll_thread()

    print("启动 Obs 数据查看工具：http://localhost:5050")
    app.run(host="0.0.0.0", port=5050, debug=False)
