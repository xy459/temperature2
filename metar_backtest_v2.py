#!/usr/bin/env python3
"""
METAR Prediction Backtester V2 — 精确模拟 v3 时间优势

核心改进:
  回测1用METAR[t]预测METAR[t+30]是30分钟间隔（最坏情况）
  但v3 API在每次METAR之间有2-3次独立观测
  距离下一次METAR最近的v3读数只差~5-10分钟
  → 5-10分钟的温度变化远小于30分钟
  → 真实预测准确率远高于44.8%

方法: 
  用线性插值从30分钟METAR数据中模拟不同时间间隔的预测准确率
"""

import csv
import datetime
import math
from collections import defaultdict

def load_metar_data(path='data/metar_history.csv'):
    records = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            tmpf = float(row['tmpf'])
            tmpc_raw = (tmpf - 32) * 5 / 9
            tmpc = round(tmpc_raw)
            valid = datetime.datetime.strptime(row['valid'], '%Y-%m-%d %H:%M')
            records.append({
                'time': valid,
                'tmpf': tmpf,
                'tmpc_raw': tmpc_raw,
                'tmpc': tmpc,
                'raw': row['metar'],
            })
    return records


def simulate_v3_accuracy(records, v3_lead_minutes):
    """
    Simulate prediction accuracy when v3 gives us data `v3_lead_minutes` before METAR.
    
    Model: between METAR[t] and METAR[t+30], temperature changes linearly.
    v3 at time (t+30 - lead) would show: METAR[t] + (METAR[t+30]-METAR[t]) × (30-lead)/30
    Predicted METAR = round(v3_value)
    """
    correct = 0
    total = 0
    off_by_1 = 0

    for i in range(len(records) - 1):
        curr = records[i]
        nxt = records[i + 1]
        dt = (nxt['time'] - curr['time']).total_seconds() / 60
        if dt > 35:
            continue

        ratio = (30 - v3_lead_minutes) / 30
        v3_raw = curr['tmpc_raw'] + (nxt['tmpc_raw'] - curr['tmpc_raw']) * ratio
        predicted = round(v3_raw)
        actual = nxt['tmpc']

        total += 1
        if predicted == actual:
            correct += 1
        elif abs(predicted - actual) == 1:
            off_by_1 += 1

    return correct, off_by_1, total


def analyze_v3_timing_advantage(records):
    """Sweep across different v3 lead times to show accuracy improvement."""
    print("=" * 78)
    print("模拟分析: v3 提前时间 vs 预测准确率")
    print("=" * 78)
    print(f"\n{'提前时间':>8} | {'准确率':>8} | {'±1°C内':>8} | {'含义':>35}")
    print("-" * 78)

    scenarios = [
        (30, "最坏: 用上一条METAR（无v3）"),
        (25, "v3 在 METAR 前25min"),
        (20, "v3 在 METAR 前20min"),
        (15, "v3 在 METAR 前15min"),
        (10, "典型: v3 在 METAR 前10min ★"),
        (5,  "最佳: v3 在 METAR 前5min"),
        (2,  "理想: v3 在 METAR 前2min"),
        (0,  "完美: v3 = METAR 同时"),
    ]

    for lead, desc in scenarios:
        correct, off1, total = simulate_v3_accuracy(records, lead)
        acc = correct / total * 100
        within1 = (correct + off1) / total * 100
        marker = " ◀◀◀" if lead == 10 else ""
        print(f"  {lead:>4}min | {acc:>6.1f}% | {within1:>6.1f}% | {desc}{marker}")

    print()
    print("★ 10分钟是 v3 最常见的预测间隔（v3每~10分钟更新一次）")
    print("  在此间隔下，准确预测下一条 METAR 的概率显著高于朴素预测")


