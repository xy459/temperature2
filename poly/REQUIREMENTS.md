# Polymarket 温度事件自动交易机器人 — 需求与逻辑文档

> 版本：1.0 | 日期：2026-04-08

---

## 一、项目概述

根据 Weather Underground（WU）实时观测数据，自动在 Polymarket 温度事件中以 FAK 限价单买入 NO 份额。

**核心流程**：持续拉取 WU 观测数据 → 计算最新均温 → 与 Polymarket 盘口档位比较 → 满足条件时自动下单。

---

## 二、支持城市

**条件**：Polymarket 事件数据源为 **Weather Underground（WU）** 且温度单位为 **摄氏度（°C）**。

### 2.1 支持城市列表（23 座，可扩展）

| 城市 | ICAO | 时区 |
|---|---|---|
| Madrid（马德里） | LEMD | Europe/Madrid |
| London（伦敦） | EGLC | Europe/London |
| Paris（巴黎） | LFPO | Europe/Paris |
| Tokyo（东京） | RJTT | Asia/Tokyo |
| Shanghai（上海） | ZSPD | Asia/Shanghai |
| Beijing（北京） | ZBAA | Asia/Shanghai |
| Chongqing（重庆） | ZUCK | Asia/Shanghai |
| Wuhan（武汉） | ZHHH | Asia/Shanghai |
| Chengdu（成都） | ZUUU | Asia/Shanghai |
| Seoul（首尔） | RKSI | Asia/Seoul |
| Singapore（新加坡） | WSSS | Asia/Singapore |
| Taipei（台北） | RCTP | Asia/Taipei |
| Lucknow（勒克瑙） | VILK | Asia/Kolkata |
| Wellington（惠灵顿） | NZWN | Pacific/Auckland |
| Toronto（多伦多） | CYYZ | America/Toronto |
| Buenos Aires（布宜诺斯艾利斯） | SAEZ | America/Argentina/Buenos_Aires |
| Sao Paulo（圣保罗） | SBGR | America/Sao_Paulo |
| Mexico City（墨西哥城） | MMMX | America/Mexico_City |
| Panama City（巴拿马城） | MPMG | America/Panama |
| Ankara（安卡拉） | LTAC | Europe/Istanbul |
| Munich（慕尼黑） | EDDM | Europe/Berlin |
| Milan（米兰） | LIML | Europe/Rome |
| Warsaw（华沙） | EPWA | Europe/Warsaw |

### 2.2 排除城市

详见 `city_exclusions.md`：
- **数据源非 WU**：Moscow、Tel Aviv、Istanbul、Hong Kong
- **华氏度**：所有美国城市（Denver、Chicago、NYC 等）

---

## 三、数据库设计（SQLite，WAL 模式）

### `observations` 表
| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | 自增 |
| city_icao | TEXT | ICAO 代码 |
| obs_time | DATETIME | WU 观测时间（去重依据） |
| poll_time | DATETIME | 本地拉取时间（UTC） |
| temperature | REAL | 气温（°C） |
| temp_max_since_7am | REAL | 当日 07:00 以来最高温 |

**唯一约束**：`(city_icao, obs_time)`

### `trade_state` 表
| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | 自增 |
| city_icao | TEXT | 城市 |
| event_date | DATE | 事件日期（城市本地日期） |
| bracket_temp | INTEGER | 档口温度 |
| offset | INTEGER | 触发偏移（-1 或 -2） |
| triggered_at | DATETIME | 首次触发时间 |

**唯一约束**：`(city_icao, event_date, bracket_temp, offset)`

### `orders` 表
| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | 自增 |
| city_icao | TEXT | 城市 |
| event_date | DATE | 事件日期 |
| bracket_temp | INTEGER | 档口温度 |
| offset | INTEGER | -1 或 -2 |
| token_id | TEXT | Polymarket NO token ID |
| price | REAL | 限价 |
| size | REAL | 买入数量 |
| order_id | TEXT | Polymarket 返回订单 ID |
| status | TEXT | filled / cancelled / error |
| wallet | TEXT | 使用的钱包地址（掩码显示） |
| raw_response | TEXT | API 原始返回 |
| created_at | DATETIME | 下单时间 |

### `settings` 表
| 字段 | 类型 | 说明 |
|---|---|---|
| key | TEXT PK | 设置项名 |
| value | TEXT | 设置值 |

---

## 四、配置参数（`config.py` + `.env`）

