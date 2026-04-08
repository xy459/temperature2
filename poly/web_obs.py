"""
临时 Web 工具：查看各城市 obs 观测数据。
运行：python web_obs.py
访问：http://localhost:5050
"""
import math
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, render_template_string, request

from cities import CITIES

DB_PATH = "poly.db"
app = Flask(__name__)

# ICAO → city dict 映射
_CITY_MAP = {c["icao"]: c for c in CITIES}


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def query_obs(icao: str, date_str: str):
    """查询指定城市指定日期（UTC obs_time 日期）的所有观测记录，降序。"""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT obs_time, poll_time, temperature, temp_max_since_7am
        FROM observations
        WHERE city_icao = ?
          AND date(obs_time) = ?
        ORDER BY obs_time DESC
        """,
        (icao, date_str),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def calc_avg(rows):
    """取最新两条计算均温（向下取整），同时返回质量状态。"""
    if len(rows) < 2:
        return None, "数据不足（< 2 条）"

    from datetime import timezone
    now = datetime.now(timezone.utc)

    t1_dt = datetime.strptime(rows[0]["obs_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    t2_dt = datetime.strptime(rows[1]["obs_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

    t2_age   = (now - t2_dt).total_seconds() / 60
    gap      = (t1_dt - t2_dt).total_seconds() / 60
    avg      = math.floor((rows[0]["temperature"] + rows[1]["temperature"]) / 2)

    warnings = []
    if t2_age > 23:
        warnings.append(f"⚠️ 较老数据已 {t2_age:.1f} 分钟（超过23分钟阈值）")
    if gap <= 9:
        warnings.append(f"⚠️ 两条间隔仅 {gap:.1f} 分钟（需 > 9 分钟）")

    status = "、".join(warnings) if warnings else "✅ 数据质量正常"
    return avg, status


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
  select, input[type=date] {
    border: 1px solid #ccc; border-radius: 4px;
    color: #333; padding: 6px 10px; font-size: 0.9rem; background: #fff;
  }
  button {
    background: #2563eb; color: #fff; border: none; border-radius: 4px;
    padding: 7px 18px; font-size: 0.9rem; cursor: pointer;
  }
  button:hover { background: #1d4ed8; }

  .city-tz { font-size: 0.78rem; color: #888; margin-top: 8px; }

  .summary-grid { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
  .stat { background: #fff; border: 1px solid #ddd; border-radius: 6px;
          padding: 12px 16px; min-width: 130px; }
  .stat-label { font-size: 0.72rem; color: #888; margin-bottom: 2px; }
  .stat-value { font-size: 1.5rem; font-weight: 700; color: #111; }

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
  .badge-new  { background: #dbeafe; color: #1d4ed8; }
  .badge-used { background: #f3f4f6; color: #6b7280; }

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
      <div>
        <button type="submit">查询</button>
      </div>
    </div>
    {% if city %}
    <div class="city-tz">时区：{{ city.timezone }} &nbsp;|&nbsp; 城市今日：{{ local_today }}</div>
    {% endif %}
  </form>
</div>

{% if city %}
<div class="summary-grid">
  <div class="stat">
    <div class="stat-label">记录条数</div>
    <div class="stat-value">{{ rows | length }}</div>
  </div>
  {% if rows %}
  <div class="stat">
    <div class="stat-label">最新温度</div>
    <div class="stat-value">{{ rows[0].temperature }}°C</div>
  </div>
  {% if rows | length >= 2 %}
  <div class="stat">
    <div class="stat-label">均温（⌊avg⌋）</div>
    <div class="stat-value">{{ avg_temp }}°C</div>
  </div>
  {% endif %}
  <div class="stat">
    <div class="stat-label">最新 obs 时间 (UTC)</div>
    <div class="stat-value" style="font-size:0.9rem; padding-top:4px;">{{ rows[0].obs_time }}</div>
  </div>
  {% endif %}
</div>

{% if rows | length >= 2 %}
<div class="quality {% if '⚠️' in quality_status %}warn{% else %}ok{% endif %}">
  {{ quality_status }}
</div>
{% endif %}

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
{% endif %}

</body>
</html>
"""


@app.route("/")
def index():
    icao = request.args.get("icao", CITIES[0]["icao"])
    city = _CITY_MAP.get(icao)

    if not city:
        city = CITIES[0]
        icao = city["icao"]

    local_today = datetime.now(ZoneInfo(city["timezone"])).strftime("%Y-%m-%d")
    date_str    = request.args.get("date", local_today)

    rows          = query_obs(icao, date_str)
    avg_temp, quality_status = calc_avg(rows)

    return render_template_string(
        TEMPLATE,
        cities         = CITIES,
        selected_icao  = icao,
        selected_date  = date_str,
        city           = city,
        local_today    = local_today,
        rows           = rows,
        avg_temp       = avg_temp,
        quality_status = quality_status,
    )


if __name__ == "__main__":
    print("启动 Obs 数据查看工具：http://localhost:5050")
    app.run(host="0.0.0.0", port=5050, debug=False)
