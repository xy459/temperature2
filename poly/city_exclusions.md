# Polymarket 温度城市 — 不符合条件城市清单

> 条件：数据源为 Weather Underground（WU）+ 摄氏度
> 不满足任一条件的城市列于下表

---

## 一、数据源非 WU（部分已纳入 `poly/cities.py`）

以下 **3 城**已加入折线图 / Obs 工具（ICAO：**UUWW / LLBG / LTFM**）。多渠道校验（2026-04）：**NOAA METAR**、**IEM ASOS** 对上述三站均可返回温度序列；**WU / AVWX / WeatherAPI** 与既有城市相同（需在部署环境有 API Key 后自行 spot-check）。

| 城市 | 参考（官方/可视化） | ICAO |
|---|---|---|
| Moscow（莫斯科） | [NOAA WRH timeseries UUWW](https://www.weather.gov/wrh/timeseries?site=UUWW) | UUWW |
| Tel Aviv（特拉维夫） | [NOAA WRH timeseries LLBG](https://www.weather.gov/wrh/timeseries?site=LLBG) | LLBG |
| Istanbul（伊斯坦布尔） | [NOAA WRH timeseries LTFM](https://www.weather.gov/wrh/timeseries?site=LTFM) | LTFM |

**香港（Hong Kong）**：**暂不纳入** `cities.py`。参考仍为 [天文台气候页](https://www.weather.gov.hk/en/cis/climat.htm)；若日后纳入，机场 METAR 一般为 **VHHH**，亦可对照天文台开放数据 API（`data.weather.gov.hk`）。

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
| ✅ 已纳入 app（原「非 WU」三城：莫斯科/特拉维夫/伊斯坦布尔） | 3 |
| ✅ 已纳入 app（六城见下节，ICAO 与 Polymarket 规则内 WU 链接一致） | 6 |
| ⏸ 暂缓纳入（香港） | 1 |
| ❌ 华氏度（美国等，未纳入） | 11 |
| **原清单中「已核查」合计** | **44** |
| **`cities.py` 当前城市数** | **38**（= 29 + 3 + 6） |

---

## 四、已纳入 `cities.py`：Polymarket 温度盘口与 WU 站（六城，2026-04 核对）

各城「Rules」中给出的 **Wunderground history** 链接末尾四码，即下表 `icao`（`web_obs` / 折线图按该 ICAO 拉数）。

| Slug / 城 | 规则中的站点/备注 | `icao` | WU 规则链接（示例日） |
|-------------|-------------------|--------|------------------------|
| `guangzhou` | 广州白云国际机场 | ZGGG | `…/cn/guangzhou/ZGGG` |
| `lagos` | 拉各斯 Murtala Muhammad 国际机场 | DNMM | `…/ng/lagos/DNMM` |
| `manila` | 马尼拉 Ninoy Aquino 国际机场 | RPLL | `…/ph/manila/RPLL` |
| `karachi` | 规则文字有「Masroor Airbase」表述；**结算以链接为准** | **OPKC** | `…/pk/karachi/OPKC` |
| `cape-town` | 开普敦国际机场（WU 路径区划为 matroosfontein） | FACT | `…/za/matroosfontein/FACT` |
| `jeddah` | 吉达阿卜杜勒-阿齐兹国王机场 | OEJN | `…/sa/jeddah/OEJN` |

事件页模式：`https://polymarket.com/event/highest-temperature-in-{slug}-on-{month}-{day}-{year}`（具体日期的规则与链接以该页 **Rules** 为准）。

---

## 五、补充说明

- **Panama City（巴拿马城）**：经核查确认使用 WU + 摄氏度，ICAO `MPMG`；当前 `cities.py` 在「29 + 莫斯科等 3 + 上表六城」**共 38 座**（香港暂缓）。

*文件生成日期：2026-04-08 | 最后更新：2026-04-27（新增六城与 Polymarket/WU 对照；香港仍暂不纳入）*
