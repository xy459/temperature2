# Polymarket 温度城市 — 不符合条件城市清单

> 条件：数据源为 Weather Underground（WU）+ 摄氏度
> 不满足任一条件的城市列于下表

---

## 一、数据源非 WU（使用 NOAA 或其他官方数据源）

| 城市 | 数据源机构 | 数据源 URL | 温度单位 | ICAO/站点 |
|---|---|---|---|---|
| Moscow（莫斯科） | NOAA | https://www.weather.gov/wrh/timeseries?site=UUWW | 摄氏度 | UUWW（伏努科沃机场） |
| Tel Aviv（特拉维夫） | NOAA | https://www.weather.gov/wrh/timeseries?site=LLBG | 摄氏度 | LLBG（本古里安机场） |
| Istanbul（伊斯坦布尔） | NOAA | https://www.weather.gov/wrh/timeseries?site=LTFM | 摄氏度 | LTFM（伊斯坦布尔机场） |
| Hong Kong（香港） | 香港天文台 | https://www.weather.gov.hk/en/cis/climat.htm | 摄氏度（精确到0.1°C） | — |

> 注：以上城市均使用摄氏度，但数据源不是 WU，无法通过现有 WU v3 API 复用。
> Hong Kong 额外特殊点：分辨率为 0.1°C（其余城市为整数°C）。

---

## 二、使用华氏度（Fahrenheit）的城市

| 城市 | 数据源机构 | 数据源 URL | 温度单位 | ICAO/站点 |
|---|---|---|---|---|
| Denver（丹佛） | WU | https://www.wunderground.com/history/daily/us/denver/KDEN | 华氏度 | KDEN |
| Chicago（芝加哥） | WU | https://www.wunderground.com/history/daily/us/chicago/KORD | 华氏度 | KORD |
| New York City（纽约） | WU | https://www.wunderground.com/history/daily/us/new-york-city/KJFK | 华氏度 | KJFK |
| Dallas（达拉斯） | WU | https://www.wunderground.com/history/daily/us/dallas/KDFW | 华氏度 | KDFW |
| Austin（奥斯汀） | WU | https://www.wunderground.com/history/daily/us/austin/KAUS | 华氏度 | KAUS |
| Los Angeles（洛杉矶） | WU | https://www.wunderground.com/history/daily/us/los-angeles/KLAX | 华氏度 | KLAX |
| Miami（迈阿密） | WU | https://www.wunderground.com/history/daily/us/miami/KMIA | 华氏度 | KMIA |
| Atlanta（亚特兰大） | WU | https://www.wunderground.com/history/daily/us/atlanta/KATL | 华氏度 | KATL |
| Seattle（西雅图） | WU | https://www.wunderground.com/history/daily/us/seattle/KSEA | 华氏度 | KSEA |
| Houston（休斯顿） | WU | https://www.wunderground.com/history/daily/us/houston/KHOU | 华氏度 | KHOU |
| San Francisco（旧金山） | WU | https://www.wunderground.com/history/daily/us/san-francisco/KSFO | 华氏度 | KSFO |

---

## 三、符合条件城市数量统计

| 分类 | 数量 |
|---|---|
| ✅ 符合（WU + 摄氏度） | 29 |
| ❌ 数据源非 WU | 4 |
| ❌ 华氏度 | 11 |
| **总计（已核查）** | **44** |

---

---

## 四、补充说明

- **Panama City（巴拿马城）**：经核查确认使用 WU + 摄氏度，ICAO `MPMG`（Marcos A. Gelabert 国际机场），**属于符合条件城市**，共 29 座。

*文件生成日期：2026-04-08 | 最后更新：2026-04-08*
