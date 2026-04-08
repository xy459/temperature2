#!/usr/bin/env python3
"""
METAR 多渠道时延测试
测试 4 个 METAR 数据源获取新报文的延迟

渠道:
  1. NOAA Aviation Weather (aviationweather.gov)
  2. Iowa State IEM (mesonet.agron.iastate.edu)
  3. OGIMET (ogimet.com)
  4. AEMET OpenData (opendata.aemet.es) — 需要 API Key

原理:
  METAR 在 :00/:30 UTC 发布 → 脚本从 :00/:30 开始密集轮询各渠道
  → 检测到新 METAR 时记录 "首次发现时间 - METAR观测时间" = 渠道延迟

使用:
  python3 metar_latency_test.py                    # 默认 LEMD
  python3 metar_latency_test.py --station KJFK      # 其他站点
  python3 metar_latency_test.py --aemet-key YOUR_KEY # 启用 AEMET
  python3 metar_latency_test.py --report            # 查看统计
"""

import json
import time
import datetime
import urllib.request
import signal
import sys
import os
import csv
import re
import argparse

UTC = datetime.timezone.utc

def utcnow():
    return datetime.datetime.now(UTC).replace(tzinfo=None)

def ts(dt):
    return dt.strftime('%Y-%m-%d %H:%M:%S')

DATA_DIR = "data"
LOG_DIR = "logs"

running = True
def handle_signal(sig, frame):
    global running
    running = False
    print("\n[信号] 正在优雅关闭...")
signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


class METARChannel:
    """Base class for a METAR data channel."""
    name = "base"

    def __init__(self, station):
        self.station = station
        self.last_metar_time = None  # "DDHHMMZ" string from METAR
        self.last_raw = None

    def fetch(self):
        """Fetch latest METAR. Returns (obs_time_str, raw_metar, fetch_ms) or None."""
        raise NotImplementedError

    def _http_get(self, url, timeout=10):
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (METAR-latency-test)',
                'Accept': '*/*',
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode('utf-8', errors='replace')
            ms = (time.monotonic() - t0) * 1000
            return body, ms
        except Exception as e:
            ms = (time.monotonic() - t0) * 1000
            return None, ms

    def _parse_metar_time(self, raw):
        """Extract observation time (DDHHMMZ) from raw METAR string."""
        m = re.search(r'\b(\d{6}Z)\b', raw)
        return m.group(1) if m else None

    def _metar_time_to_utc(self, time_str):
        """Convert DDHHMMZ to a datetime (using current month/year)."""
        if not time_str or len(time_str) != 7:
            return None
        now = utcnow()
        day = int(time_str[:2])
        hour = int(time_str[2:4])
        minute = int(time_str[4:6])
        try:
            dt = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
            if dt > now + datetime.timedelta(hours=1):
                if now.month == 1:
                    dt = dt.replace(year=now.year - 1, month=12)
                else:
                    dt = dt.replace(month=now.month - 1)
            return dt
        except ValueError:
            return None


class NOAAChannel(METARChannel):
    """NOAA Aviation Weather Center (aviationweather.gov)"""
    name = "NOAA"

    def fetch(self):
        url = f"https://aviationweather.gov/api/data/metar?ids={self.station}&format=raw&taf=false"
        body, ms = self._http_get(url)
        if body is None:
            return None

        lines = [l.strip() for l in body.strip().split('\n') if l.strip() and self.station in l]
        if not lines:
            return None

        raw = lines[0]
        obs_time = self._parse_metar_time(raw)
        return obs_time, raw, ms


class IEMChannel(METARChannel):
    """Iowa State IEM (mesonet.agron.iastate.edu)"""
    name = "IEM"

    def fetch(self):
        now = utcnow()
        url = (f"https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
               f"station={self.station}&data=metar&tz=Etc/UTC&format=onlycomma"
               f"&latlon=no&missing=M&direct=no&report_type=3&report_type=4"
               f"&year1={now.year}&month1={now.month}&day1={now.day}"
               f"&year2={now.year}&month2={now.month}&day2={now.day}")
        body, ms = self._http_get(url, timeout=15)
        if body is None:
            return None

        lines = body.strip().split('\n')
        if len(lines) < 2:
            return None

        last_line = lines[-1]
        parts = last_line.split(',')
        metar_col = -1
        header = lines[0].split(',')
        for i, h in enumerate(header):
            if h.strip().lower() == 'metar':
                metar_col = i
                break
        if metar_col < 0 or metar_col >= len(parts):
            return None

        raw = parts[metar_col].strip()
        obs_time = self._parse_metar_time(raw)
        return obs_time, raw, ms


