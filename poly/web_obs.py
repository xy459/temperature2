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
         background: #0f172a; color: #e2e8f0; min-height: 100vh; padding: 24px; }
  h1 { font-size: 1.4rem; font-weight: 600; margin-bottom: 20px; color: #f1f5f9; }

  .card { background: #1e293b; border-radius: 12px; padding: 20px;
          margin-bottom: 20px; border: 1px solid #334155; }

  .form-row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
  label { font-size: 0.8rem; color: #94a3b8; display: block; margin-bottom: 4px; }
  select, input[type=date] {
    background: #0f172a; border: 1px solid #475569; border-radius: 8px;
    color: #e2e8f0; padding: 8px 12px; font-size: 0.9rem; outline: none;
    appearance: none;
  }
  select:focus, input[type=date]:focus { border-color: #6366f1; }
  button {
    background: #6366f1; color: white; border: none; border-radius: 8px;
    padding: 9px 20px; font-size: 0.9rem; cursor: pointer; transition: background .15s;
  }
  button:hover { background: #4f46e5; }

  .summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
                  gap: 12px; margin-bottom: 20px; }
  .stat { background: #0f172a; border-radius: 8px; padding: 14px;
          border: 1px solid #334155; }
  .stat-label { font-size: 0.72rem; color: #64748b; margin-bottom: 4px; }
  .stat-value { font-size: 1.6rem; font-weight: 700; color: #f8fafc; }
  .stat-value.temp { color: #f59e0b; }
  .stat-value.avg  { color: #34d399; }

  .quality { font-size: 0.82rem; padding: 8px 14px; border-radius: 6px;
             margin-bottom: 16px; background: #1e293b; border: 1px solid #334155; }
  .quality.ok  { border-color: #34d399; color: #34d399; }
  .quality.warn { border-color: #f59e0b; color: #f59e0b; }

  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th { text-align: left; padding: 10px 12px; color: #64748b;
       font-size: 0.75rem; font-weight: 500;
       border-bottom: 1px solid #334155; white-space: nowrap; }
  td { padding: 9px 12px; border-bottom: 1px solid #1e293b;
       color: #cbd5e1; white-space: nowrap; }
  tr:first-child td { color: #f1f5f9; font-weight: 600; }
  tr:first-child td.temp-cell { color: #f59e0b; }
  tr:nth-child(2) td { color: #e2e8f0; }
  tr:nth-child(2) td.temp-cell { color: #fbbf24; }
  tr:hover td { background: #1e293b; }

  .badge { display: inline-block; padding: 2px 8px; border-radius: 999px;
           font-size: 0.7rem; font-weight: 600; }
  .badge-new  { background: #1d4ed8; color: #93c5fd; }
  .badge-used { background: #374151; color: #9ca3af; }

  .empty { color: #475569; text-align: center; padding: 40px; }
  .city-tz { font-size: 0.78rem; color: #64748b; margin-top: 4px; }
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
    <div class="stat-value temp">{{ rows[0].temperature }}°C</div>
  </div>
  {% if rows | length >= 2 %}
  <div class="stat">
    <div class="stat-label">均温（最新2条, ⌊avg⌋）</div>
    <div class="stat-value avg">{{ avg_temp }}°C</div>
  </div>
  {% endif %}
  <div class="stat">
    <div class="stat-label">最新 obs 时间（UTC）</div>
    <div class="stat-value" style="font-size:1rem; padding-top:6px;">{{ rows[0].obs_time }}</div>
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
        <td class="temp-cell">{{ r.temperature }}</td>
        <td>{{ r.temp_max_since_7am if r.temp_max_since_7am is not none else "—" }}</td>
        <td style="color:#64748b; font-size:0.8rem;">{{ r.poll_time }}</td>
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
