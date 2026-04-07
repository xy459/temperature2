#!/usr/bin/env python3
"""Poll WU v3 API every 60 seconds to capture high-frequency observation data."""

import json
import time
import datetime
import urllib.request
import csv
import sys
import signal

API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"
STATION = "LEMD"
URL = f"https://api.weather.com/v3/wx/observations/current?apiKey={API_KEY}&language=en-US&units=m&format=json&icaoCode={STATION}"
OUTPUT = "data/v3_highfreq.csv"
INTERVAL = 60

fields = [
    "poll_time_utc", "obs_time_utc", "obs_time_local",
    "temperature", "temperatureMaxSince7Am", "temperatureMax24Hour",
    "temperatureMin24Hour", "temperatureDewPoint",
    "relativeHumidity", "windSpeed", "windDirection",
    "pressureAltimeter", "uvIndex", "cloudCoverPhrase",
    "validTimeUtc"
]

running = True
def handle_signal(sig, frame):
    global running
    running = False
    print(f"\n[{datetime.datetime.utcnow().strftime('%H:%M:%S')}] Shutting down...")

signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)

with open(OUTPUT, "a", newline="") as f:
    writer = csv.writer(f)
    if f.tell() == 0:
        writer.writerow(fields)
        f.flush()

    last_obs_time = None
    poll_count = 0

    while running:
        poll_count += 1
        now = datetime.datetime.utcnow()
        try:
            req = urllib.request.Request(URL)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())

            obs_utc = data.get("validTimeUtc", 0)
            obs_time = datetime.datetime.utcfromtimestamp(obs_utc).strftime("%Y-%m-%d %H:%M:%S")
            obs_local = data.get("validTimeLocal", "")
            is_new = obs_utc != last_obs_time

            row = [
                now.strftime("%Y-%m-%d %H:%M:%S"),
                obs_time,
                obs_local,
                data.get("temperature"),
                data.get("temperatureMaxSince7Am"),
                data.get("temperatureMax24Hour"),
                data.get("temperatureMin24Hour"),
                data.get("temperatureDewPoint"),
                data.get("relativeHumidity"),
                data.get("windSpeed"),
                data.get("windDirection"),
                data.get("pressureAltimeter"),
                data.get("uvIndex"),
                data.get("cloudCoverPhrase"),
                obs_utc,
            ]
            writer.writerow(row)
            f.flush()

            marker = " ⚡ NEW" if is_new else ""
            print(f"[{now.strftime('%H:%M:%S')}] #{poll_count} obs={obs_time} temp={data.get('temperature')}°C dewpt={data.get('temperatureDewPoint')}°C max7am={data.get('temperatureMaxSince7Am')}°C{marker}")

            if is_new:
                last_obs_time = obs_utc

        except Exception as e:
            print(f"[{now.strftime('%H:%M:%S')}] ERROR: {e}", file=sys.stderr)

        if running:
            time.sleep(INTERVAL)

print(f"Total polls: {poll_count}. Data saved to {OUTPUT}")
