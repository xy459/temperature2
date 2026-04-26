#!/usr/bin/env python3
"""核对 Polymarket 温度盘口 Rules 与 cities.py 的 icao（支持 WU + NOAA 双源）。"""
import re
import sys
import time

import requests

from cities import CITIES

SKIP_SLUGS = frozenset(
    {"guangzhou", "lagos", "manila", "karachi", "cape-town", "jeddah"}
)
DATE_PATH = "april-27-2026"
SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "PolyTempBot/1.0 (icao verification)",
        "Accept": "text/html,application/xhtml+xml",
    }
)
AVAIL = re.compile(
    r"available here:\s*<?(https?://[^\s<\"'\\]+)",
    re.IGNORECASE,
)
WU_ICAO = re.compile(
    r"wunderground\.com/history/daily/[^/]+/[^/]+/([A-Z0-9]{4})",
    re.IGNORECASE,
)
NOAA_SITE = re.compile(r"timeseries\?[^\"']*site=([A-Z0-9]{4})", re.IGNORECASE)


def clean_url(raw: str) -> str:
    s = raw.strip()
    s = re.split(r"[\s<\"'\\]+", s, maxsplit=1)[0]
    return s.rstrip(").,")


# 本事件主规则里的一条「来源 + available here」链接（排除页内推荐/多市场杂链）
MAIN_RULE = re.compile(
    r"The resolution source for this market will be information from (?:Wunderground|NOAA),\s*"
    r".*?available here:\s*<?(https?://[^\s<\"'\\]+)",
    re.DOTALL | re.IGNORECASE,
)


def collect_codes(html: str) -> list[tuple[str, str, str]]:
    """主规则中解析；若 WU+NOAA 同页出现多段重复，去重后合并。"""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str, str]] = []
    for m in MAIN_RULE.finditer(html):
        url = clean_url(m.group(1))
        if "wunderground.com" in url.lower():
            mc = WU_ICAO.search(url)
            if mc:
                icao = mc.group(1).upper()
                if ("wu", icao) not in seen:
                    seen.add(("wu", icao))
                    out.append(("wu", icao, url))
        if "weather.gov" in url.lower() and "site=" in url.lower():
            ms = NOAA_SITE.search(url)
            if ms:
                icao = ms.group(1).upper()
                if ("noaa", icao) not in seen:
                    seen.add(("noaa", icao))
                    out.append(("noaa", icao, url))
    return out


def main() -> int:
    ok = 0
    bad: list[tuple[str, str, list]] = []  # slug, expect, list of tuples

    to_check = [
        c for c in CITIES
        if c["slug"] not in SKIP_SLUGS and not c.get("fahrenheit")
    ]
    print(
        f"检查 {len(to_check)} 城，"
        f"highest-temperature-in-{{slug}}-on-{DATE_PATH}\n"
    )

    for c in to_check:
        slug, expect = c["slug"], c["icao"]
        url = f"https://polymarket.com/event/highest-temperature-in-{slug}-on-{DATE_PATH}"
        try:
            r = SESSION.get(url, timeout=25)
        except Exception as e:
            bad.append((slug, expect, [("error", str(e), url)]))
            time.sleep(0.4)
            continue
        if r.status_code != 200:
            bad.append((slug, expect, [("http", str(r.status_code), url)]))
            time.sleep(0.4)
            continue
        codes = collect_codes(r.text)
        time.sleep(0.45)
        found = {x[1] for x in codes}
        if expect in found:
            ok += 1
        else:
            bad.append((slug, expect, codes or [("none", "无 available here 源", url)]))

    for slug, expect, codes in bad:
        print(f"【未对齐】{slug}  cities icao={expect}")
        for t, icao, u in codes:
            print(f"  Polymarket: [{t}] {icao}  {u[:100]}")
        print()
    print(
        f"与 Polymarket(任一分辨率源) 四码一致: {ok} / {len(to_check)}"
    )
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
