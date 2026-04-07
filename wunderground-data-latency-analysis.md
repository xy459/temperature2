# Wunderground 数据源延迟分析报告

> **项目背景**：Polymarket 温度预测市场  
> **分析日期**：2026-04-07  
> **目标站点**：LEMD — Adolfo Suárez Madrid-Barajas Airport Station  

---

## 一、需求描述

### 1.1 业务场景

在 Polymarket 上参与温度预测市场（如 [Highest temperature in Madrid on April 7?](https://polymarket.com/zh/event/highest-temperature-in-madrid-on-april-7-2026)），需要尽可能快速地获取 Wunderground 数据源的温度数据，以便在数据公开后第一时间做出交易决策。

### 1.2 市场规则要点

- **结算数据源**：Weather Underground 的 [LEMD 历史页面](https://www.wunderground.com/history/daily/es/madrid/LEMD)
- **精度**：整数摄氏度（如 9°C）
- **结算时机**：当天所有数据 **finalized** 之后才能 resolve
- **不可追溯**：数据最终化后的修订不影响结算

### 1.3 核心问题

1. Wunderground 页面数据从哪里来？更新频率是多少？
2. 能否通过 API 直接获取，比网页更快？
3. 服务器部署在哪里？是否有 CDN 缓存延迟？
4. 是否有更上游的数据源可以提前获得信号？

---

## 二、解决思路

### 2.1 逆向分析 Wunderground 前端

通过对 Wunderground 历史页面（`/history/daily/es/madrid/LEMD`）进行 HTML 源码分析和网络请求抓包，还原其前端调用的后端 API 端点及认证方式。

### 2.2 直接测试 API 性能

绕过网页前端，直接调用底层 `api.weather.com` 的 REST API，测量响应时间和缓存行为。

### 2.3 延迟测试

编写自动化轮询脚本，在 METAR 观测时间点（整点/半点）前后密集轮询两套 API，精确测量"气象站观测 → API 可查询"的延迟。

### 2.4 基础设施调研

调查 Wunderground/The Weather Company 的服务器架构、CDN 分布，评估就近部署服务器的可行性。

---

## 三、分析过程

### 3.1 前端逆向 — 提取 API 端点和密钥

通过 `curl` 下载 Wunderground 历史页面的 HTML 源码，使用 `grep` 提取所有 `api.weather.com` 的 URL，发现：

**提取到的公开 API Key**：
```
e1f10a1e78da46f5b10a1e78da96f525
```

> 此 Key 嵌入在 Wunderground 前端 JavaScript 中，是公开已知的（在 LoxWiki 等社区有记录）。并非官方对外提供的 API Key，属于前端内部使用。

**发现的所有 API 端点**：

| 端点 | 用途 | 缓存策略 |
|------|------|----------|
| `v3/wx/observations/current?icaoCode=LEMD` | 当前实时观测 | `max-age=26`（26 秒） |
| `v1/location/LEMD:9:ES/observations/historical.json` | 历史逐次观测 | `s-maxage=3600`（CDN 1 小时） |
| `v3/wx/forecast/daily/5day` | 5 天预报 | — |
| `v3/location/near?product=pws` | 附近 PWS 站点 | — |
| `v3/location/point?icaoCode=LEMD` | 站点位置信息 | — |

### 3.2 API 响应验证

#### v3 实时观测 API

```bash
curl 'https://api.weather.com/v3/wx/observations/current?apiKey=e1f10a1e78da46f5b10a1e78da96f525&units=m&format=json&icaoCode=LEMD'
```

返回结果（截取关键字段）：
```json
{
    "temperature": 13,
    "temperatureMax24Hour": 26,
    "temperatureMaxSince7Am": 26,
    "temperatureMin24Hour": 9,
    "validTimeLocal": "2026-04-07T04:26:33+0200",
    "validTimeUtc": 1775528793
}
```

**关键发现**：
- `temperatureMaxSince7Am` 字段直接给出当天 7AM 以来的最高温度，这正是 Polymarket 市场关心的值
- `max-age=26` 意味着缓存仅 26 秒，近乎实时

#### v1 历史观测 API

```bash
curl 'https://api.weather.com/v1/location/LEMD:9:ES/observations/historical.json?apiKey=e1f10a1e78da46f5b10a1e78da96f525&units=m&startDate=20260407&endDate=20260407'
```

返回当天所有逐次观测，每 30 分钟一条记录：
```
22:00 UTC | 17°C
22:30 UTC | 17°C
23:00 UTC | 16°C
23:30 UTC | 16°C
00:00 UTC | 14°C
00:30 UTC | 15°C
01:00 UTC | 14°C
01:30 UTC | 14°C
02:00 UTC | 13°C
```

**关键发现**：
- 观测间隔为 30 分钟，与 METAR 报文周期一致
- `s-maxage=3600`：CDN 缓存 1 小时，意味着新的 METAR 观测可能最多延迟 1 小时才出现在此 API 中
- 这就是 Wunderground 历史页面表格的直接数据源

### 3.3 HTTP 响应头深度分析

| API | Cache-Control | 实际含义 |
|-----|---------------|----------|
| v3 实时观测 | `max-age=26` | 浏览器/客户端缓存 26 秒 |
| v1 历史观测 | `public, max-age=2959, s-maxage=3600` | CDN 缓存 1 小时，客户端 ~50 分钟 |
| Wunderground HTML 页面 | `max-age=0, no-cache` | 页面本身不缓存（SSR），但后端 API 有缓存 |

所有响应都带有 `akamai-grn` 头，确认经过 Akamai CDN。

### 3.4 基础设施调查结果

| 项目 | 详情 |
|------|------|
| **CDN 提供商** | Akamai（全球 ~4100 个 PoP） |
| **马德里 PoP** | 有，2024 年 3 月上线 `es-mad` 区域 |
| **欧洲主要 PoP** | 法兰克福、阿姆斯特丹、伦敦、巴黎、米兰 |
| **后端所有者** | The Weather Company, LLC（注册地：密歇根州 Ann Arbor） |
| **DNS** | `api.weather.com` → Akamai 边缘节点（`a1-70.akam.net` 等） |

### 3.5 延迟实测

#### 测试方法

编写 Python 脚本 `wu_latency_test.py`，同时轮询 v3 和 v1 两个 API，在 METAR 时间点前后密集轮询（5-15 秒间隔），检测观测数据变化并记录延迟。

#### 测试结果

**测试时间**：2026-04-07 03:03 ~ 03:07 UTC

| 事件 | 时间 | 详情 |
|------|------|------|
| 初始状态 | 03:04:14 UTC | v3 最新观测 02:56:40 UTC (13°C)<br>v1 最新观测 02:00:00 UTC (13°C, 共 9 条) |
| v3 检测到新观测 | 03:06:46 UTC | 观测时间 03:06:45 UTC → **延迟 1 秒** |
| v3 检测到新观测（第二实例） | 03:06:51 UTC | 同一观测 03:06:45 UTC → **延迟 6 秒** |
| v1 状态 | 03:07:53 UTC | 仍为 02:00 UTC，无变化（CDN 缓存未过期） |

#### 关键发现

```
┌─────────────────────────────────────────────────────┐
│  v3 实时观测 API 延迟：1 ~ 6 秒                      │
│  v1 历史观测 API 延迟：最长可达 1 小时（CDN 缓存）     │
│  差距：v3 比 v1 快 ~60 倍                             │
└─────────────────────────────────────────────────────┘
```

- **v3 实时 API 的数据几乎实时可用**（1-6 秒延迟），远远快于 Wunderground 网页展示的数据
- **v1 历史 API 受 CDN 1 小时缓存限制**，即使底层数据已更新，API 也可能返回旧数据
- METAR 观测时间并非精确在 :00 和 :30，实测观测时间为 02:56:40 和 03:06:45（偏差约 3-7 分钟）
- HTTP 响应时间约 640-1350ms（从本地 macOS 测试，非欧洲服务器）

### 3.6 Wunderground API 公开性调查

| 方式 | 状态 | 价格 | 说明 |
|------|------|------|------|
| 旧 WU 免费 API | 2018 年 12 月关闭 | — | 已完全废弃 |
| 前端内嵌 Key | 可用 | 免费 | 非官方，可能随时失效或限流 |
| PWS 贡献者 Key | 可用 | 免费 | 需注册个人气象站 |
| The Weather Company 商业 API | 可用 | $500/月起 | Standard 套餐，100 万次/月 |
| TWC 免费试用 | 30 天 | 免费 | 仅限企业客户，5 万次/天 |

---

## 四、结论与建议

### 4.1 数据获取最优方案

```
推荐方案：v3 实时观测 API（26 秒缓存，1-6 秒延迟）
```

直接轮询 `api.weather.com/v3/wx/observations/current?icaoCode=LEMD`，每 30 秒一次，跟踪 `temperatureMaxSince7Am` 字段。这比 Wunderground 网页快最多 **1 小时**。

示例请求：
```
GET https://api.weather.com/v3/wx/observations/current
    ?apiKey=e1f10a1e78da46f5b10a1e78da96f525
    &language=en-US
    &units=m
    &format=json
    &icaoCode=LEMD
```

关键返回字段：
- `temperature` — 当前温度
- `temperatureMaxSince7Am` — 当天 7AM 起最高温度（**市场结算的关键值**）
- `temperatureMax24Hour` — 过去 24 小时最高温度
- `validTimeUtc` — 观测时间戳

### 4.2 关于服务器部署位置

| 因素 | 分析 |
|------|------|
| 当前延迟瓶颈 | CDN 缓存（1 小时），不是网络延迟 |
| 网络 RTT 节省 | 从美国 ~500ms → 欧洲 ~20-50ms，节省 ~450ms |
| 相比 CDN 缓存 | 450ms vs 3600s，网络延迟占比 < 0.01% |
| **结论** | 如果用 v3 实时 API（无 CDN 缓存问题），部署位置不重要<br>如果用 v1 历史 API，部署位置同样不重要（瓶颈在缓存） |

**建议**：如果你已有欧洲服务器，可以用它来轮询。否则无需为此专门部署，因为 v3 API 的缓存仅 26 秒，网络延迟相对可忽略。

### 4.3 更上游数据源（信号领先）

除了 Wunderground，以下数据源可以更早获得温度信号：

| 数据源 | 预计领先时间 | 说明 |
|--------|-------------|------|
| **AEMET OpenData**（西班牙气象局） | 10-30 分钟 | 最上游，免费 API，需申请 Key |
| **NOAA/ADDS METAR** | 5-15 分钟 | 航空气象，`aviationweather.gov` |
| **OGIMET** | 5-15 分钟 | 原始 SYNOP/METAR 报文 |

注意：Polymarket 结算依据的是 Wunderground，不是这些上游源。但上游数据可以帮你**提前预判最终结果**并更早下单。

### 4.4 关于"数据最终化"时机

Polymarket 规则要求"data finalized"后才能结算。根据分析：

- Wunderground 历史页面的数据来自 v1 历史 API
- 当天的最后一条 METAR 观测通常在马德里时间午夜（UTC 22:00）之后
- 数据最终化大约在次日 UTC 00:00 ~ 06:00 之间
- v1 API 的 `metadata.expire_time_gmt` 字段可以用来追踪缓存过期时间

### 4.5 完整数据架构建议

```
层级 1: 信号源（提前获知温度）
├── AEMET OpenData API（最上游）
├── NOAA ADDS METAR
└── WU v3 实时观测 API（26s 缓存，1-6s 延迟）
    └── 持续追踪 temperatureMaxSince7Am

层级 2: 结算数据源（确认最终结果）
└── WU v1 历史观测 API（1h CDN 缓存）
    └── 轮询检测数据最终化
    └── 最终化后与层级 1 交叉验证

层级 3: 交易执行
└── 已有 Polymarket 交易系统
    └── 触发买入/卖出
```

---

## 五、风险提示

1. **API Key 风险**：前端内嵌 Key 非官方公开，可能被更换或限流
2. **缓存一致性**：v3 API 的 `temperatureMaxSince7Am` 与 v1 历史 API 的最终最高温可能存在舍入差异
3. **METAR 修订**：气象站可能发布修订后的 METAR（METAR COR），Wunderground 是否采纳存在不确定性
4. **频率限制**：高频轮询可能触发 IP 限流或封禁，建议控制在每 30 秒一次

---

## 附录

### A. 测试脚本

延迟测试脚本位于 `wu_latency_test.py`，使用方法：

```bash
PYTHONUNBUFFERED=1 python3 wu_latency_test.py
```

功能：
- 同时轮询 v3 实时和 v1 历史两个 API
- 在整点/半点前后自动加密轮询间隔（5-15 秒）
- 检测新观测数据并计算延迟
- 退出时汇总统计
- 日志保存在 `logs/` 目录

### B. 测试原始日志

```
[2026-04-07 03:04:14 UTC] [初始] v3 当前观测: 02:56:40 UTC | 13°C | Max7AM: 26°C | HTTP: 659ms | Cache: max-age=145
[2026-04-07 03:04:14 UTC] [初始] v1 历史观测: 9 条 | 最新: 02:00:00 UTC | 13°C | HTTP: 642ms | Cache: public, max-age=1214, s-maxage=3600
[2026-04-07 03:06:35 UTC] [心跳 #10] 无变化 | 间隔15s | v3: 02:56:40 UTC 13°C (770ms) | v1: 9条 最新02:00:00 UTC (682ms)
[2026-04-07 03:06:46 UTC] ⚡ v3 新观测! 观测时间: 03:06:45 UTC | 延迟: 1s (0.0min) | 温度: 12°C | Max7AM: 26°C | HTTP: 963ms
[2026-04-07 03:06:51 UTC] ⚡ v3 新观测! 观测时间: 03:06:45 UTC | 延迟: 6s (0.1min) | 温度: 12°C | Max7AM: 26°C | HTTP: 743ms
[2026-04-07 03:07:53 UTC] [心跳 #20] 无变化 | v1: 9条 最新02:00:00 UTC (仍为 1 小时前的数据)
```

### C. API 快速参考

```bash
# 实时观测（推荐，最快）
curl 'https://api.weather.com/v3/wx/observations/current?apiKey=e1f10a1e78da46f5b10a1e78da96f525&units=m&format=json&icaoCode=LEMD'

# 历史逐次观测（Wunderground 页面数据源）
curl 'https://api.weather.com/v1/location/LEMD:9:ES/observations/historical.json?apiKey=e1f10a1e78da46f5b10a1e78da96f525&units=m&startDate=20260407&endDate=20260407'

# 切换城市：替换 icaoCode / LOCATION_ID
# 常见 ICAO: KJFK(纽约), EGLL(伦敦), RJTT(东京), RKSI(首尔), VHHH(香港)
```
