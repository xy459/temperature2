#!/usr/bin/env python3
"""
多渠道气象观测数据获取测试脚本
纯验证脚本，不写数据库，只确认各渠道 API 数据是否正常返回。

渠道：
  1. IEM (Iowa Environmental Mesonet) — ASOS/AWOS CSV，METAR+SPECI 解析数据
  2. WeatherAPI.com — ~10-15分钟更新频率，最接近 WU obs 替代品
  3. AVWX (avwx.rest) — 航空气象 API，METAR+SPECI 结构化 JSON
  4. NOAA ADDS (aviationweather.gov) — METAR+SPECI 原始报文（含 hours 参数）

用法：
  python multi_channel_test.py                          # 测试所有城市
  python multi_channel_test.py --city LEMD              # 只测单个城市
  python multi_channel_test.py --city LEMD --verbose    # 单城市 + 打印原始响应
  python multi_channel_test.py --channel weatherapi     # 只测某渠道
  python multi_channel_test.py --city LEMD EGLC LFPO    # 测几个城市

环境变量（.env 或 export）：
  WEATHERAPI_KEY  — weatherapi.com 免费 key（https://www.weatherapi.com/signup.aspx）
  AVWX_TOKEN      — avwx.rest 免费 token（https://account.avwx.rest）
"""

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / "poly" / ".env")
except ImportError:
    pass

# ── 城市列表（与 poly/cities.py 对齐；含莫斯科/特拉维夫/伊斯坦布尔） ──

CITIES = [
    {"name": "Madrid",       "name_cn": "马德里",         "icao": "LEMD", "country": "ES"},
    {"name": "London",       "name_cn": "伦敦",           "icao": "EGLC", "country": "GB"},
    {"name": "Paris",        "name_cn": "巴黎",           "icao": "LFPO", "country": "FR"},
    {"name": "Munich",       "name_cn": "慕尼黑",         "icao": "EDDM", "country": "DE"},
    {"name": "Milan",        "name_cn": "米兰",           "icao": "LIML", "country": "IT"},
    {"name": "Warsaw",       "name_cn": "华沙",           "icao": "EPWA", "country": "PL"},
    {"name": "Ankara",       "name_cn": "安卡拉",         "icao": "LTAC", "country": "TR"},
    {"name": "Tokyo",        "name_cn": "东京",           "icao": "RJTT", "country": "JP"},
    {"name": "Seoul",        "name_cn": "首尔",           "icao": "RKSI", "country": "KR"},
    {"name": "Shanghai",     "name_cn": "上海",           "icao": "ZSPD", "country": "CN"},
    {"name": "Beijing",      "name_cn": "北京",           "icao": "ZBAA", "country": "CN"},
    {"name": "Chongqing",    "name_cn": "重庆",           "icao": "ZUCK", "country": "CN"},
    {"name": "Wuhan",        "name_cn": "武汉",           "icao": "ZHHH", "country": "CN"},
    {"name": "Chengdu",      "name_cn": "成都",           "icao": "ZUUU", "country": "CN"},
    {"name": "Taipei",       "name_cn": "台北",           "icao": "RCTP", "country": "TW"},
    {"name": "Singapore",    "name_cn": "新加坡",         "icao": "WSSS", "country": "SG"},
    {"name": "Lucknow",      "name_cn": "勒克瑙",         "icao": "VILK", "country": "IN"},
    {"name": "Wellington",   "name_cn": "惠灵顿",         "icao": "NZWN", "country": "NZ"},
    {"name": "Toronto",      "name_cn": "多伦多",         "icao": "CYYZ", "country": "CA"},
    {"name": "Buenos Aires", "name_cn": "布宜诺斯艾利斯", "icao": "SAEZ", "country": "AR"},
    {"name": "Sao Paulo",    "name_cn": "圣保罗",         "icao": "SBGR", "country": "BR"},
    {"name": "Mexico City",  "name_cn": "墨西哥城",       "icao": "MMMX", "country": "MX"},
    {"name": "Panama City",  "name_cn": "巴拿马城",       "icao": "MPMG", "country": "PA"},
    {"name": "Moscow",       "name_cn": "莫斯科",         "icao": "UUWW", "country": "RU"},
    {"name": "Tel Aviv",     "name_cn": "特拉维夫",       "icao": "LLBG", "country": "IL"},
    {"name": "Istanbul",     "name_cn": "伊斯坦布尔",     "icao": "LTFM", "country": "TR"},
]