```python
# WU API
WU_API_KEY               = "..."          # WU API 密钥（.env）
WU_POLL_INTERVAL_SECONDS = 60             # obs 拉取间隔（秒）
WU_API_BASE              = "https://api.weather.com/v3/wx/observations/current"

# obs 数据质量（严格模式）
OBS_MAX_SECOND_AGE_MINUTES = 23   # 较老那条 obs 距现在最大允许时间（分钟）
OBS_MIN_GAP_MINUTES        = 9    # 两条 obs 之间最小时间间隔（分钟）

# 交易参数
OFFSET_MINUS_2_PRICE = 0.98    # avg-2 档口限价（USDC）
OFFSET_MINUS_2_SIZE  = 200     # avg-2 买入份额
OFFSET_MINUS_1_PRICE = 0.90    # avg-1 档口限价（USDC）
OFFSET_MINUS_1_SIZE  = 200     # avg-1 买入份额

# 特殊档口规则
BRACKET_SKIP_KEYWORD  = "or higher"   # 包含此关键词的档口跳过
BRACKET_LOWER_KEYWORD = "or below"    # 包含此关键词，bracket_temp 视为 BRACKET_LOWER_TEMP
BRACKET_LOWER_TEMP    = 17

# 交易检查间隔
TRADE_CHECK_INTERVAL_SECONDS = 60

# Polymarket CLOB
CLOB_HOST         = "https://clob.polymarket.com"
POLYGON_CHAIN_ID  = 137
SIGNATURE_TYPE    = 2    # 2=GNOSIS_SAFE，1=POLY_PROXY，0=EOA
```

---

## 五、WU 数据拉取线程（`obs_poller.py`）

**线程模型**：单线程，顺序轮询所有城市。

```
启动
  │
  └─► 循环（每 60 秒）
         │
         ├─► 对每个城市（city_icao）:
         │       │
         │       ▼
         │     GET WU v3 API
         │     ?icaoCode={icao}&units=m&format=json
         │       │
         │       ├─[HTTP/超时异常]─► log.error + continue
         │       │
         │       ▼
         │     提取 validTimeUtc → obs_time
         │     提取 temperature, temperatureMaxSince7Am
         │       │
         │       ▼
         │     INSERT OR IGNORE INTO observations
         │     （唯一约束自动去重）
         │       │
         │       ├─[新数据写入]─► log.info("新obs: {city} {obs_time} {temp}°C")
         │       └─[已存在跳过]─► 静默处理
         │
         └─► sleep(60)
```

---

## 六、交易线程（`trader.py`）

**线程模型**：单线程，顺序处理所有城市。

```
启动
  │
  └─► 循环（每 60 秒）
         │
         ├─► 对每个城市:
         │
         │   ┌─ STEP 1: 读取最近 2 条 obs ─────────────────────────────┐
         │   │  SELECT obs_time, temperature                            │
         │   │  FROM observations WHERE city_icao=?                    │
         │   │  ORDER BY obs_time DESC LIMIT 2                         │
         │   └──────────────────────────────────────────────────────────┘
         │          │
         │          ├─[不足 2 条]─► log.warning + 跳过
         │          │
         │   ┌─ STEP 2: 数据质量校验（严格模式）──────────────────────┐
         │   │                                                         │
         │   │  t1 = obs[0].obs_time（较新）                          │
         │   │  t2 = obs[1].obs_time（较老）                          │
         │   │                                                         │
         │   │  规则一：t2 距现在 ≤ 23 分钟                           │
         │   │    否 → log.error("obs 过期") + 跳过                   │
         │   │                                                         │
         │   │  规则二：t1 - t2 > 9 分钟                              │
         │   │    否 → log.error("obs 间隔不足") + 跳过               │
         │   └──────────────────────────────────────────────────────────┘
         │          │
         │   ┌─ STEP 3: 计算均值 ──────────────────────────────────────┐
         │   │  avg_temp = math.floor((obs[0].temp + obs[1].temp) / 2) │
         │   │  示例：(20+21)/2=20.5 → floor → 20                     │
         │   │         (20+22)/2=21.0 → floor → 21                    │
         │   └──────────────────────────────────────────────────────────┘
         │          │
         │   ┌─ STEP 4: 获取当日市场档口 ──────────────────────────────┐
         │   │  event_date = 城市本地今日日期                          │
         │   │  slug = "highest-temperature-in-{city_slug}-on-{date}"  │
         │   │  调用 Gamma API → 解析各档口 bracket_temp + no_token_id │
         │   │    失败 → log.error + 跳过                              │
         │   └──────────────────────────────────────────────────────────┘
         │          │
         │   ┌─ STEP 5: 遍历档口，判断触发条件 ───────────────────────┐
         │   │                                                         │
         │   │  对每个 bracket（question, bracket_temp, no_token_id）: │
         │   │                                                         │
         │   │  ① "or higher" in question → 跳过                     │
         │   │  ② "or below"  in question → bracket_temp = 17        │
         │   │  ③ 其他 → 从 question 解析整数温度                     │
         │   │                                                         │
         │   │  对 offset in [-2, -1]:                                │
         │   │    target_temp = avg_temp + offset                     │
         │   │    bracket_temp != target_temp → 跳过                  │
         │   │                                                         │
         │   │    查 trade_state 是否已触发                           │
         │   │    已触发 → 跳过                                       │
         │   └──────────────────────────────────────────────────────────┘
         │          │
         │   ┌─ STEP 6: 执行下单 ──────────────────────────────────────┐
         │   │                                                         │
         │   │  price = OFFSET_MINUS_2_PRICE（offset=-2）             │
         │   │        | OFFSET_MINUS_1_PRICE（offset=-1）             │
         │   │  size  = OFFSET_MINUS_2_SIZE / OFFSET_MINUS_1_SIZE     │
         │   │                                                         │
         │   │  检查 USDC 余额 ≥ price × size                        │
         │   │    不足 → log.error("USDC余额不足") + 跳过             │
         │   │                                                         │
         │   │  wallet = wallet_manager.get_next_wallet()             │
         │   │  clob_wrapper.place_limit_buy_no(                      │
         │   │      token_id, price, size, FAK                        │
         │   │  )                                                      │
         │   │                                                         │
         │   │  成功 → INSERT trade_state（标记已触发）               │
         │   │          INSERT orders（记录订单详情）                  │
         │   │          log.info("下单成功 ✓")                        │
         │   │                                                         │
         │   │  失败 → log.error("下单失败")                          │
         │   │          不写 trade_state（下轮重试）                   │
         │   └──────────────────────────────────────────────────────────┘
         │
         └─► sleep(60)
```

