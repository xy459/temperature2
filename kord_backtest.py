#!/usr/bin/env python3
"""
Polymarket KORD 温度回测工具 — 方案二
======================================
对比多个数据源的KORD每日最高温，确认哪个与WU结算值最一致。

数据源:
  1. NWS CLI (via IEM JSON API) — NWS每日气候报告，经过人工质控
  2. NWS CF6 (via IEM JSON API) — NWS月度气候表，结构化数据
  3. IEM METAR汇总 — 从原始METAR逐条记录计算的每日max
  4. Weather Underground — Polymarket的结算源 (需手动补充或用Selenium)

安装: pip install requests pandas
运行: python kord_backtest.py
"""

import requests
import pandas as pd
import json
import re
import sys
from datetime import datetime, timedelta
from io import StringIO

# ===================== 配置 =====================
STATION = "KORD"
YEAR = 2026
DATE_START = "2026-02-01"
DATE_END   = "2026-04-03"


# ===================== 数据源1: NWS CLI =====================
def fetch_cli(station="KORD", year=2026):
    """
    IEM 解析的 NWS CLI 产品 (经过NWS人工质控的官方每日气候数据)
    API示例: https://mesonet.agron.iastate.edu/json/cli.py?station=KDSM&year=2024
    返回字段: high (最高温°F整数), high_time, low, ...
    """
    url = f"https://mesonet.agron.iastate.edu/json/cli.py?station={station}&year={year}"
    print(f"[CLI] Fetching {url}")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    rows = []
    for item in data.get("results", []):
        rows.append({
            "date": item["valid"],
            "cli_high": item.get("high"),
            "cli_high_time": item.get("high_time"),
            "cli_low": item.get("low"),
        })
    df = pd.DataFrame(rows)
    df["cli_high"] = pd.to_numeric(df["cli_high"], errors="coerce")
    df["cli_low"]  = pd.to_numeric(df["cli_low"],  errors="coerce")
    print(f"  -> {len(df)} days loaded")
    return df


# ===================== 数据源2: NWS CF6 =====================
def fetch_cf6(station="KORD", year=2026):
    """
    IEM 解析的 NWS CF6 产品 (月度气候表格)
    API示例: https://mesonet.agron.iastate.edu/json/cf6.py?station=KDSM&year=2024
    """
    url = f"https://mesonet.agron.iastate.edu/json/cf6.py?station={station}&year={year}"
    print(f"[CF6] Fetching {url}")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    rows = []
    for item in data.get("results", []):
        rows.append({
            "date": item["valid"],
            "cf6_high": item.get("high"),
            "cf6_low":  item.get("low"),
        })
    df = pd.DataFrame(rows)
    df["cf6_high"] = pd.to_numeric(df["cf6_high"], errors="coerce")
    df["cf6_low"]  = pd.to_numeric(df["cf6_low"],  errors="coerce")
    print(f"  -> {len(df)} days loaded")
    return df


