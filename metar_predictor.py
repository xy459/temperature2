#!/usr/bin/env python3
"""
METAR Temperature Predictor — 长期运行版
用 v3 API 高频数据提前推算 METAR 温度，自动验证并持久化结果

所有数据写入 data/ 目录:
  - predictions.csv    每次预测记录
  - verifications.csv  预测 vs 实际 METAR 验证
  - v3_observations.csv  所有独立 v3 观测（去重）

使用方法:
  PYTHONUNBUFFERED=1 nohup python3 metar_predictor.py &

  查看实时日志:
    tail -f logs/predictor.log

  查看当前准确率:
    python3 metar_predictor.py --report
"""

import json
import time
import datetime
import urllib.request
import signal
import sys
import os
import csv

UTC = datetime.timezone.utc

API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"
STATION = "LEMD"
V3_URL = f"https://api.weather.com/v3/wx/observations/current?apiKey={API_KEY}&language=en-US&units=m&format=json&icaoCode={STATION}"
V1_URL = f"https://api.weather.com/v1/location/{STATION}:9:ES/observations/historical.json?apiKey={API_KEY}&units=m"

DATA_DIR = "data"
LOG_DIR = "logs"
PREDICTIONS_CSV = os.path.join(DATA_DIR, "predictions.csv")
VERIFICATIONS_CSV = os.path.join(DATA_DIR, "verifications.csv")
V3_OBS_CSV = os.path.join(DATA_DIR, "v3_observations.csv")

POLL_INTERVAL = 30  # seconds

running = True
def handle_signal(sig, frame):
    global running
    running = False
    print("\n[信号] 正在优雅关闭...")
signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


def ensure_csv(path, headers):
    if not os.path.exists(path):
        with open(path, 'w', newline='') as f:
            csv.writer(f).writerow(headers)


def append_csv(path, row):
    with open(path, 'a', newline='') as f:
        csv.writer(f).writerow(row)


def load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def utcnow():
    return datetime.datetime.now(UTC).replace(tzinfo=None)


def from_epoch(epoch):
    return datetime.datetime.fromtimestamp(epoch, UTC).replace(tzinfo=None)


def ts(dt):
    return dt.strftime('%Y-%m-%d %H:%M:%S')


