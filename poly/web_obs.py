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
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta

import requests
from flask import Flask, render_template_string, request, jsonify
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

V1_BASE       = "https://api.weather.com/v1/location/{icao}:9:{country}/observations/historical.json"
NOAA_BASE     = "https://aviationweather.gov/api/data/metar?ids={icao}&format=raw&taf=false"
POLL_INTERVAL = 5 * 60  # 秒

# 匹配 METAR 温度/露点组，如 18/06、M02/M10、09/M03
_NOAA_TEMP_RE = re.compile(r'\b(M?\d{1,2})/(M?\d{1,2})\b')


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
    """拉取指定城市指定日期的 WU METAR 并写库，返回 (新增条数, error_msg)。"""
    obs_list, err = _fetch_v1(city["icao"], city["country"], date_str)
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


# ── 启动全量初始化 ────────────────────────────────────────────────────

def init_metar_all():
    """程序启动时，为所有城市拉取当天 WU METAR 并写库。"""
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

            # NOAA METAR 轮询
            is_new, err = fetch_and_store_noaa(icao)
            if err:
                logger.debug("[noaa] 轮询失败 %s (%s): %s", city["name"], icao, err)
            elif is_new:
                logger.info("[noaa] %s (%s) → 新 METAR", city["name"], icao)

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
    """从库中读取 WU METAR，不足时即时补拉一次（仅当该日期完全无数据）。"""
    if not db.has_metar_data(icao, date_str):
        city = _CITY_MAP.get(icao)
        if city:
            logger.info("[metar] %s %s 无数据，即时补拉", city["name"], date_str)
            fetch_and_store(city, date_str)

    return db.get_metar_observations(icao, date_str)


def query_noaa_metar(icao: str, date_str: str):
    """从库中读取 NOAA METAR；若查询今天且无数据，则即时拉取一次。"""
    rows = db.get_noaa_metar_observations(icao, date_str)
    if not rows:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if date_str == today:
            fetch_and_store_noaa(icao)
            rows = db.get_noaa_metar_observations(icao, date_str)
    return rows


# ── METAR 与 obs 关联 ─────────────────────────────────────────────────