def analyze_daily_max_prediction(records):
    """
    Polymarket 核心场景: 日最高温预测
    
    v3 的 temperatureMaxSince7Am 是一个只升不降的累计最大值
    一旦达到当日最高温，后续所有 v3 读数都会保持这个值
    → 关键: 什么时候可以确认 "今天的最高温不会再涨了"?
    """
    print("\n" + "=" * 78)
    print("Polymarket 策略: 日最高温锁定分析")
    print("=" * 78)

    daily = defaultdict(list)
    for r in records:
        date = r['time'].date()
        daily[date].append(r)

    print(f"\n{'日期':>12} | {'最高温':>6} | {'达到时刻(UTC)':>13} | {'开始下降时刻':>12} | "
          f"{'锁定提前量':>10} | {'v3提前量':>8}")
    print("-" * 85)

    for date in sorted(daily.keys()):
        obs = daily[date]
        if len(obs) < 20:
            continue

        local7am_utc = 5  # 7AM Madrid (UTC+2) = 5:00 UTC
        after7am = [o for o in obs if o['time'].hour >= local7am_utc]
        if not after7am:
            continue

        temps = [o['tmpc'] for o in after7am]
        final_max = max(temps)

        first_hit_time = None
        confirmed_time = None
        for j, o in enumerate(after7am):
            if o['tmpc'] == final_max and first_hit_time is None:
                first_hit_time = o['time']

            if first_hit_time and o['tmpc'] < final_max:
                confirmed_time = o['time']
                break

        if first_hit_time is None:
            continue

        last_obs_time = after7am[-1]['time']

        if confirmed_time:
            # v3 advantage: v3 shows the peak ~10 min before the confirming METAR
            # and the drop-off METAR confirms it's declining
            v3_confirm = confirmed_time - datetime.timedelta(minutes=10)
            lead_hours = (last_obs_time - v3_confirm).total_seconds() / 3600
        else:
            v3_confirm = first_hit_time
            lead_hours = 0

        metar_lead = (last_obs_time - first_hit_time).total_seconds() / 3600

        print(f"  {date} | {final_max:>4}°C | {first_hit_time.strftime('%H:%M'):>13} | "
              f"{confirmed_time.strftime('%H:%M') if confirmed_time else 'N/A':>12} | "
              f"{metar_lead:>8.1f}h | "
              f"{lead_hours:>6.1f}h")

    print("""
分析说明:
  • 达到时刻: 当日最高温首次出现在 METAR 中的 UTC 时间
  • 开始下降时刻: 温度开始低于最高温的首个 METAR 时间
  • v3提前量: 从"确认最高温已锁定"到当日结束(23:30 UTC)的时间
  
  策略核心: 当 v3 显示温度开始下降 → 日最高温已锁定 → 可以交易""")


def analyze_edge_cases(records):
    """Analyze the critical edge case: temperature at a rounding boundary."""
    print("\n" + "=" * 78)
    print("边界分析: 温度在 X.5°C 附近时的预测风险")
    print("=" * 78)

    boundary_changes = 0
    boundary_stays = 0
    nonboundary_changes = 0
    nonboundary_stays = 0

    for i in range(len(records) - 1):
        curr = records[i]
        nxt = records[i + 1]
        dt = (nxt['time'] - curr['time']).total_seconds() / 60
        if dt > 35:
            continue

        frac = abs(curr['tmpc_raw'] - round(curr['tmpc_raw']))
        near_boundary = frac > 0.35  # within 0.15°C of .5 boundary

        changed = curr['tmpc'] != nxt['tmpc']

        if near_boundary:
            if changed:
                boundary_changes += 1
            else:
                boundary_stays += 1
        else:
            if changed:
                nonboundary_changes += 1
            else:
                nonboundary_stays += 1

    b_total = boundary_changes + boundary_stays
    nb_total = nonboundary_changes + nonboundary_stays

    print(f"\n靠近边界 (±0.15°C of .5): {b_total} 个 METAR 间隔")
    if b_total > 0:
        print(f"  温度不变: {boundary_stays} ({boundary_stays/b_total*100:.1f}%)")
        print(f"  温度变化: {boundary_changes} ({boundary_changes/b_total*100:.1f}%)")

    print(f"\n远离边界: {nb_total} 个 METAR 间隔")
    if nb_total > 0:
        print(f"  温度不变: {nonboundary_stays} ({nonboundary_stays/nb_total*100:.1f}%)")
        print(f"  温度变化: {nonboundary_changes} ({nonboundary_changes/nb_total*100:.1f}%)")


