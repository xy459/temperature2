#!/usr/bin/env python3
"""
WU KORD 历史数据抓取器
=======================
从Weather Underground抓取KORD每日最高温，生成 wu_data.csv

方法A (推荐): Selenium headless Chrome
方法B: 拦截WU内部API

安装: pip install selenium
      确保 chromedriver 在 PATH 中 (或 pip install webdriver-manager)

运行: python wu_scraper.py
"""
import csv
import re
import time
import sys
from datetime import datetime, timedelta

STATION = "KORD"
START = "2026-02-01"
END   = "2026-04-03"
OUTPUT = "wu_data.csv"


def scrape_selenium():
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
    except ImportError:
        print("需要安装 selenium: pip install selenium")
        print("还需要 chromedriver 可用")
        sys.exit(1)

    try:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
    except ImportError:
        service = None  # 假设 chromedriver 在 PATH

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)")

    if service:
        driver = webdriver.Chrome(service=service, options=opts)
    else:
        driver = webdriver.Chrome(options=opts)

    sd = datetime.strptime(START, "%Y-%m-%d")
    ed = datetime.strptime(END,   "%Y-%m-%d")
    results = []

    d = sd
    while d <= ed:
        url = (f"https://www.wunderground.com/history/daily/us/il/chicago/"
               f"{STATION}/date/{d.year}-{d.month}-{d.day}")
        datestr = d.strftime("%Y-%m-%d")
        print(f"  {datestr} ... ", end="", flush=True)

        try:
            driver.get(url)
            time.sleep(4)  # 等JS渲染

            page = driver.page_source

            # WU 历史页面的 summary 表格
            # 查找 "Max Temperature" 或 "Maximum Temperature" 行
            high = None

            # 策略1: 正则匹配 summary 表格区域
            # WU的历史页面把每日汇总放在一个 table 中
            m = re.search(
                r'(?:Max(?:imum)?\s+Temp(?:erature)?)'
                r'.*?<td[^>]*>\s*<span[^>]*>\s*(\d{1,3})\s*',
                page, re.DOTALL | re.IGNORECASE
            )
            if m:
                high = int(m.group(1))

            # 策略2: JSON嵌入数据
            if high is None:
                m = re.search(r'"temperatureMax"\s*:\s*(\d+)', page)
                if m:
                    high = int(m.group(1))

            # 策略3: 在 history-observation-table 找
            if high is None:
                m = re.search(
                    r'Max</span>.*?<span[^>]*>(\d{1,3})',
                    page, re.DOTALL
                )
                if m:
                    high = int(m.group(1))

            # 策略4: 用 class="wu-value wu-value-to"
            if high is None:
                # WU常用 class 包含温度值
                els = driver.find_elements(By.CSS_SELECTOR, ".wu-value-to")
                for el in els:
                    txt = el.text.strip()
                    if txt.isdigit() and 0 < int(txt) < 120:
                        high = int(txt)
                        break

            if high is not None:
                results.append({"date": datestr, "wu_high": high})
                print(f"{high}°F ✓")
            else:
                print("NOT FOUND ✗")
                # 保存debug页面
                with open(f"debug_{datestr}.html", "w") as f:
                    f.write(page)
                print(f"    (已保存 debug_{datestr}.html 供排查)")

        except Exception as e:
            print(f"ERROR: {e}")

        d += timedelta(days=1)
        time.sleep(1)  # 限速

    driver.quit()

    # 保存
    if results:
        with open(OUTPUT, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["date", "wu_high"])
            w.writeheader()
            w.writerows(results)
        print(f"\n✓ 保存 {len(results)} 天到 {OUTPUT}")
    else:
        print("\n✗ 未获取到任何数据")

    return results


def print_manual_guide():
    """手动收集指南"""
    sd = datetime.strptime(START, "%Y-%m-%d")
    ed = datetime.strptime(END,   "%Y-%m-%d")

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  手动收集 WU 历史数据指南                                    ║
╠══════════════════════════════════════════════════════════════╣

1. 访问 WU 历史页面:
   https://www.wunderground.com/history/daily/us/il/chicago/KORD/date/2026-3-1

2. 在页面 Summary 表格中找到 "Max Temperature" 的 Actual 值

3. 创建 wu_data.csv 文件，格式:
   date,wu_high
   2026-02-01,35
   2026-02-02,42
   ...

4. URL 模板 (改日期即可):
""")
    d = sd
    while d <= ed:
        url = (f"   https://www.wunderground.com/history/daily/us/il/chicago/"
               f"KORD/date/{d.year}-{d.month}-{d.day}")
        print(url)
        d += timedelta(days=1)

    print(f"""
╚══════════════════════════════════════════════════════════════╝

提示: 也可以用 Claude Chrome Extension 自动化:
  "帮我访问以下WU页面，提取每天的Max Temperature actual值，
   整理成CSV: date,wu_high"
""")


if __name__ == "__main__":
    if "--manual" in sys.argv:
        print_manual_guide()
    else:
        scrape_selenium()