class OGIMETChannel(METARChannel):
    """OGIMET (ogimet.com)"""
    name = "OGIMET"

    def fetch(self):
        url = (f"https://www.ogimet.com/display_metars2.php?"
               f"lugar={self.station}&tipo=ALL&ord=REV&nil=SI&fmt=txt"
               f"&send=send")
        body, ms = self._http_get(url, timeout=15)
        if body is None:
            return None

        for line in body.split('\n'):
            line = line.strip()
            if self.station in line and 'METAR' in line and 'Z ' in line:
                raw_start = line.find('METAR')
                if raw_start >= 0:
                    raw = line[raw_start:].rstrip('=').strip()
                    obs_time = self._parse_metar_time(raw)
                    return obs_time, raw, ms
            elif self.station in line and re.search(r'\d{6}Z', line):
                obs_time = self._parse_metar_time(line)
                if obs_time:
                    return obs_time, line.strip(), ms

        return None


class AEMETChannel(METARChannel):
    """AEMET OpenData (opendata.aemet.es) — Spain only"""
    name = "AEMET"

    STATION_MAP = {
        'LEMD': '3129',
        'LEBL': '0076',
        'LEAL': '8025',
        'LEMG': '6155A',
        'LEZL': '5783',
    }

    def __init__(self, station, api_key="eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4eTk4MzEzOEBnbWFpbC5jb20iLCJqdGkiOiI2Y2NjYTc5ZC0xNWFlLTQ3ZTUtOWMyMi01NTVmYjhjMThjMDAiLCJpc3MiOiJBRU1FVCIsImlhdCI6MTc3NTY0ODQ0OCwidXNlcklkIjoiNmNjY2E3OWQtMTVhZS00N2U1LTljMjItNTU1ZmI4YzE4YzAwIiwicm9sZSI6IiJ9.LEVILkzmaAZpbclW5S2C2mjS7PD12CsQWthseZPTdqk"):
        super().__init__(station)
        self.api_key = api_key
        self.aemet_id = self.STATION_MAP.get(station)

    def fetch(self):
        if not self.api_key or not self.aemet_id:
            return None

        url = (f"https://opendata.aemet.es/opendata/api/observacion/"
               f"convencional/datos/estacion/{self.aemet_id}/"
               f"?api_key={self.api_key}")
        body, ms = self._http_get(url, timeout=15)
        if body is None:
            return None

        try:
            meta = json.loads(body)
            data_url = meta.get('datos')
            if not data_url:
                return None
            body2, ms2 = self._http_get(data_url, timeout=15)
            if body2 is None:
                return None
            ms += ms2
            records = json.loads(body2)
            if not records:
                return None
            latest = records[-1]
            fint = latest.get('fint', '')
            ta = latest.get('ta')
            # Handle "+0000" → "+00:00" for fromisoformat compatibility
            fint_clean = re.sub(r'\+(\d{2})(\d{2})$', r'+\1:\2', fint)
            dt = datetime.datetime.fromisoformat(fint_clean)
            dt_naive = dt.replace(tzinfo=None)
            obs_str = dt_naive.strftime('%d%H%MZ')
            raw = f"AEMET {self.aemet_id} {obs_str} ta={ta}°C"
            return obs_str, raw, ms
        except Exception as e:
            print(f"  [AEMET debug] {e}")
            return None