class METARPredictor:
    PRED_HEADERS = [
        'predict_time', 'target_metar_time', 'v3_obs_time', 'v3_temp',
        'trend_per_min', 'minutes_until', 'predicted_temp', 'confidence',
        'v3_max7am', 'verified', 'actual_temp', 'correct', 'diff'
    ]
    VERIFY_HEADERS = [
        'metar_time', 'metar_temp', 'predicted_temp', 'correct', 'diff',
        'confidence', 'predict_time', 'v3_obs_time', 'v3_temp', 'lead_minutes'
    ]
    V3_OBS_HEADERS = [
        'poll_time', 'obs_time', 'temperature', 'temperatureMaxSince7Am',
        'temperatureDewPoint', 'windSpeed', 'windDirection', 'validTimeUtc'
    ]

    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(LOG_DIR, exist_ok=True)

        ensure_csv(PREDICTIONS_CSV, self.PRED_HEADERS)
        ensure_csv(VERIFICATIONS_CSV, self.VERIFY_HEADERS)
        ensure_csv(V3_OBS_CSV, self.V3_OBS_HEADERS)

        self.logfile = open(os.path.join(LOG_DIR, 'predictor.log'), 'a')

        self.v3_recent = []
        self.last_v3_epoch = 0
        self.pending_predictions = {}  # metar_time_str -> prediction_dict
        self.last_v1_check = None
        self.known_metar_times = set()
        self.daily_max_v3 = {}  # date_str -> max

        self._load_existing()

    def _load_existing(self):
        """Load already-seen METAR times to avoid re-verifying."""
        existing = load_csv(VERIFICATIONS_CSV)
        for row in existing:
            self.known_metar_times.add(row['metar_time'])
        if existing:
            self.log(f"已加载 {len(existing)} 条历史验证记录")

        preds = load_csv(PREDICTIONS_CSV)
        unverified = [p for p in preds if p.get('verified') != 'True']
        for p in unverified:
            key = p['target_metar_time']
            self.pending_predictions[key] = {
                'predict_time': p['predict_time'],
                'target_metar_time': p['target_metar_time'],
                'v3_obs_time': p['v3_obs_time'],
                'v3_temp': int(p['v3_temp']),
                'predicted_temp': int(p['predicted_temp']),
                'confidence': p['confidence'],
                'trend_per_min': float(p['trend_per_min']),
                'v3_max7am': p.get('v3_max7am', ''),
            }
        if unverified:
            self.log(f"已加载 {len(unverified)} 条待验证预测")

    def log(self, msg):
        line = f"[{ts(utcnow())}] {msg}"
        print(line)
        self.logfile.write(line + '\n')
        self.logfile.flush()

    def fetch_v3(self):
        try:
            req = urllib.request.Request(V3_URL, headers={
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'application/json',
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except Exception as e:
            self.log(f"⚠ v3 请求失败: {e}")
            return None

    def fetch_v1_today(self):
        try:
            today = utcnow().strftime('%Y%m%d')
            url = f"{V1_URL}&startDate={today}&endDate={today}"
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'application/json',
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            return data.get('observations', [])
        except Exception as e:
            self.log(f"⚠ v1 请求失败: {e}")
            return []

    def next_metar_time(self):
        now = utcnow()
        if now.minute < 30:
            return now.replace(minute=30, second=0, microsecond=0)
        return (now.replace(minute=0, second=0, microsecond=0)
                + datetime.timedelta(hours=1))

    def prev_metar_time(self):
        now = utcnow()
        if now.minute >= 30:
            return now.replace(minute=30, second=0, microsecond=0)
        return now.replace(minute=0, second=0, microsecond=0)

    def get_trend(self):
        if len(self.v3_recent) < 2:
            return 0.0
        a, b = self.v3_recent[-2], self.v3_recent[-1]
        dt = b['epoch'] - a['epoch']
        if dt <= 0:
            return 0.0
        return (b['temp'] - a['temp']) / (dt / 60.0)

    def process_v3(self, data):
        epoch = data.get('validTimeUtc', 0)
        if epoch <= self.last_v3_epoch:
            return False

        self.last_v3_epoch = epoch
        obs_time = from_epoch(epoch)
        temp = data.get('temperature')
        max7am = data.get('temperatureMaxSince7Am')
        dewpt = data.get('temperatureDewPoint')
        wind_spd = data.get('windSpeed')
        wind_dir = data.get('windDirection')

        self.v3_recent.append({
            'epoch': epoch,
            'obs_time': obs_time,
            'temp': temp,
            'max7am': max7am,
            'dewpt': dewpt,
        })
        if len(self.v3_recent) > 20:
            self.v3_recent = self.v3_recent[-20:]

        append_csv(V3_OBS_CSV, [
            ts(utcnow()), ts(obs_time), temp, max7am, dewpt,
            wind_spd, wind_dir, epoch
        ])

        today_str = utcnow().strftime('%Y-%m-%d')
        if max7am is not None:
            old_max = self.daily_max_v3.get(today_str)
            if old_max is None or max7am > old_max:
                self.daily_max_v3[today_str] = max7am
                if old_max is not None and max7am > old_max:
                    self.log(f"🔺 日最高温更新! {old_max}°C → {max7am}°C (v3 max7am)")

        self.log(f"⚡ v3 新观测 | {ts(obs_time)} | temp={temp}°C | "
                 f"max7am={max7am}°C | dewpt={dewpt}°C")
        return True

    def make_prediction(self):
        if not self.v3_recent:
            return

        latest = self.v3_recent[-1]
        target = self.next_metar_time()
        target_str = ts(target)

        # Always update prediction for this target (latest v3 wins)
        now = utcnow()
        minutes_until = (target - now).total_seconds() / 60
        trend = self.get_trend()

        raw = latest['temp'] + trend * minutes_until
        predicted = round(raw)

        confidence = "HIGH"
        reasons = []
        if abs(trend) > 0.05:
            confidence = "MEDIUM"
            reasons.append(f"趋势{trend:+.3f}°C/min")
        frac = abs(raw - round(raw))
        if frac > 0.35:
            if confidence == "HIGH":
                confidence = "MEDIUM"
            else:
                confidence = "LOW"
            reasons.append(f"接近四舍五入边界({raw:.1f})")
        if minutes_until > 20:
            confidence = "LOW"
            reasons.append(f"距METAR还有{minutes_until:.0f}min")

        pred = {
            'predict_time': ts(now),
            'target_metar_time': target_str,
            'v3_obs_time': ts(latest['obs_time']),
            'v3_temp': latest['temp'],
            'predicted_temp': predicted,
            'confidence': confidence,
            'trend_per_min': trend,
            'v3_max7am': latest.get('max7am', ''),
        }

        is_update = target_str in self.pending_predictions
        old_pred = self.pending_predictions.get(target_str, {}).get('predicted_temp')
        self.pending_predictions[target_str] = pred

        append_csv(PREDICTIONS_CSV, [
            pred['predict_time'], pred['target_metar_time'],
            pred['v3_obs_time'], pred['v3_temp'], f"{trend:.4f}",
            f"{minutes_until:.1f}", pred['predicted_temp'], pred['confidence'],
            pred['v3_max7am'], '', '', '', ''
        ])

        if is_update and old_pred != predicted:
            self.log(f"🔮 预测更新 METAR {target.strftime('%H:%M')} = {predicted}°C "
                     f"(was {old_pred}°C) | v3={latest['temp']}°C "
                     f"趋势={trend:+.4f} 还有{minutes_until:.0f}min [{confidence}]")
        elif not is_update:
            reason_str = " | ".join(reasons) if reasons else "稳定"
            self.log(f"🔮 预测 METAR {target.strftime('%H:%M')} = {predicted}°C | "
                     f"v3={latest['temp']}°C 趋势={trend:+.4f} "
                     f"还有{minutes_until:.0f}min [{confidence}] {reason_str}")

    def check_v1_and_verify(self):
        now = utcnow()
        if self.last_v1_check and (now - self.last_v1_check).total_seconds() < 60:
            return
        self.last_v1_check = now

        observations = self.fetch_v1_today()
        if not observations:
            return

        for obs in observations:
            metar_epoch = obs.get('valid_time_gmt', 0)
            metar_time = from_epoch(metar_epoch)
            metar_time_str = ts(metar_time)
            metar_temp = obs.get('temp')

            if metar_time_str in self.known_metar_times:
                continue

            self.known_metar_times.add(metar_time_str)
            self.log(f"📡 METAR 到达 | {metar_time.strftime('%H:%M')} | temp={metar_temp}°C")

            pred = self.pending_predictions.pop(metar_time_str, None)
            if pred is None:
                for key in list(self.pending_predictions.keys()):
                    try:
                        pred_dt = datetime.datetime.strptime(key, '%Y-%m-%d %H:%M:%S')
                        if abs((metar_time - pred_dt).total_seconds()) < 120:
                            pred = self.pending_predictions.pop(key)
                            break
                    except ValueError:
                        continue

            if pred is None:
                self.log(f"  ⏭ 无对应预测（可能是启动前的 METAR）")
                continue

            predicted = pred['predicted_temp']
            correct = predicted == metar_temp
            diff = predicted - metar_temp

            predict_dt = datetime.datetime.strptime(pred['predict_time'], '%Y-%m-%d %H:%M:%S')
            lead = (metar_time - predict_dt).total_seconds() / 60

            append_csv(VERIFICATIONS_CSV, [
                metar_time_str, metar_temp, predicted, correct, diff,
                pred['confidence'], pred['predict_time'], pred['v3_obs_time'],
                pred['v3_temp'], f"{lead:.1f}"
            ])

            icon = "✅" if correct else "❌"
            self.log(f"  {icon} 预测={predicted}°C vs 实际={metar_temp}°C | "
                     f"差={diff:+d} | 置信={pred['confidence']} | "
                     f"提前{lead:.0f}min")

    def print_report(self):
        records = load_csv(VERIFICATIONS_CSV)
        if not records:
            self.log("暂无验证记录")
            return

        total = len(records)
        correct = sum(1 for r in records if r['correct'] == 'True')
        within1 = sum(1 for r in records if abs(int(r['diff'])) <= 1)

        self.log("=" * 60)
        self.log(f"📊 累计准确率: {correct}/{total} = {correct/total*100:.1f}%")
        self.log(f"   ±1°C 内:   {within1}/{total} = {within1/total*100:.1f}%")

        by_conf = {}
        for r in records:
            c = r['confidence']
            by_conf.setdefault(c, {'total': 0, 'correct': 0})
            by_conf[c]['total'] += 1
            if r['correct'] == 'True':
                by_conf[c]['correct'] += 1

        self.log(f"   按置信度:")
        for c in ['HIGH', 'MEDIUM', 'LOW']:
            if c in by_conf:
                d = by_conf[c]
                self.log(f"     {c:>6}: {d['correct']}/{d['total']} "
                         f"({d['correct']/d['total']*100:.0f}%)")

        by_date = {}
        for r in records:
            date = r['metar_time'][:10]
            by_date.setdefault(date, {'total': 0, 'correct': 0})
            by_date[date]['total'] += 1
            if r['correct'] == 'True':
                by_date[date]['correct'] += 1

        self.log(f"   按日期:")
        for date in sorted(by_date.keys()):
            d = by_date[date]
            self.log(f"     {date}: {d['correct']}/{d['total']} "
                     f"({d['correct']/d['total']*100:.0f}%)")

        self.log("=" * 60)

    def run(self):
        self.log("=" * 60)
        self.log("METAR 温度实时预测系统启动")
        self.log(f"站点: {STATION} | 轮询: {POLL_INTERVAL}s")
        self.log(f"数据目录: {os.path.abspath(DATA_DIR)}")
        self.log("=" * 60)

        self.print_report()

        poll_count = 0
        last_pred_target = None

        while running:
            poll_count += 1
            v3_data = self.fetch_v3()

            if v3_data:
                is_new = self.process_v3(v3_data)

                target = self.next_metar_time()
                target_str = ts(target)
                if is_new or target_str != last_pred_target:
                    self.make_prediction()
                    last_pred_target = target_str

            # Check v1 more often around :00/:30
            now = utcnow()
            mins_past = now.minute % 30
            if 1 <= mins_past <= 10:
                if self.last_v1_check is None or \
                   (now - self.last_v1_check).total_seconds() >= 30:
                    self.check_v1_and_verify()
            else:
                self.check_v1_and_verify()

            if poll_count % 120 == 0:
                self.print_report()

            if running:
                time.sleep(POLL_INTERVAL)

        self.log("\n关闭中...")
        self.print_report()
        self.logfile.close()


def show_report():
    print("=" * 60)
    print("METAR 预测准确率报告")
    print("=" * 60)

    records = load_csv(VERIFICATIONS_CSV)
    if not records:
        print("暂无验证记录。请先运行预测器收集数据。")
        return

    total = len(records)
    correct = sum(1 for r in records if r['correct'] == 'True')
    within1 = sum(1 for r in records if abs(int(r['diff'])) <= 1)

    print(f"\n总计: {total} 条预测已验证")
    print(f"完全命中: {correct}/{total} = {correct/total*100:.1f}%")
    print(f"±1°C 内:  {within1}/{total} = {within1/total*100:.1f}%")

    from collections import Counter
    diffs = Counter(int(r['diff']) for r in records)
    print(f"\n误差分布:")
    for d in sorted(diffs.keys()):
        pct = diffs[d] / total * 100
        bar = "█" * int(pct / 2)
        print(f"  {d:+d}°C: {diffs[d]:3d} ({pct:5.1f}%) {bar}")

    print(f"\n按置信度:")
    by_conf = {}
    for r in records:
        c = r['confidence']
        by_conf.setdefault(c, {'total': 0, 'correct': 0})
        by_conf[c]['total'] += 1
        if r['correct'] == 'True':
            by_conf[c]['correct'] += 1
    for c in ['HIGH', 'MEDIUM', 'LOW']:
        if c in by_conf:
            d = by_conf[c]
            print(f"  {c:>6}: {d['correct']}/{d['total']} "
                  f"({d['correct']/d['total']*100:.0f}%)")

    print(f"\n按日期:")
    by_date = {}
    for r in records:
        date = r['metar_time'][:10]
        by_date.setdefault(date, {'total': 0, 'correct': 0})
        by_date[date]['total'] += 1
        if r['correct'] == 'True':
            by_date[date]['correct'] += 1
    for date in sorted(by_date.keys()):
        d = by_date[date]
        print(f"  {date}: {d['correct']}/{d['total']} "
              f"({d['correct']/d['total']*100:.0f}%)")

    print(f"\n最近 10 条验证:")
    for r in records[-10:]:
        icon = "✅" if r['correct'] == 'True' else "❌"
        print(f"  {icon} {r['metar_time'][11:16]} | "
              f"预测={r['predicted_temp']}°C 实际={r['metar_temp']}°C "
              f"差={int(r['diff']):+d} [{r['confidence']}] "
              f"提前{float(r['lead_minutes']):.0f}min")


if __name__ == "__main__":
    if '--report' in sys.argv:
        show_report()
    else:
        predictor = METARPredictor()
        predictor.run()