def _window_start(obs_dt: datetime) -> datetime:
    """返回 obs_dt 所在 30 分钟窗口的起始时间（精确到分，秒归零）。"""
    total_min    = obs_dt.hour * 60 + obs_dt.minute
    window_min   = (total_min // 30) * 30
    return obs_dt.replace(
        hour=window_min // 60, minute=window_min % 60,
        second=0, microsecond=0,
    )


def _build_metar_list(rows: list) -> list:
    """将 DB METAR 记录转为 sorted [(datetime, temperature)] 列表。"""
    result = []
    for m in rows:
        try:
            dt = datetime.strptime(m["obs_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            result.append((dt, m["temperature"]))
        except Exception:
            continue
    result.sort()
    return result


def _find_in_window(metar_list: list, start: datetime, end: datetime):
    """返回落在 [start, end) 窗口内的第一条 (temperature, utc_iso_str)，无则 (None, None)。"""
    for dt, temp in metar_list:
        if start <= dt < end:
            return temp, dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return None, None


def enrich_obs_with_metar(obs_rows: list, metar_rows: list, noaa_rows: list) -> list:
    """
    为每条 obs 记录附加 WU METAR（当前/上一时段）和 NOAA METAR（当前时段）字段。
    时间戳采用区间查找 [window_start, window_start+30min)，兼容非整点报文。
    """
    wu_list   = _build_metar_list(metar_rows)
    noaa_list = _build_metar_list(noaa_rows)

    enriched = []
    for row in obs_rows:
        r      = dict(row)
        obs_dt = datetime.strptime(row["obs_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

        curr_start = _window_start(obs_dt)
        prev_start = curr_start - timedelta(minutes=30)
        curr_end   = curr_start + timedelta(minutes=30)

        r["curr_metar_temp"], r["curr_metar_time"] = _find_in_window(wu_list,   curr_start, curr_end)
        r["prev_metar_temp"], r["prev_metar_time"] = _find_in_window(wu_list,   prev_start, curr_start)
        r["noaa_metar_temp"], r["noaa_metar_time"] = _find_in_window(noaa_list, curr_start, curr_end)
        enriched.append(r)

    return enriched


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

  .nav { margin-bottom: 16px; font-size: 0.85rem; }
  .nav a { color: #2563eb; text-decoration: none; }
  .nav a:hover { text-decoration: underline; }

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
    margin-bottom: 10px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .chart-subtitle {
    font-size: 0.72rem;
    color: #999;
    font-weight: 400;
    margin-left: 5px;
  }

  .chart-wrap {
    position: relative;
    height: 180px;
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

<div class="nav">← <a href="/">返回 Obs 数据查看</a></div>
<h1>🌡 城市温度折线图（NOAA METAR）</h1>

<div class="card">
  <div class="form-row">
    <div>
      <label>日期（各城市本地日期）</label>
      <input type="date" id="date-picker" value="{{ selected_date }}">
    </div>
    <div><button onclick="loadCharts()">查询</button></div>
  </div>
</div>

<div id="charts-container">
  <div class="loading-state">加载中…</div>
</div>

<script>
/* UTC 字符串 → 该城市本地时间，以"假 UTC"形式返回 Date 对象（供 Chart.js 时间轴使用）。
   原理：将 UTC 时间转成城市本地时间字符串，再解析为 Date（当作 UTC 处理），
   这样 Chart.js 的 min/max 也以同样方式设置，整条轴就是本地时间刻度。 */
function toFakeUTC(utcStr, tz) {
  const d = new Date(utcStr.replace(' ', 'T') + 'Z');
  const localStr = d.toLocaleString('sv-SE', { timeZone: tz }); // "YYYY-MM-DD HH:MM:SS"
  return new Date(localStr.replace(' ', 'T') + 'Z');
}

const _chartInstances = [];

async function loadCharts() {
  const date = document.getElementById('date-picker').value;
  if (!date) return;

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

  const dayStart = new Date(date + 'T00:00:00Z');
  const dayEnd   = new Date(date + 'T23:59:59Z');

  json.cities.forEach((city, idx) => {
    const card = document.createElement('div');
    card.className = 'chart-card';

    const points = (city.data || [])
      .filter(d => d.temperature !== null && d.temperature !== undefined)
      .map(d => ({ x: toFakeUTC(d.obs_time, city.timezone), y: d.temperature }));

    const bodyHtml = points.length === 0
      ? '<div class="no-data">暂无数据</div>'
      : '<canvas id="cv-' + idx + '"></canvas>';

    card.innerHTML =
      '<div class="chart-title">' + city.name_cn +
      '<span class="chart-subtitle">' + city.name + ' · ' + city.icao + '</span></div>' +
      '<div class="chart-wrap">' + bodyHtml + '</div>';
    grid.appendChild(card);

    if (points.length === 0) return;

    const ctx = document.getElementById('cv-' + idx).getContext('2d');
    const chart = new Chart(ctx, {
      type: 'line',
      data: {
        datasets: [{
          data: points,
          borderColor: '#2563eb',
          backgroundColor: 'rgba(37,99,235,0.07)',
          borderWidth: 1.8,
          pointRadius: 2.5,
          pointHoverRadius: 5,
          pointBackgroundColor: '#2563eb',
          fill: true,
          tension: 0.35,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => {
                const dt = new Date(items[0].parsed.x);
                const h  = String(dt.getUTCHours()).padStart(2, '0');
                const m  = String(dt.getUTCMinutes()).padStart(2, '0');
                return h + ':' + m + ' 本地';
              },
              label: (item) => item.parsed.y + '°C',
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
              callback: (v) => v + '°',
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
}

document.addEventListener('DOMContentLoaded', loadCharts);
</script>
</body>
</html>
"""

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

  /* 质量状态：无方框，纯文字 */
  .quality      { font-size: 0.82rem; margin-bottom: 12px; }
  .quality.ok   { color: #15803d; }
  .quality.warn { color: #b45309; }

  /* 表格 */
  .table-header { display: flex; justify-content: space-between; align-items: center;
                  margin-bottom: 8px; }
  .tz-toggle { font-size: 0.78rem; color: #2563eb; cursor: pointer;
               user-select: none; border: 1px solid #2563eb; border-radius: 4px;
               padding: 3px 10px; }
  .tz-toggle:hover { background: #eff6ff; }

  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th { text-align: left; padding: 8px 12px; color: #666; font-size: 0.75rem;
       border-bottom: 2px solid #e5e5e5; white-space: nowrap; }
  td { padding: 8px 12px; border-bottom: 1px solid #f0f0f0; color: #333; white-space: nowrap; }
  tr:first-child td { font-weight: 600; }
  tr:hover td { background: #f9f9f9; }

  .badge { display: inline-block; padding: 1px 8px; border-radius: 4px;
           font-size: 0.7rem; font-weight: 600; }
  .badge-new  { background: #dbeafe; color: #1d4ed8; }
  .badge-used { background: #f3f4f6; color: #6b7280; }

  .metar-val      { color: #111; }
  .metar-val.none { color: #bbb; }
  .metar-time     { font-size: 0.75rem; color: #aaa; display: block; }

  .empty { color: #aaa; text-align: center; padding: 32px; }
</style>
</head>
<body>
<div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:16px;">
  <h1 style="margin-bottom:0;">🌡 Obs 数据查看</h1>
  <a href="/charts" style="font-size:0.85rem; color:#2563eb; text-decoration:none;
     border:1px solid #2563eb; border-radius:4px; padding:5px 14px;">
    📈 折线图总览
  </a>
</div>

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

{# ── 统计卡片（仅 obs 相关）── #}
<div class="stats-row">
  <div class="stat">
    <div class="stat-label">obs 记录条数</div>
    <div class="stat-value">{{ rows | length }}</div>
  </div>
  {% if rows %}
  <div class="stat">
    <div class="stat-label">最新 WU 温度</div>
    <div class="stat-value">{{ rows[0].temperature }}°C</div>
    <div class="stat-sub" id="latest-obs-time" data-utc="{{ rows[0].obs_time }}">{{ rows[0].obs_time }}</div>
  </div>
  {% if rows | length >= 2 %}
  <div class="stat">
    <div class="stat-label">均温 ⌊avg⌋</div>
    <div class="stat-value">{{ avg_temp }}°C</div>
  </div>
  {% endif %}
  {% endif %}
</div>

{% if rows | length >= 2 %}
<div class="quality {% if '⚠️' in quality_status %}warn{% else %}ok{% endif %}">
  {{ quality_status }}
</div>
{% endif %}

{# ── obs 表格（含 METAR 两列）── #}
<div class="card">
  {% if rows %}
  <div class="table-header">
    <span id="tz-label" style="font-size:0.8rem; color:#666;">时间：本地时间（{{ city_tz }}）</span>
    <span class="tz-toggle" id="tz-btn" onclick="toggleTz()">切换 UTC</span>
  </div>
  <table>
    <thead>
      <tr>
        <th>#</th>
        <th id="th-obs">obs_time</th>
        <th>温度 (°C)</th>
        <th>当日最高 (°C)</th>
        <th id="th-poll">poll_time</th>
        <th>⌊均温⌋</th>
        <th>上一时段 METAR</th>
        <th>当前时段 METAR</th>
        <th>NOAA METAR</th>
      </tr>
    </thead>
    <tbody>
      {% for r in rows %}
      <tr>
        <td>{{ loop.index }}</td>
        <td class="tc obs-time" data-utc="{{ r.obs_time }}">{{ r.obs_time }}</td>
        <td>{{ r.temperature }}</td>
        <td>{{ r.temp_max_since_7am if r.temp_max_since_7am is not none else "—" }}</td>
        <td class="tc poll-time" style="color:#aaa; font-size:0.8rem;" data-utc="{{ r.poll_time }}">{{ r.poll_time }}</td>
        <td>
          {% if r.row_avg is not none %}
            <span style="font-weight:600; color:{% if loop.index <= 2 %}#1d4ed8{% else %}#9ca3af{% endif %};">{{ r.row_avg }}°C</span>
          {% else %}
            <span style="color:#bbb;">—</span>
          {% endif %}
        </td>
        <td>
          {% if r.prev_metar_temp is not none %}
            <span class="metar-val">{{ r.prev_metar_temp }}°C</span>
            <span class="metar-time tc" data-utc="{{ r.prev_metar_time }}">{{ r.prev_metar_time }}</span>
          {% else %}
            <span class="metar-val none">—</span>
          {% endif %}
        </td>
        <td>
          {% if r.curr_metar_temp is not none %}
            <span class="metar-val">{{ r.curr_metar_temp }}°C</span>
            <span class="metar-time tc" data-utc="{{ r.curr_metar_time }}">{{ r.curr_metar_time }}</span>
          {% else %}
            <span class="metar-val none">—</span>
          {% endif %}
        </td>
        <td>
          {% if r.noaa_metar_temp is not none %}
            <span class="metar-val">{{ r.noaa_metar_temp }}°C</span>
            <span class="metar-time tc" data-utc="{{ r.noaa_metar_time }}">{{ r.noaa_metar_time }}</span>
          {% else %}
            <span class="metar-val none">—</span>
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

{% endif %}

<script>
const CITY_TZ = {{ city_tz | tojson }};
let showUtc = false;

/* UTC 字符串 ("YYYY-MM-DD HH:MM:SS" 或 "YYYY-MM-DDTHH:MM:SSZ") → 转为指定时区的显示字符串 */
function toLocal(utcStr, tz) {
  const s = utcStr.includes('T') ? utcStr : utcStr.replace(' ', 'T') + 'Z';
  const d = new Date(s);
  if (isNaN(d)) return utcStr;
  return d.toLocaleString('sv-SE', { timeZone: tz,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit' }).replace('T', ' ');
}

/* METAR 时间只显示 HH:MM */
function toLocalHHMM(utcStr, tz) {
  const s = utcStr.includes('T') ? utcStr : utcStr.replace(' ', 'T') + 'Z';
  const d = new Date(s);
  if (isNaN(d)) return utcStr;
  return d.toLocaleTimeString('sv-SE', { timeZone: tz, hour: '2-digit', minute: '2-digit' });
}

function applyTz() {
  /* obs_time / poll_time 列：完整 datetime */
  document.querySelectorAll('.tc.obs-time, .tc.poll-time').forEach(el => {
    const utc = el.dataset.utc;
    el.textContent = showUtc ? utc : toLocal(utc, CITY_TZ);
  });
  /* METAR 时间：只显示 HH:MM */
  document.querySelectorAll('.metar-time.tc').forEach(el => {
    const utc = el.dataset.utc;
    if (!utc) return;
    el.textContent = showUtc
      ? utc.includes('T') ? utc.slice(11, 16) + ' UTC' : utc
      : toLocalHHMM(utc, CITY_TZ) + ' 本地';
  });
  /* 统计卡最新时间 */
  const lt = document.getElementById('latest-obs-time');
  if (lt) lt.textContent = showUtc ? lt.dataset.utc : toLocal(lt.dataset.utc, CITY_TZ);

  /* 标签 */
  document.getElementById('tz-label').textContent =
    showUtc ? '时间：UTC' : `时间：本地时间（${CITY_TZ}）`;
  document.getElementById('tz-btn').textContent =
    showUtc ? '切换本地时间' : '切换 UTC';
}

function toggleTz() { showUtc = !showUtc; applyTz(); }

/* 页面加载默认显示本地时间 */
document.addEventListener('DOMContentLoaded', applyTz);
</script>
</body>
</html>
"""


# ── Flask 路由 ────────────────────────────────────────────────────────

@app.route("/charts")
def charts():
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    selected_date = request.args.get("date", today_utc)
    return render_template_string(CHARTS_TEMPLATE, selected_date=selected_date)


@app.route("/api/charts_data")
def charts_data():
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    date_str = request.args.get("date", today_utc)

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

        rows = db.get_noaa_metar_by_utc_range(city["icao"], utc_start, utc_end)
        result.append({
            "icao":     city["icao"],
            "name":     city["name"],
            "name_cn":  city["name_cn"],
            "timezone": city["timezone"],
            "data":     rows,
        })

    return jsonify({"date": date_str, "cities": result})


@app.route("/")
def index():
    icao = request.args.get("icao", CITIES[0]["icao"])
    city = _CITY_MAP.get(icao)
    if not city:
        city = CITIES[0]
        icao = city["icao"]

    local_today = datetime.now(ZoneInfo(city["timezone"])).strftime("%Y-%m-%d")
    date_str    = request.args.get("date", local_today)

    obs_rows          = query_obs(icao, date_str)
    avg_temp, quality = calc_avg(obs_rows)
    metar_rows        = query_metar(icao, date_str)
    noaa_rows         = query_noaa_metar(icao, date_str)
    rows              = enrich_obs_with_metar(obs_rows, metar_rows, noaa_rows)

    # 为每行附加各自的滚动均温：该行与紧邻下一行（时间上更早）的 floor 均值
    for i, r in enumerate(rows):
        if i + 1 < len(rows):
            t1 = r["temperature"]
            t2 = rows[i + 1]["temperature"]
            r["row_avg"] = math.floor((t1 + t2) / 2) if (t1 is not None and t2 is not None) else None
        else:
            r["row_avg"] = None  # 最后一行无下一行

    return render_template_string(
        TEMPLATE,
        cities         = CITIES,
        selected_icao  = icao,
        selected_date  = date_str,
        city           = city,
        local_today    = local_today,
        city_tz        = city["timezone"],
        rows           = rows,
        avg_temp       = avg_temp,
        quality_status = quality,
    )


# ── 入口 ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db.init_db()

    # 启动全量初始化（同步，确保首次访问前数据就绪）
    init_metar_all()
    init_noaa_metar_all()

    # 启动后台轮询线程
    start_poll_thread()

    # 过滤外部扫描机器人发来的 TLS 握手包产生的 werkzeug 噪声日志
    class _TLSProbeFilter(logging.Filter):
        def filter(self, record):
            return "Bad request version" not in record.getMessage()

    logging.getLogger("werkzeug").addFilter(_TLSProbeFilter())

    print("启动 Obs 数据查看工具：http://localhost:5050")
    app.run(host="0.0.0.0", port=5050, debug=False)
