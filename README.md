# KORD 温度回测工具 — Polymarket 温度盘口

## 快速开始

```bash
# 1. 安装依赖
pip install requests pandas

# 2. 先不带WU数据运行，对比 CLI vs CF6 vs METAR
python kord_backtest.py

# 3. 获取WU数据 (选一种方法):

# 方法A: Selenium自动抓取
pip install selenium webdriver-manager
python wu_scraper.py

# 方法B: 手动收集
python wu_scraper.py --manual   # 打印所有URL，手动记录到wu_data.csv

# 方法C: Claude Chrome Extension
# 在Claude对话中让它帮你批量访问WU历史页面提取数据

# 4. 有了 wu_data.csv 后重新运行
python kord_backtest.py
```

## 数据源说明

| 源 | 字段 | 来源 | 特点 |
|---|---|---|---|
| NWS CLI | cli_high | IEM API → NWS气候报告 | **最权威**，经人工质控 |
| NWS CF6 | cf6_high | IEM API → NWS月度表 | 与CLI通常一致 |
| METAR | metar_high_int | IEM API → 逐条METAR | 可能有±1°F舍入误差 |
| WU | wu_high | Weather Underground | **Polymarket结算源** |

## 关键API参考

```
# NWS CLI (KORD 每日气候报告)
https://mesonet.agron.iastate.edu/json/cli.py?station=KORD&year=2026

# NWS CF6 (KORD 月度气候表)
https://mesonet.agron.iastate.edu/json/cf6.py?station=KORD&year=2026

# METAR逐条下载
https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=KORD&...

# WU 历史 (需JS渲染)
https://www.wunderground.com/history/daily/us/il/chicago/KORD/date/2026-3-15

# NWS O'Hare 点预报 (用于交易决策)
https://forecast.weather.gov/MapClick.php?textField1=41.98&textField2=-87.9
```

## wu_data.csv 格式

```csv
date,wu_high
2026-02-01,35
2026-02-02,42
2026-02-03,38
```

## ⚠️ ASOS温度精度注意

参考: https://mesonet.agron.iastate.edu/onsite/news.phtml?id=1469

- ASOS 内部存储整数°F，但传输时转为整数°C再转回
- 这个 F→C→F 往返可能导致 ±1°F 的舍入误差
- 实时METAR数据和最终CLI报告可能相差1°F
- 对于边缘盘口(如48-49 vs 50-51)，这个差异可能影响结算