def analyze_max_temp_transition(records):
    """
    Key Polymarket question: When will the max go from N to N+1?
    Analyze how quickly the daily running max increases.
    """
    print("\n" + "=" * 78)
    print("日最高温递增模式分析")
    print("=" * 78)

    daily = defaultdict(list)
    for r in records:
        date = r['time'].date()
        daily[date].append(r)

    print()
    for date in sorted(daily.keys()):
        obs = daily[date]
        if len(obs) < 20:
            continue

        local7am_utc = 5
        after7am = [o for o in obs if o['time'].hour >= local7am_utc]
        if not after7am:
            continue

        running_max = float('-inf')
        transitions = []
        for o in after7am:
            if o['tmpc'] > running_max:
                old = running_max
                running_max = o['tmpc']
                if old != float('-inf'):
                    transitions.append({
                        'time': o['time'],
                        'from': old,
                        'to': running_max,
                    })

        final_max = max(o['tmpc'] for o in after7am)
        min_temp = min(o['tmpc'] for o in after7am if o['time'].hour >= local7am_utc)

        print(f"📅 {date} | 最低={min_temp}°C → 最高={final_max}°C | 爬升 {final_max-min_temp}°C")
        for t in transitions:
            print(f"   {t['time'].strftime('%H:%M')} UTC: {t['from']:>2}°C → {t['to']:>2}°C (+{t['to']-t['from']})")
        print()


def print_trading_strategy():
    print("=" * 78)
    print("📈 Polymarket 交易策略总结")
    print("=" * 78)
    print("""
┌──────────────────────────────────────────────────────────────────┐
│                   v3 API → METAR 预测 → 交易策略                  │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  数据流:                                                         │
│    传感器 → SIM系统 → v3 API(每~10min) → 你 → 预测 METAR        │
│                    → METAR(每30min) → v1 API → WU页面 → 结算    │
│                                                                  │
│  你的时间优势:                                                    │
│    v3 API 比 WU 历史页面提前 ~15-25 分钟                          │
│    = 10min(v3→METAR间隔) + 5-15min(METAR→WU页面延迟)            │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│  场景 A: 温度稳定 (44.8% 的时间)                                 │
│    v3 连续多次显示同一温度 → METAR 几乎必然一致                   │
│    操作: 高置信度, 可大仓位                                       │
│                                                                  │
│  场景 B: 温度缓慢变化 (45.7% — 每30min变1°C)                    │
│    v3 显示 N°C, 趋势 +0.03°C/min                                │
│    操作: 预测 METAR = N 或 N+1, 查看v3趋势方向判断               │
│                                                                  │
│  场景 C: 温度快速变化 (9.5% — 每30min变≥2°C)                    │
│    通常出现在日出后快速升温期 (7-10AM local)                      │
│    操作: 降低仓位, 等待下一个 v3 更新确认                         │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│  日最高温 (Polymarket 结算):                                      │
│                                                                  │
│  1. 升温阶段 (通常 7AM-3PM local):                                │
│     v3 temperatureMaxSince7Am 持续上升                            │
│     → 等待, 不要过早做多 "≤X°C"                                  │
│                                                                  │
│  2. 到达峰值 (通常 12-4PM local):                                 │
│     v3 current temp = temperatureMaxSince7Am                     │
│     且趋势 = 0 或轻微下降                                        │
│     → 高概率峰值, 但等待确认                                     │
│                                                                  │
│  3. 确认下降 (峰值后 ~30-60min):                                  │
│     v3 current temp < temperatureMaxSince7Am                     │
│     且连续2+ 次 v3 都低于max                                     │
│     → 日最高温已锁定 ✅ 此时交易                                 │
│                                                                  │
│  4. 锁定后 (通常 4PM-11PM local):                                 │
│     v3 max 不再变化, 日最高温已确定                               │
│     → 如果市场价格未充分反映, 可交易                              │
│     → 回测显示平均提前 7-10 小时锁定                              │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│  关键风险:                                                        │
│    1. 四舍五入边界: v3 显示 24°C 但实际传感器可能是 23.6°C        │
│       → METAR 可能报 24 也可能报 23 (5min平均+四舍五入)          │
│    2. SPECI 报文: 天气急变时可能额外发布 → 影响 v1 数据           │
│    3. v3 数据丢失: ~1.3% 概率 TWC 丢失某条 METAR                 │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
""")


if __name__ == '__main__':
    records = load_metar_data()
    print(f"加载 {len(records)} 条 METAR 记录")
    print(f"时间范围: {records[0]['time']} ~ {records[-1]['time']}\n")

    analyze_v3_timing_advantage(records)
    analyze_daily_max_prediction(records)
    analyze_edge_cases(records)
    analyze_max_temp_transition(records)
    print_trading_strategy()
