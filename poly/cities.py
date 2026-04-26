from datetime import datetime
from zoneinfo import ZoneInfo

# web_obs 多渠道：WU 历史 METAR、NOAA、WeatherAPI、AVWX（各渠道按 ICAO）
# 可选：wu_v1=False 关闭 WU V1；avwx=False 关闭 AVWX（如俄罗斯/白俄罗斯机场被 API 屏蔽）
# fahrenheit=True：美国城市 **SQLite 中温度存华氏度**（WU 直接存 °F；NOAA/METAR 报文为摄氏，入库前转 °F）；折线图同显 °F
# Polymarket：美国城 ICAO 与 `city_exclusions.md` 第六节对拍（以事件页主规则 WU 末四码为准；换日请重查）
# Moscow / Tel Aviv / Istanbul：已纳入；香港（VHHH）暂不纳入
# 2026-04 起：广州/拉各斯/马尼拉/卡拉奇/开普敦/吉达（ICAO 与 Polymarket 温度盘口规则内 Wunderground 链接一致，见 city_exclusions.md）
CITIES = [
    {"name": "Madrid",        "name_cn": "马德里",          "icao": "LEMD", "country": "ES", "slug": "madrid",        "timezone": "Europe/Madrid"},
    {"name": "London",        "name_cn": "伦敦",            "icao": "EGLC", "country": "GB", "slug": "london",        "timezone": "Europe/London"},
    {"name": "Paris",         "name_cn": "巴黎",            "icao": "LFPB", "country": "FR", "slug": "paris",         "timezone": "Europe/Paris"},
    {"name": "Munich",        "name_cn": "慕尼黑",          "icao": "EDDM", "country": "DE", "slug": "munich",        "timezone": "Europe/Berlin"},
    {"name": "Milan",         "name_cn": "米兰",            "icao": "LIMC", "country": "IT", "slug": "milan",         "timezone": "Europe/Rome"},
    {"name": "Warsaw",        "name_cn": "华沙",            "icao": "EPWA", "country": "PL", "slug": "warsaw",        "timezone": "Europe/Warsaw"},
    {"name": "Helsinki",      "name_cn": "赫尔辛基",        "icao": "EFHK", "country": "FI", "slug": "helsinki",      "timezone": "Europe/Helsinki"},
    {"name": "Amsterdam",     "name_cn": "阿姆斯特丹",      "icao": "EHAM", "country": "NL", "slug": "amsterdam",     "timezone": "Europe/Amsterdam"},
    {"name": "Ankara",        "name_cn": "安卡拉",          "icao": "LTAC", "country": "TR", "slug": "ankara",        "timezone": "Europe/Istanbul"},
    {"name": "Tokyo",         "name_cn": "东京",            "icao": "RJTT", "country": "JP", "slug": "tokyo",         "timezone": "Asia/Tokyo"},
    {"name": "Seoul",         "name_cn": "首尔",            "icao": "RKSI", "country": "KR", "slug": "seoul",         "timezone": "Asia/Seoul"},
    {"name": "Shanghai",      "name_cn": "上海",            "icao": "ZSPD", "country": "CN", "slug": "shanghai",      "timezone": "Asia/Shanghai"},
    {"name": "Beijing",       "name_cn": "北京",            "icao": "ZBAA", "country": "CN", "slug": "beijing",       "timezone": "Asia/Shanghai"},
    {"name": "Chongqing",     "name_cn": "重庆",            "icao": "ZUCK", "country": "CN", "slug": "chongqing",     "timezone": "Asia/Shanghai"},
    {"name": "Wuhan",         "name_cn": "武汉",            "icao": "ZHHH", "country": "CN", "slug": "wuhan",         "timezone": "Asia/Shanghai"},
    {"name": "Chengdu",       "name_cn": "成都",            "icao": "ZUUU", "country": "CN", "slug": "chengdu",       "timezone": "Asia/Shanghai"},
    {"name": "Shenzhen",      "name_cn": "深圳",            "icao": "ZGSZ", "country": "CN", "slug": "shenzhen",      "timezone": "Asia/Shanghai"},
    {"name": "Guangzhou",     "name_cn": "广州",            "icao": "ZGGG", "country": "CN", "slug": "guangzhou",     "timezone": "Asia/Shanghai"},
    {"name": "Taipei",        "name_cn": "台北",            "icao": "RCSS", "country": "TW", "slug": "taipei",        "timezone": "Asia/Taipei"},
    {"name": "Singapore",     "name_cn": "新加坡",          "icao": "WSSS", "country": "SG", "slug": "singapore",     "timezone": "Asia/Singapore"},
    {"name": "Kuala Lumpur",  "name_cn": "吉隆坡",          "icao": "WMKK", "country": "MY", "slug": "kuala-lumpur",  "timezone": "Asia/Kuala_Lumpur"},
    {"name": "Jakarta",       "name_cn": "雅加达",          "icao": "WIHH", "country": "ID", "slug": "jakarta",       "timezone": "Asia/Jakarta"},
    {"name": "Manila",        "name_cn": "马尼拉",          "icao": "RPLL", "country": "PH", "slug": "manila",        "timezone": "Asia/Manila"},
    {"name": "Busan",         "name_cn": "釜山",            "icao": "RKPK", "country": "KR", "slug": "busan",         "timezone": "Asia/Seoul"},
    {"name": "Lucknow",       "name_cn": "勒克瑙",          "icao": "VILK", "country": "IN", "slug": "lucknow",       "timezone": "Asia/Kolkata"},
    {"name": "Karachi",       "name_cn": "卡拉奇",          "icao": "OPKC", "country": "PK", "slug": "karachi",       "timezone": "Asia/Karachi"},
    {"name": "Wellington",    "name_cn": "惠灵顿",          "icao": "NZWN", "country": "NZ", "slug": "wellington",    "timezone": "Pacific/Auckland"},
    {"name": "Toronto",       "name_cn": "多伦多",          "icao": "CYYZ", "country": "CA", "slug": "toronto",       "timezone": "America/Toronto"},
    {"name": "Buenos Aires",  "name_cn": "布宜诺斯艾利斯",   "icao": "SAEZ", "country": "AR", "slug": "buenos-aires",  "timezone": "America/Argentina/Buenos_Aires"},
    {"name": "Cape Town",     "name_cn": "开普敦",          "icao": "FACT", "country": "ZA", "slug": "cape-town",     "timezone": "Africa/Johannesburg"},
    {"name": "Sao Paulo",     "name_cn": "圣保罗",          "icao": "SBGR", "country": "BR", "slug": "sao-paulo",     "timezone": "America/Sao_Paulo"},
    {"name": "Mexico City",   "name_cn": "墨西哥城",        "icao": "MMMX", "country": "MX", "slug": "mexico-city",   "timezone": "America/Mexico_City"},
    {"name": "Panama City",   "name_cn": "巴拿马城",        "icao": "MPMG", "country": "PA", "slug": "panama-city",   "timezone": "America/Panama"},
    {"name": "Lagos",         "name_cn": "拉各斯",          "icao": "DNMM", "country": "NG", "slug": "lagos",         "timezone": "Africa/Lagos"},
    {"name": "Moscow",        "name_cn": "莫斯科",          "icao": "UUWW", "country": "RU", "slug": "moscow",        "timezone": "Europe/Moscow", "avwx": False},
    {"name": "Tel Aviv",      "name_cn": "特拉维夫",        "icao": "LLBG", "country": "IL", "slug": "tel-aviv",      "timezone": "Asia/Jerusalem"},
    {"name": "Jeddah",        "name_cn": "吉达",            "icao": "OEJN", "country": "SA", "slug": "jeddah",        "timezone": "Asia/Riyadh"},
    {"name": "Istanbul",      "name_cn": "伊斯坦布尔",      "icao": "LTFM", "country": "TR", "slug": "istanbul",      "timezone": "Europe/Istanbul", "wu_v1": False},
    # 美国 fahrenheit，库内 °F；见 city_exclusions.md
    {"name": "Denver",         "name_cn": "丹佛",            "icao": "KBKF", "country": "US", "slug": "denver",         "timezone": "America/Denver",    "fahrenheit": True},
    {"name": "Chicago",        "name_cn": "芝加哥",          "icao": "KORD", "country": "US", "slug": "chicago",        "timezone": "America/Chicago",  "fahrenheit": True},
    {"name": "New York City",  "name_cn": "纽约",            "icao": "KJFK", "country": "US", "slug": "new-york-city", "timezone": "America/New_York", "fahrenheit": True},
    {"name": "Dallas",         "name_cn": "达拉斯",          "icao": "KDAL", "country": "US", "slug": "dallas",         "timezone": "America/Chicago",  "fahrenheit": True},
    {"name": "Austin",         "name_cn": "奥斯汀",          "icao": "KAUS", "country": "US", "slug": "austin",         "timezone": "America/Chicago",  "fahrenheit": True},
    {"name": "Los Angeles",    "name_cn": "洛杉矶",          "icao": "KLAX", "country": "US", "slug": "los-angeles",    "timezone": "America/Los_Angeles", "fahrenheit": True},
    {"name": "Miami",          "name_cn": "迈阿密",          "icao": "KMIA", "country": "US", "slug": "miami",          "timezone": "America/New_York", "fahrenheit": True},
    {"name": "Atlanta",        "name_cn": "亚特兰大",        "icao": "KATL", "country": "US", "slug": "atlanta",        "timezone": "America/New_York", "fahrenheit": True},
    {"name": "Seattle",        "name_cn": "西雅图",          "icao": "KSEA", "country": "US", "slug": "seattle",        "timezone": "America/Los_Angeles", "fahrenheit": True},
    {"name": "Houston",        "name_cn": "休斯顿",          "icao": "KHOU", "country": "US", "slug": "houston",        "timezone": "America/Chicago",  "fahrenheit": True},
    {"name": "San Francisco",  "name_cn": "旧金山",          "icao": "KSFO", "country": "US", "slug": "san-francisco",  "timezone": "America/Los_Angeles", "fahrenheit": True},
]


def get_today_event_slug(city: dict) -> str:
    """生成该城市当地今日事件 slug：highest-temperature-in-{slug}-on-{month}-{day}-{year}"""
    today = datetime.now(ZoneInfo(city["timezone"]))
    month = today.strftime("%B").lower()
    day   = today.day
    year  = today.year
    return f"highest-temperature-in-{city['slug']}-on-{month}-{day}-{year}"


def get_today_local_date(city: dict) -> str:
    """返回城市本地今日日期字符串，格式 YYYY-MM-DD"""
    today = datetime.now(ZoneInfo(city["timezone"]))
    return today.strftime("%Y-%m-%d")