CITY_MAP = {c["icao"]: c for c in CITIES}

# ── 环境变量 ──────────────────────────────────────────────────────

WEATHERAPI_KEY = os.getenv("WEATHERAPI_KEY", "")
AVWX_TOKEN = os.getenv("AVWX_TOKEN", "")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "MultiChannelTest/1.0"})

_NOAA_TEMP_RE = re.compile(r"\b(M?\d{2})/(M?\d{2})\b")


# ═══════════════════════════════════════════════════════════════════
#  渠道 1: IEM (Iowa Environmental Mesonet)
#  数据: ASOS/AWOS CSV，含 METAR(report_type=3) + SPECI(report_type=4)
#  频率: ~30分钟 (METAR) + 天气变化时的 SPECI
#  无需 API Key
# ═══════════════════════════════════════════════════════════════════

def fetch_iem(icao: str) -> dict:
    now = datetime.now(timezone.utc)
    url = (
        f"https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
        f"station={icao}&data=tmpf&data=dwpf&data=relh&data=drct&data=sknt&data=metar"
        f"&tz=Etc/UTC&format=onlycomma&latlon=no&missing=M&direct=no"
        f"&report_type=3&report_type=4"
        f"&year1={now.year}&month1={now.month}&day1={now.day}"
        f"&year2={now.year}&month2={now.month}&day2={now.day}"
    )

    t0 = time.monotonic()
    resp = SESSION.get(url, timeout=15)
    resp.raise_for_status()
    http_ms = int((time.monotonic() - t0) * 1000)

    lines = resp.text.strip().split("\n")
    if len(lines) < 2:
        return {"ok": True, "obs": [], "http_ms": http_ms, "note": "today no data yet"}

    header = [h.strip() for h in lines[0].split(",")]
    col = {h: i for i, h in enumerate(header)}

    obs_list = []
    speci_count = 0
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) < len(header):
            continue
        valid_str = parts[col.get("valid", 1)].strip()
        tmpf_str = parts[col.get("tmpf", -1)].strip() if "tmpf" in col else "M"
        metar_raw = parts[col.get("metar", -1)].strip() if "metar" in col else ""

        if tmpf_str == "M" or not tmpf_str:
            continue
        try:
            temp_c = round((float(tmpf_str) - 32) * 5 / 9, 1)
        except ValueError:
            continue

        is_speci = "SPECI" in metar_raw
        if is_speci:
            speci_count += 1

        obs_list.append({
            "time": valid_str,
            "temp_c": temp_c,
            "speci": is_speci,
            "raw": metar_raw[:120],
        })

    latest = obs_list[-1] if obs_list else None
    return {
        "ok": True,
        "obs": obs_list,
        "latest": latest,
        "total": len(obs_list),
        "speci_count": speci_count,
        "http_ms": http_ms,
    }


# ═══════════════════════════════════════════════════════════════════
#  渠道 2: WeatherAPI.com
#  数据: 当前天气 JSON，~10-15分钟更新
#  频率: ~10-15分钟（最接近 WU obs 的替代品）
#  需要 API Key（免费 tier 100万次/月）
# ═══════════════════════════════════════════════════════════════════