---

## 七、特殊档口处理规则

| 档口标签 | 处理方式 |
|---|---|
| `"XX°C or higher"` | 完全跳过，不参与任何计算 |
| `"XX°C or below"` | `bracket_temp` 视为 `BRACKET_LOWER_TEMP`（默认 17） |
| 其他整数档口 | 正常解析 |

---

## 八、触发条件汇总

| 条件 | 档口温度 | 限价 | 数量 | 触发次数 |
|---|---|---|---|---|
| `bracket_temp == avg_temp - 2` | 均值 -2°C | 0.98 USDC | 200 份 | 每档口每天仅 1 次 |
| `bracket_temp == avg_temp - 1` | 均值 -1°C | 0.90 USDC | 200 份 | 每档口每天仅 1 次 |

---

## 九、数据质量校验（最终版）

```
读最近 2 条 obs
      ↓
[检查0] 是否有 2 条？              否 → warning + 跳过
      ↓ 是
[检查1] t2 距现在 ≤ 23 分钟？      否 → error + 跳过
      ↓ 是
[检查2] t1 - t2 > 9 分钟？         否 → error + 跳过
      ↓ 是
avg = floor((t1.temp + t2.temp) / 2)
      ↓
继续交易逻辑
```

---

## 十、文件结构

```
poly/
├── main.py              # 入口：解密钱包，启动两个后台线程
├── config.py            # 所有配置参数
├── cities.py            # 城市定义（ICAO、slug、timezone）
├── database.py          # SQLite 操作封装
├── obs_poller.py        # WU 数据拉取线程
├── trader.py            # 交易逻辑线程
├── gamma_client.py      # Gamma API（档口查询）
├── clob_wrapper.py      # Polymarket CLOB 下单封装（FAK）
├── wallet_manager.py    # 加密钱包加载与轮换
├── crypto_utils.py      # AES-256-CBC 解密
├── wallets.key          # 加密私钥文件（用户提供）
├── .env                 # 环境变量（用户配置）
├── .env.example         # 环境变量模板
├── requirements.txt     # Python 依赖
├── poly.db              # SQLite 数据库（运行时生成）
├── logs/                # 日志目录（运行时生成）
└── REQUIREMENTS.md      # 本文档
```

---

## 十一、wallets.key 文件格式

```
# 每行格式：<POLY_FUNDER地址> <AES加密私钥密文>
# 空行和 # 开头的行会被跳过

0xAbCd...1234 <base64加密私钥>
0xEfGh...5678 <base64加密私钥>
```

---

## 十二、启动流程

```
python main.py
  │
  ├─► 交互式输入解密密码（或从环境变量 POLY_MASTER_PASSWORD 读取）
  ├─► 解密 wallets.key，加载钱包
  ├─► 初始化 SQLite 数据库
  ├─► 启动 obs_poller 后台线程（WU 数据拉取）
  └─► 启动 trader 后台线程（交易逻辑）
        │
        └─► 主线程 sleep 等待（Ctrl+C 优雅退出）
```

---

*文档版本：1.0 | 最后更新：2026-04-08*