class METARLatencyTester:
    RESULTS_CSV_HEADERS = [
        'metar_time_utc', 'metar_obs_str', 'channel', 'first_seen_utc',
        'latency_seconds', 'http_ms', 'raw_metar'
    ]

    def __init__(self, station, aemet_key=None, poll_interval=15):
        self.station = station
        self.poll_interval = poll_interval

        self.channels = [
            NOAAChannel(station),
            IEMChannel(station),
            OGIMETChannel(station),
            AEMETChannel(station, aemet_key),
        ]

        self.results_csv = os.path.join(DATA_DIR, "metar_latency.csv")
        self.logfile = None

        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(LOG_DIR, exist_ok=True)

        if not os.path.exists(self.results_csv):
            with open(self.results_csv, 'w', newline='') as f:
                csv.writer(f).writerow(self.RESULTS_CSV_HEADERS)

        self.logfile = open(os.path.join(LOG_DIR, 'metar_latency.log'), 'a')

        self.pending_metar = {}  # obs_str -> {channel_name: first_seen or None}
        self.known_obs = {}  # channel_name -> last known obs_str

    def log(self, msg):
        line = f"[{ts(utcnow())}] {msg}"
        print(line)
        if self.logfile:
            self.logfile.write(line + '\n')
            self.logfile.flush()

    def next_metar_time(self):
        now = utcnow()
        if now.minute < 30:
            return now.replace(minute=30, second=0, microsecond=0)
        return (now.replace(minute=0, second=0, microsecond=0)
                + datetime.timedelta(hours=1))

    def minutes_to_next_metar(self):
        return (self.next_metar_time() - utcnow()).total_seconds() / 60

    def poll_all_channels(self):
        now = utcnow()
        for ch in self.channels:
            result = ch.fetch()
            if result is None:
                continue

            obs_str, raw, http_ms = result

            if obs_str is None:
                continue

            if ch.name in self.known_obs and self.known_obs[ch.name] == obs_str:
                continue

            old_obs = self.known_obs.get(ch.name)
            self.known_obs[ch.name] = obs_str

            metar_dt = ch._metar_time_to_utc(obs_str)
            if metar_dt is None:
                continue

            latency = (now - metar_dt).total_seconds()

            if latency < 0 or latency > 7200:
                continue

            self.log(f"  ⚡ {ch.name:>6} | 新METAR {obs_str} | "
                     f"延迟 {latency:.0f}s ({latency/60:.1f}min) | "
                     f"HTTP {http_ms:.0f}ms")
            self.log(f"         └─ {raw[:100]}")

            with open(self.results_csv, 'a', newline='') as f:
                csv.writer(f).writerow([
                    ts(metar_dt), obs_str, ch.name, ts(now),
                    f"{latency:.1f}", f"{http_ms:.0f}",
                    raw[:200]
                ])

    def print_report(self):
        if not os.path.exists(self.results_csv):
            print("暂无数据")
            return

        with open(self.results_csv) as f:
            records = list(csv.DictReader(f))

        if not records:
            print("暂无数据")
            return

        by_channel = {}
        for r in records:
            ch = r['channel']
            lat = float(r['latency_seconds'])
            by_channel.setdefault(ch, []).append(lat)

        print("=" * 70)
        print(f"METAR 渠道时延统计 | 站点: {self.station} | 共 {len(records)} 条记录")
        print("=" * 70)
        print(f"\n{'渠道':>8} | {'记录数':>5} | {'平均延迟':>8} | {'最小':>6} | {'最大':>6} | {'中位数':>6}")
        print("-" * 70)

        for ch in ['NOAA', 'IEM', 'OGIMET', 'AEMET']:
            if ch not in by_channel:
                print(f"  {ch:>6} | {'N/A':>5} |")
                continue
            lats = sorted(by_channel[ch])
            n = len(lats)
            avg = sum(lats) / n
            mn = min(lats)
            mx = max(lats)
            med = lats[n // 2]
            print(f"  {ch:>6} | {n:>5} | {avg:>6.0f}s | {mn:>4.0f}s | {mx:>4.0f}s | {med:>4.0f}s")

        print()

        by_metar = {}
        for r in records:
            key = r['metar_obs_str']
            by_metar.setdefault(key, {})[r['channel']] = float(r['latency_seconds'])

        print(f"最近 10 次 METAR 各渠道到达时间对比:")
        print(f"{'METAR时间':>10} |", end="")
        for ch in ['NOAA', 'IEM', 'OGIMET', 'AEMET']:
            print(f" {ch:>8} |", end="")
        print(f" {'最快':>6}")
        print("-" * 70)

        recent_keys = list(by_metar.keys())[-10:]
        for key in recent_keys:
            ch_lats = by_metar[key]
            print(f"  {key:>8} |", end="")
            vals = []
            for ch in ['NOAA', 'IEM', 'OGIMET', 'AEMET']:
                if ch in ch_lats:
                    v = ch_lats[ch]
                    vals.append((v, ch))
                    print(f" {v:>6.0f}s |", end="")
                else:
                    print(f" {'---':>6}s |", end="")
            if vals:
                fastest = min(vals, key=lambda x: x[0])
                print(f" {fastest[1]}")
            else:
                print()

        print()

    def run(self):
        self.log("=" * 60)
        self.log(f"METAR 多渠道时延测试启动")
        self.log(f"站点: {self.station}")
        self.log(f"渠道: {', '.join(ch.name for ch in self.channels)}")
        self.log(f"轮询间隔: {self.poll_interval}s（METAR前后加密至 5s）")
        self.log("=" * 60)

        for ch in self.channels:
            result = ch.fetch()
            if result:
                obs_str, raw, ms = result
                self.known_obs[ch.name] = obs_str
                self.log(f"  初始 {ch.name:>6} | 当前METAR: {obs_str} | HTTP {ms:.0f}ms")
            else:
                self.log(f"  初始 {ch.name:>6} | 获取失败")

        poll_count = 0
        last_heartbeat = utcnow()

        last_phase = None

        while running:
            poll_count += 1
            now = utcnow()

            # :00/:30 后过了多少分钟（0~29）
            mins_since_metar = now.minute % 30 + now.second / 60.0

            # 轮询策略:
            #   0~3 min:  极速 0.2s（抢先捕捉最快渠道）
            #   3~5 min:  快速 1s
            #   5~12 min: 普通 5s（等较慢的渠道）
            #   12~30 min: 空闲 15s
            if mins_since_metar <= 3:
                interval = 0.2
                phase = "burst"
            elif mins_since_metar <= 5:
                interval = 1
                phase = "fast"
            elif mins_since_metar <= 12:
                interval = 5
                phase = "normal"
            else:
                interval = self.poll_interval
                phase = "idle"

            if phase != last_phase:
                if phase == "burst":
                    metar_label = now.replace(
                        minute=(now.minute // 30) * 30, second=0, microsecond=0
                    )
                    self.log(f"━━━ METAR {metar_label.strftime('%H:%M')} 刚发布，"
                             f"极速轮询开始（每0.2s）━━━")
                elif phase == "fast":
                    self.log(f"━━━ 切换到快速轮询（每1s）━━━")
                elif phase == "normal":
                    self.log(f"━━━ 切换到普通轮询（每5s）━━━")
                elif phase == "idle":
                    self.log(f"━━━ 切换到空闲（每{self.poll_interval}s）━━━")
            last_phase = phase

            self.poll_all_channels()

            if (now - last_heartbeat).total_seconds() >= 300:
                last_heartbeat = now
                remaining = 30 - mins_since_metar
                self.log(f"[心跳] #{poll_count} | 距下次METAR {remaining:.0f}min | "
                         f"间隔={interval}s | 模式={phase}")

            if running:
                time.sleep(interval)

        self.log("\n关闭中...")
        self.print_report()
        if self.logfile:
            self.logfile.close()


def main():
    parser = argparse.ArgumentParser(description='METAR 多渠道时延测试')
    parser.add_argument('--station', default='LEMD', help='ICAO 站点代码 (默认 LEMD)')
    parser.add_argument('--aemet-key', default=None, help='AEMET OpenData API Key')
    parser.add_argument('--interval', type=int, default=15, help='基础轮询间隔秒数 (默认 15)')
    parser.add_argument('--report', action='store_true', help='显示统计报告后退出')
    args = parser.parse_args()

    tester = METARLatencyTester(
        station=args.station,
        aemet_key=args.aemet_key,
        poll_interval=args.interval,
    )

    if args.report:
        tester.print_report()
    else:
        tester.run()


if __name__ == '__main__':
    main()