def fetch_weatherapi(icao: str) -> dict:
    if not WEATHERAPI_KEY:
        return {"ok": False, "error": "WEATHERAPI_KEY 未设置"}

    url = "http://api.weatherapi.com/v1/current.json"
    t0 = time.monotonic()
    resp = SESSION.get(url, params={
        "key": WEATHERAPI_KEY,
        "q": f"metar:{icao}",
        "aqi": "no",
    }, timeout=10)
    resp.raise_for_status()
    http_ms = int((time.monotonic() - t0) * 1000)

    data = resp.json()
    cur = data.get("current", {})
    loc = data.get("location", {})

    temp_c = cur.get("temp_c")
    last_updated = cur.get("last_updated", "")
    last_epoch = cur.get("last_updated_epoch")
    humidity = cur.get("humidity")
    wind_kph = cur.get("wind_kph")
    wind_dir = cur.get("wind_dir")
    condition = cur.get("condition", {}).get("text", "")

    obs_utc = None
    if last_epoch:
        obs_utc = datetime.fromtimestamp(last_epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    return {
        "ok": True,
        "temp_c": temp_c,
        "obs_time_local": last_updated,
        "obs_time_utc": obs_utc,
        "humidity": humidity,
        "wind": f"{wind_kph} kph {wind_dir}",
        "condition": condition,
        "location": f"{loc.get('name')}, {loc.get('country')}",
        "tz": loc.get("tz_id"),
        "http_ms": http_ms,
    }


# ═══════════════════════════════════════════════════════════════════
#  渠道 3: AVWX (avwx.rest)
#  数据: 最新 METAR (含 SPECI) 结构化 JSON
#  频率: METAR 频率 (~30分钟) + SPECI
#  需要 API Token（免费 tier 有调用限制）
# ═══════════════════════════════════════════════════════════════════

def fetch_avwx(icao: str) -> dict:
    if not AVWX_TOKEN:
        return {"ok": False, "error": "AVWX_TOKEN 未设置"}

    url = f"https://avwx.rest/api/metar/{icao}"
    t0 = time.monotonic()
    resp = SESSION.get(url, headers={
        "Authorization": f"BEARER {AVWX_TOKEN}",
    }, timeout=10)
    resp.raise_for_status()
    http_ms = int((time.monotonic() - t0) * 1000)

    data = resp.json()
    raw = data.get("raw", "")

    temp_info = data.get("temperature", {})
    temp_c = temp_info.get("value") if isinstance(temp_info, dict) else None

    dewp_info = data.get("dewpoint", {})
    dewp_c = dewp_info.get("value") if isinstance(dewp_info, dict) else None

    time_info = data.get("time", {})
    time_repr = time_info.get("repr", "") if isinstance(time_info, dict) else ""
    time_dt = time_info.get("dt", "") if isinstance(time_info, dict) else ""

    wind_info = data.get("wind_direction", {})
    wind_dir = wind_info.get("value") if isinstance(wind_info, dict) else None
    wind_spd_info = data.get("wind_speed", {})
    wind_spd = wind_spd_info.get("value") if isinstance(wind_spd_info, dict) else None

    flight_rules = data.get("flight_rules", "")

    obs_utc = None
    if time_dt:
        try:
            dt = datetime.fromisoformat(time_dt.replace("Z", "+00:00"))
            obs_utc = dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    return {
        "ok": True,
        "temp_c": temp_c,
        "dewpoint_c": dewp_c,
        "obs_time_utc": obs_utc,
        "obs_time_repr": time_repr,
        "wind": f"{wind_dir}° {wind_spd}kt" if wind_dir is not None else None,
        "flight_rules": flight_rules,
        "raw": raw,
        "http_ms": http_ms,
    }


# ═══════════════════════════════════════════════════════════════════
#  渠道 4: NOAA ADDS (aviationweather.gov)
#  数据: 原始 METAR + SPECI 报文
#  频率: METAR (~30分钟) + SPECI（天气变化时自动发）
#  无需 API Key
#  关键参数: hours=3 拉取最近3小时，确保包含 SPECI
# ═══════════════════════════════════════════════════════════════════

def _parse_metar_temp(raw: str):
    m = _NOAA_TEMP_RE.search(raw)
    if not m:
        return None
    val = m.group(1)
    return -int(val[1:]) if val.startswith("M") else int(val)


def _parse_metar_time(raw: str):
    m = re.search(r"\b(\d{2})(\d{2})(\d{2})Z\b", raw)
    if not m:
        return None
    day, hour, minute = int(m.group(1)), int(m.group(2)), int(m.group(3))
    now = datetime.now(timezone.utc)
    try:
        dt = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
        if dt > now + timedelta(hours=1):
            if now.month == 1:
                dt = dt.replace(year=now.year - 1, month=12)
            else:
                dt = dt.replace(month=now.month - 1)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def fetch_noaa_adds(icao: str) -> dict:
    url = (
        f"https://aviationweather.gov/api/data/metar"
        f"?ids={icao}&format=raw&taf=false&hours=3"
    )

    t0 = time.monotonic()
    resp = SESSION.get(url, timeout=10)
    resp.raise_for_status()
    http_ms = int((time.monotonic() - t0) * 1000)

    body = resp.text.strip()
    lines = [l.strip() for l in body.splitlines() if l.strip() and icao in l]

    if not lines:
        return {"ok": True, "obs": [], "http_ms": http_ms, "note": "no data"}

    obs_list = []
    speci_count = 0
    seen = set()
    for raw in lines:
        obs_time = _parse_metar_time(raw)
        temp = _parse_metar_temp(raw)
        if not obs_time or obs_time in seen:
            continue
        seen.add(obs_time)

        is_speci = raw.strip().startswith("SPECI")
        if is_speci:
            speci_count += 1

        obs_list.append({
            "time": obs_time,
            "temp_c": temp,
            "speci": is_speci,
            "raw": raw[:140],
        })

    obs_list.sort(key=lambda x: x["time"])
    latest = obs_list[-1] if obs_list else None

    return {
        "ok": True,
        "obs": obs_list,
        "latest": latest,
        "total": len(obs_list),
        "speci_count": speci_count,
        "http_ms": http_ms,
    }


# ═══════════════════════════════════════════════════════════════════
#  测试执行
# ═══════════════════════════════════════════════════════════════════

CHANNEL_FUNCS = {
    "iem":        ("IEM",        fetch_iem),
    "weatherapi": ("WeatherAPI", fetch_weatherapi),
    "avwx":       ("AVWX",       fetch_avwx),
    "noaa_adds":  ("NOAA ADDS",  fetch_noaa_adds),
}


def test_one_city(icao: str, channels: list[str], verbose: bool = False) -> dict:
    """测试单个城市所有指定渠道，返回 {channel_key: result_dict}"""
    results = {}
    for ch_key in channels:
        label, func = CHANNEL_FUNCS[ch_key]
        try:
            result = func(icao)
        except requests.HTTPError as e:
            result = {"ok": False, "error": f"HTTP {e.response.status_code}: {e}"}
        except Exception as e:
            result = {"ok": False, "error": str(e)}
        results[ch_key] = result

        if verbose:
            print(f"\n    [{label}] raw response:")
            print(f"    {json.dumps(result, ensure_ascii=False, indent=2, default=str)[:800]}")

    return results


def _fmt_temp(val) -> str:
    if val is None:
        return "  —  "
    if isinstance(val, float) and val != int(val):
        return f"{val:+.1f}°C"
    return f"{int(val):+d}°C"


def _extract_hhmm(time_str: str) -> str:
    """从各种时间格式中提取 HH:MM"""
    if not time_str:
        return ""
    # "2026-04-09 15:30:00" → "15:30"
    # "2026-04-09 15:30"    → "15:30"
    m = re.search(r"(\d{2}:\d{2})", time_str)
    return m.group(1) if m else time_str[-5:]


def _extract_latest_temp(ch_key: str, result: dict):
    """从各渠道结果中提取最新温度和 HH:MM"""
    if not result.get("ok"):
        return None, None

    if ch_key in ("iem", "noaa_adds"):
        latest = result.get("latest")
        if latest:
            return latest.get("temp_c"), _extract_hhmm(latest.get("time", ""))
        return None, None

    if ch_key == "weatherapi":
        return result.get("temp_c"), _extract_hhmm(result.get("obs_time_utc", ""))

    if ch_key == "avwx":
        return result.get("temp_c"), _extract_hhmm(result.get("obs_time_utc", ""))

    return None, None


def print_summary_table(all_results: dict, channels: list[str]):
    """打印全城市对比表格"""
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    ch_labels = [CHANNEL_FUNCS[k][0] for k in channels]

    print()
    print("=" * 110)
    print(f"  多渠道温度对比 | UTC {now_utc}")
    print("=" * 110)

    # header
    hdr = f"  {'城市':<10} {'ICAO':<6}"
    for label in ch_labels:
        hdr += f" │ {label:^14}"
    hdr += " │ 温差"
    print(hdr)
    print("  " + "─" * 106)

    ok_counts = {k: 0 for k in channels}
    err_counts = {k: 0 for k in channels}

    for city in CITIES:
        icao = city["icao"]
        if icao not in all_results:
            continue

        city_res = all_results[icao]
        row = f"  {city['name_cn']:<10} {icao:<6}"
        temps = []

        for ch_key in channels:
            res = city_res.get(ch_key, {})
            if not res.get("ok"):
                err_str = (res.get("error") or "ERR")[:10]
                row += f" │ {'✗ ' + err_str:^14}"
                err_counts[ch_key] += 1
            else:
                temp, hhmm = _extract_latest_temp(ch_key, res)
                if temp is not None:
                    temps.append(temp)
                    http_ms = res.get("http_ms", 0)
                    cell = f"{_fmt_temp(temp)} {hhmm}"
                    row += f" │ {cell:^14}"
                    ok_counts[ch_key] += 1
                else:
                    row += f" │ {'— no data':^14}"
                    ok_counts[ch_key] += 1

        if len(temps) >= 2:
            diff = max(temps) - min(temps)
            row += f" │ {diff:.1f}°"
        else:
            row += f" │  —"

        print(row)

    print("  " + "─" * 106)

    # 渠道统计
    total = len(all_results)
    print(f"\n  渠道覆盖统计（共 {total} 站点）：")
    for ch_key in channels:
        label = CHANNEL_FUNCS[ch_key][0]
        ok = ok_counts[ch_key]
        err = err_counts[ch_key]
        pct = ok / total * 100 if total else 0
        status = "✅" if err == 0 else "⚠️"
        extra = ""
        if ch_key == "weatherapi" and not WEATHERAPI_KEY:
            extra = " （未配置 WEATHERAPI_KEY）"
        elif ch_key == "avwx" and not AVWX_TOKEN:
            extra = " （未配置 AVWX_TOKEN）"
        print(f"    {status} {label:<12} {ok:>2}/{total} 成功 ({pct:.0f}%)  {err} 失败{extra}")

    print()


def print_city_detail(icao: str, city_res: dict, channels: list[str]):
    """打印单个城市的详细数据"""
    city = CITY_MAP.get(icao, {"name": icao, "name_cn": icao})
    print(f"\n{'─' * 80}")
    print(f"  {city['name_cn']} ({city['name']}) — {icao}")
    print(f"{'─' * 80}")

    for ch_key in channels:
        label = CHANNEL_FUNCS[ch_key][0]
        res = city_res.get(ch_key, {})

        if not res.get("ok"):
            print(f"\n  [{label}] ✗ {res.get('error', 'unknown error')}")
            continue

        http_ms = res.get("http_ms", 0)

        if ch_key == "iem":
            total = res.get("total", 0)
            speci = res.get("speci_count", 0)
            print(f"\n  [{label}] ✓  {total} 条记录 (SPECI: {speci})  HTTP {http_ms}ms")
            obs = res.get("obs", [])
            if obs:
                print(f"    {'时间(UTC)':<20} {'温度':>7} {'SPECI':>6}  报文")
                for o in obs[-8:]:
                    sp_mark = "SPECI" if o.get("speci") else ""
                    print(f"    {o['time']:<20} {_fmt_temp(o['temp_c']):>7} {sp_mark:>6}  {o.get('raw', '')[:70]}")
                if len(obs) > 8:
                    print(f"    ... 更早 {len(obs) - 8} 条已省略")

        elif ch_key == "weatherapi":
            temp = res.get("temp_c")
            print(f"\n  [{label}] ✓  HTTP {http_ms}ms")
            print(f"    温度: {_fmt_temp(temp)}")
            print(f"    观测时间(UTC): {res.get('obs_time_utc', '?')}")
            print(f"    观测时间(本地): {res.get('obs_time_local', '?')}")
            print(f"    湿度: {res.get('humidity')}%  风: {res.get('wind')}")
            print(f"    天气: {res.get('condition')}")
            print(f"    位置: {res.get('location')}  时区: {res.get('tz')}")

        elif ch_key == "avwx":
            temp = res.get("temp_c")
            print(f"\n  [{label}] ✓  HTTP {http_ms}ms")
            print(f"    温度: {_fmt_temp(temp)}  露点: {_fmt_temp(res.get('dewpoint_c'))}")
            print(f"    观测时间(UTC): {res.get('obs_time_utc', '?')}  ({res.get('obs_time_repr')})")
            print(f"    风: {res.get('wind')}  飞行规则: {res.get('flight_rules')}")
            print(f"    原始报文: {res.get('raw', '')[:100]}")

        elif ch_key == "noaa_adds":
            total = res.get("total", 0)
            speci = res.get("speci_count", 0)
            print(f"\n  [{label}] ✓  {total} 条报文 (SPECI: {speci})  HTTP {http_ms}ms")
            obs = res.get("obs", [])
            if obs:
                print(f"    {'时间(UTC)':<20} {'温度':>7} {'SPECI':>6}  报文")
                for o in obs:
                    sp_mark = "SPECI" if o.get("speci") else ""
                    print(f"    {o['time']:<20} {_fmt_temp(o['temp_c']):>7} {sp_mark:>6}  {o.get('raw', '')[:70]}")


# ═══════════════════════════════════════════════════════════════════
#  CLI 入口
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="多渠道气象观测数据获取测试",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--city", nargs="+", metavar="ICAO",
        help="只测指定城市，如 --city LEMD EGLC",
    )
    parser.add_argument(
        "--channel", nargs="+", metavar="CH",
        choices=list(CHANNEL_FUNCS.keys()),
        help="只测指定渠道: iem, weatherapi, avwx, noaa_adds",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="打印原始 JSON 响应")
    parser.add_argument("--parallel", "-p", type=int, default=4, help="并行城市数 (默认4)")
    args = parser.parse_args()

    channels = args.channel or list(CHANNEL_FUNCS.keys())
    if args.city:
        target_icaos = [c.upper() for c in args.city]
        unknown = [c for c in target_icaos if c not in CITY_MAP]
        if unknown:
            print(f"未知 ICAO: {', '.join(unknown)}")
            print(f"可选: {', '.join(c['icao'] for c in CITIES)}")
            sys.exit(1)
        cities = [CITY_MAP[c] for c in target_icaos]
    else:
        cities = CITIES

    ch_names = [CHANNEL_FUNCS[k][0] for k in channels]
    print(f"\n多渠道气象数据获取测试")
    print(f"城市: {len(cities)} 个  渠道: {', '.join(ch_names)}")
    print(f"WEATHERAPI_KEY: {'✓ 已配置' if WEATHERAPI_KEY else '✗ 未配置'}")
    print(f"AVWX_TOKEN:     {'✓ 已配置' if AVWX_TOKEN else '✗ 未配置'}")
    print(f"UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n")

    all_results = {}
    t_start = time.monotonic()

    def _process_city(city):
        icao = city["icao"]
        result = test_one_city(icao, channels, verbose=args.verbose)
        return icao, result

    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {pool.submit(_process_city, c): c for c in cities}
        done_count = 0
        for future in as_completed(futures):
            city = futures[future]
            done_count += 1
            try:
                icao, result = future.result()
                all_results[icao] = result

                # 进度行
                parts = []
                for ch_key in channels:
                    res = result.get(ch_key, {})
                    label = CHANNEL_FUNCS[ch_key][0]
                    if not res.get("ok"):
                        parts.append(f"{label}:✗")
                    else:
                        temp, _ = _extract_latest_temp(ch_key, res)
                        parts.append(f"{label}:{_fmt_temp(temp)}" if temp is not None else f"{label}:—")
                print(f"  [{done_count:>2}/{len(cities)}] {city['name_cn']:<10} ({city['icao']})  {' │ '.join(parts)}")

            except Exception as e:
                print(f"  [{done_count:>2}/{len(cities)}] {city['name_cn']:<10} ({city['icao']})  全部失败: {e}")

    elapsed = time.monotonic() - t_start
    print(f"\n总耗时: {elapsed:.1f}s")

    # 对比表格
    print_summary_table(all_results, channels)

    # 少量城市时打印详细信息
    if len(cities) <= 5:
        for city in cities:
            if city["icao"] in all_results:
                print_city_detail(city["icao"], all_results[city["icao"]], channels)
        print()


if __name__ == "__main__":
    main()