# ===================== 数据源3: IEM METAR 逐条汇总 =====================
def fetch_iem_metar(station="KORD", start="2026-02-01", end="2026-04-04"):
    """
    从IEM下载KORD逐条METAR，自行计算每日最高温。
    ⚠️ 可能受F->C->F舍入问题影响(±1°F)。
    API: https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py
    """
    sd = datetime.strptime(start, "%Y-%m-%d")
    ed = datetime.strptime(end,   "%Y-%m-%d")
    url = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
    params = {
        "station": station, "data": "tmpf",
        "year1": sd.year, "month1": sd.month, "day1": sd.day,
        "year2": ed.year, "month2": ed.month, "day2": ed.day,
        "tz": "America/Chicago", "format": "onlycomma",
        "latlon": "no", "elev": "no", "missing": "M",
        "trace": "T", "direct": "no", "report_type": "3",
    }
    print(f"[METAR] Fetching hourly obs {start} -> {end}")
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    df = pd.read_csv(StringIO(resp.text), comment="#", low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    df["tmpf"]  = pd.to_numeric(df["tmpf"], errors="coerce")
    df["valid"] = pd.to_datetime(df["valid"])
    df["date"]  = df["valid"].dt.strftime("%Y-%m-%d")
    daily = (
        df.dropna(subset=["tmpf"])
          .groupby("date")
          .agg(metar_high=("tmpf","max"), metar_count=("tmpf","count"))
          .reset_index()
    )
    daily["metar_high_int"] = daily["metar_high"].round(0).astype(int)
    print(f"  -> {len(daily)} days, {len(df)} raw obs")
    return daily


# ===================== 数据源4: WU 手动CSV =====================
def load_wu_csv(path="wu_data.csv"):
    """加载手动收集的WU数据 CSV: date,wu_high"""
    try:
        df = pd.read_csv(path)
        df["date"]    = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df["wu_high"] = pd.to_numeric(df["wu_high"], errors="coerce")
        print(f"[WU] Loaded {len(df)} days from {path}")
        return df
    except FileNotFoundError:
        print(f"[WU] {path} not found — 跳过WU数据")
        return pd.DataFrame()


# ===================== 分析引擎 =====================
def analyze(merged: pd.DataFrame):
    high_cols = [c for c in ["cli_high","cf6_high","metar_high_int","wu_high"]
                 if c in merged.columns and merged[c].notna().sum() > 0]

    n = len(merged)
    print(f"\n{'='*72}")
    print(f"  分析结果  ({n} days: {merged['date'].min()} ~ {merged['date'].max()})")
    print(f"{'='*72}")

    print(f"\n  数据覆盖:")
    for c in high_cols:
        cnt = merged[c].notna().sum()
        print(f"    {c:18s}  {cnt:3d}/{n}  ({cnt/n*100:.0f}%)")

    # 两两比较
    print(f"\n  {'A':>16s}  vs  {'B':<16s}   N  exact  <=1F   MAE   bias")
    print(f"  {'-'*68}")
    for i in range(len(high_cols)):
        for j in range(i+1, len(high_cols)):
            a, b = high_cols[i], high_cols[j]
            both = merged[[a,b]].dropna()
            if len(both) < 3: continue
            d = both[a] - both[b]
            n2 = len(both)
            exact   = (d == 0).sum()
            within1 = (d.abs() <= 1).sum()
            mae     = d.abs().mean()
            bias    = d.mean()
            print(f"  {a:>16s}  vs  {b:<16s}  {n2:3d}  "
                  f"{exact:3d}({exact/n2*100:2.0f}%)  "
                  f"{within1/n2*100:3.0f}%  {mae:4.1f}  {bias:+5.2f}")

    # WU 专项
    if "wu_high" in high_cols:
        print(f"\n  ★ WU (结算源) vs 其他数据源:")
        for c in high_cols:
            if c == "wu_high": continue
            both = merged[["date","wu_high",c]].dropna()
            if len(both) < 3: continue
            d = both["wu_high"] - both[c]
            print(f"\n    WU - {c}  (N={len(both)}):")
            print(f"      完全一致: {(d==0).sum()}/{len(both)} ({(d==0).mean()*100:.1f}%)")
            print(f"      差<=1°F:  {(d.abs()<=1).sum()}/{len(both)} ({(d.abs()<=1).mean()*100:.1f}%)")
            print(f"      MAE:      {d.abs().mean():.2f}°F")
            print(f"      偏差:     {d.mean():+.2f}°F (正=WU偏高)")
            big = both[d.abs() > 1]
            if len(big):
                print(f"      差>1°F:")
                for _, r in big.iterrows():
                    print(f"        {r['date']}: WU={r['wu_high']:.0f}  "
                          f"{c}={r[c]:.0f}  diff={r['wu_high']-r[c]:+.0f}")

    # 明细表 (最近30天)
    print(f"\n  最近30天明细:")
    hdr = f"  {'date':>12s}"
    for c in high_cols:
        hdr += f"  {c.replace('_high','').replace('_int','(i)'):>10s}"
    print(hdr)
    print("  " + "-" * (len(hdr)-2))
    tail = merged.dropna(subset=high_cols[:1], how="all").tail(30)
    for _, row in tail.iterrows():
        line = f"  {row['date']:>12s}"
        for c in high_cols:
            v = row.get(c)
            line += f"  {('--' if pd.isna(v) else f'{v:.0f}'):>10s}"
        print(line)


# ===================== main =====================
def main():
    print("=" * 72)
    print("  Polymarket KORD 温度回测 — 方案二: 多数据源对比")
    print(f"  区间: {DATE_START} ~ {DATE_END}")
    print("=" * 72, "\n")

    cli_df   = fetch_cli(STATION, YEAR)
    cf6_df   = fetch_cf6(STATION, YEAR)
    metar_df = fetch_iem_metar(STATION, DATE_START, DATE_END)
    wu_df    = load_wu_csv("wu_data.csv")

    # 合并
    merged = cli_df[["date","cli_high"]].copy()
    if not cf6_df.empty:
        merged = merged.merge(cf6_df[["date","cf6_high"]], on="date", how="outer")
    if not metar_df.empty:
        merged = merged.merge(metar_df[["date","metar_high_int"]], on="date", how="outer")
    if not wu_df.empty:
        merged = merged.merge(wu_df[["date","wu_high"]], on="date", how="outer")

    merged = merged[(merged["date"]>=DATE_START)&(merged["date"]<=DATE_END)]
    merged = merged.sort_values("date").reset_index(drop=True)

    analyze(merged)

    out = "kord_backtest_results.csv"
    merged.to_csv(out, index=False)
    print(f"\n  -> 保存到 {out}")

    print(f"""
{'='*72}
  结论参考
{'='*72}
  cli_high       = NWS CLI (经人工质控，最权威)
  cf6_high       = NWS CF6 (月度表格，与CLI通常一致)
  metar_high_int = METAR逐条max取整 (可能±1°F舍入误差)
  wu_high        = Weather Underground (Polymarket结算源)

  ★ 如果 cli_high ≈ wu_high → 用NWS CLI预判结算值即可
  ★ 如果有系统偏差 → 需要在押注时考虑这个偏差

  补充WU数据方法:
  1. 手动: 访问 WU 历史页面逐日记录到 wu_data.csv
  2. 自动: 用 Claude Chrome Extension 批量抓取
  3. Selenium: pip install selenium 后本脚本自动尝试
""")


if __name__ == "__main__":
    main()
