# Polymarket 温度城市 — 不符合条件城市清单

> 条件：数据源为 Weather Underground（WU）+ 摄氏度
> 不满足任一条件的城市列于下表（**美国 K 字机场**现已在 `cities.py` 中用于本仓库 `web_obs` 折线图，见第二节：图表 **°F**，与 Polymarket「摄氏盘」无必然对应。）

---

## 一、数据源非 WU（部分已纳入 `poly/cities.py`）

以下 **3 城**已加入折线图 / Obs 工具（ICAO：**UUWW / LLBG / LTFM**）。多渠道校验（2026-04）：**NOAA METAR** 等对上述三站可返回温度序列；**WU / AVWX / WeatherAPI** 与既有城市相同（需在部署环境有 API Key 后自行 spot-check）。

| 城市 | 参考（官方/可视化） | ICAO |
|---|---|---|
| Moscow（莫斯科） | [NOAA WRH timeseries UUWW](https://www.weather.gov/wrh/timeseries?site=UUWW) | UUWW |
| Tel Aviv（特拉维夫） | [NOAA WRH timeseries LLBG](https://www.weather.gov/wrh/timeseries?site=LLBG) | LLBG |
| Istanbul（伊斯坦布尔） | [NOAA WRH timeseries LTFM](https://www.weather.gov/wrh/timeseries?site=LTFM) | LTFM |

**香港（Hong Kong）**：**暂不纳入** `cities.py`。参考仍为 [天文台气候页](https://www.weather.gov.hk/en/cis/climat.htm)；若日后纳入，机场 METAR 一般为 **VHHH**，亦可对照天文台开放数据 API（`data.weather.gov.hk`）。

---

## 二、使用华氏度（Fahrenheit）的美国城市（已纳入 `cities.py` + `web_obs`）

`fahrenheit: True`：**数据库内温度均为 °F**（WU V1 英制原样；NOAA/AVWX/WeatherAPI 侧为 METAR/摄氏，入库前 `°C→°F`）。**与 Polymarket 的 ICAO 对拍**见 **第六节**（与第四节六城、第五节相同方式：打开事件 **Rules**，只认主规则里「The resolution source … available here:」的**首条** WU 链接末四码；**不使用**已删除的自动脚本）。

| 城市 | 说明 |
|---|---|
| 共 11 城 | 详见 **第六节** 表（含 2026-04-27 对拍结果与与 `cities.py` 是否一致） |

---

## 三、符合条件城市数量统计

| 分类 | 数量 |
|---|---|
| ✅ 符合（WU + 摄氏度） | 29 |
| ✅ 已纳入 app（原「非 WU」三城：莫斯科/特拉维夫/伊斯坦布尔） | 3 |
| ✅ 已纳入 app（六城见下节，ICAO 与 Polymarket 规则内 WU 链接一致） | 6 |
| ⏸ 暂缓纳入（香港） | 1 |
| ✅ 已纳入 app（**美国 11 城**，`fahrenheit`；`web_obs` 展示 **°F**，见上节） | 11 |
| **原清单中「已核查」合计（未计美国 11 城旧口径）** | **44** |
| **`cities.py` 当前城市数** | **49**（= 38 + 11 美国） |

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

- **Panama City（巴拿马城）**：经核查确认使用 WU + 摄氏度，ICAO `MPMG`；当前 `cities.py` **共 49 城**（含美国 11；香港仍暂缓）。

### 2026-04-27：其余 32 城与 Polymarket Rules 主规则对照

已排除上表六城（广州/拉各斯/马尼拉/卡拉奇/开普敦/吉达），对 `cities.py` 中其余城市在 **2026-04-27** 当日事件页（`highest-temperature-in-{slug}-on-april-27-2026`）人工对照：每条只认 **主规则**里「The resolution source … available here:」后的首条链接（WU 路径末四码或 NOAA `site=` 四码）。

| 结论 | 说明 |
|------|------|
| 31 城一致 | 主规则链接四码与 `cities.py` 中 `icao` 相同 |
| **巴黎** | Polymarket 为 **WU `…/LFPB`**（Paris-Le Bourget，Bonneuil-en-France），已把 `cities.py` 中巴黎从 LFPG 改为 **LFPB** |
| 莫斯科 / 特拉维夫 / 伊斯坦布尔 | 主规则为 **NOAA** `weather.gov/wrh/timeseries?site=`，四码仍为 **UUWW / LLBG / LTFM**，与代码一致 |

---

## 六、美国 11 城与 Polymarket（**2026-04-27** 事件页，与第四节**同一核对方式**）

人读各城事件 **Rules** 主文，只认「The resolution source … available here:」后**第一条** Wunderground 链接，路径**末四码** = 结算用 ICAO。已**删除**易误判的 `check_polymarket_icao.py`，不再用脚本批量扫页。

| 城市 | Polymarket 事件 | 规则中站点 / 说明 | WU 末四码 | `cities.py` icao | 对拍结果 |
|------|-----------------|-------------------|-----------|-----------------|---------|
| 丹佛 | [highest-temperature-in-denver…](https://polymarket.com/event/highest-temperature-in-denver-on-april-27-2026) | Buckley Space Force Base（Aurora） | **KBKF** | KBKF | 已按 PM 自 KDEN 更正 |
| 芝加哥 | […chicago…](https://polymarket.com/event/highest-temperature-in-chicago-on-april-27-2026) | Chicago O’Hare Intl Airport | KORD | KORD | 一致 |
| 纽约 | […new-york-city…](https://polymarket.com/event/highest-temperature-in-new-york-city-on-april-27-2026) | **该日 404 无此事件**（Rules 未手开）；按用户指定 **LaGuardia** | **KLGA** | KLGA | 与 `cities.py` 一致 |
| 达拉斯 | […dallas…](https://polymarket.com/event/highest-temperature-in-dallas-on-april-27-2026) | Dallas **Love Field** | **KDAL** | KDAL | 已按 PM 自 KDFW 更正 |
| 奥斯汀 | […austin…](https://polymarket.com/event/highest-temperature-in-austin-on-april-27-2026) | Austin-Bergstrom International | KAUS | KAUS | 一致 |
| 洛杉矶 | […los-angeles…](https://polymarket.com/event/highest-temperature-in-los-angeles-on-april-27-2026) | Los Angeles International | KLAX | KLAX | 一致 |
| 迈阿密 | […miami…](https://polymarket.com/event/highest-temperature-in-miami-on-april-27-2026) | Miami International | KMIA | KMIA | 一致 |
| 亚特兰大 | […atlanta…](https://polymarket.com/event/highest-temperature-in-atlanta-on-april-27-2026) | Hartsfield-Jackson International | KATL | KATL | 一致 |
| 西雅图 | […seattle…](https://polymarket.com/event/highest-temperature-in-seattle-on-april-27-2026) | Seattle-Tacoma International（WU 区划名 seatac） | KSEA | KSEA | 一致 |
| 休斯顿 | […houston…](https://polymarket.com/event/highest-temperature-in-houston-on-april-27-2026) | William P. Hobby Airport | KHOU | KHOU | 一致 |
| 旧金山 | […san-francisco…](https://polymarket.com/event/highest-temperature-in-san-francisco-on-april-27-2026) | San Francisco International | KSFO | KSFO | 一致 |

> 换日开盘时须重新打开**对应日期**事件页；纽约等城市若无事件则为「无页可对」。

*文件生成日期：2026-04-08 | 最后更新：美国城第六节手工对拍、丹佛→KBKF/达拉斯→KDAL/纽约待核；已移除自动核对脚本；香港仍暂不纳入）*
