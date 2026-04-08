from datetime import datetime
from zoneinfo import ZoneInfo

# 仅包含 WU 数据源 + 摄氏度 的城市
# 排除：Moscow / Tel Aviv / Istanbul / Hong Kong（非WU）
# 排除：所有美国城市（华氏度）
CITIES = [
    {"name": "Madrid",        "name_cn": "马德里",          "icao": "LEMD", "country": "ES", "slug": "madrid",        "timezone": "Europe/Madrid"},
    {"name": "London",        "name_cn": "伦敦",            "icao": "EGLC", "country": "GB", "slug": "london",        "timezone": "Europe/London"},
    {"name": "Paris",         "name_cn": "巴黎",            "icao": "LFPO", "country": "FR", "slug": "paris",         "timezone": "Europe/Paris"},
    {"name": "Munich",        "name_cn": "慕尼黑",          "icao": "EDDM", "country": "DE", "slug": "munich",        "timezone": "Europe/Berlin"},
    {"name": "Milan",         "name_cn": "米兰",            "icao": "LIML", "country": "IT", "slug": "milan",         "timezone": "Europe/Rome"},
    {"name": "Warsaw",        "name_cn": "华沙",            "icao": "EPWA", "country": "PL", "slug": "warsaw",        "timezone": "Europe/Warsaw"},
    {"name": "Ankara",        "name_cn": "安卡拉",          "icao": "LTAC", "country": "TR", "slug": "ankara",        "timezone": "Europe/Istanbul"},
    {"name": "Tokyo",         "name_cn": "东京",            "icao": "RJTT", "country": "JP", "slug": "tokyo",         "timezone": "Asia/Tokyo"},
    {"name": "Seoul",         "name_cn": "首尔",            "icao": "RKSI", "country": "KR", "slug": "seoul",         "timezone": "Asia/Seoul"},
    {"name": "Shanghai",      "name_cn": "上海",            "icao": "ZSPD", "country": "CN", "slug": "shanghai",      "timezone": "Asia/Shanghai"},
    {"name": "Beijing",       "name_cn": "北京",            "icao": "ZBAA", "country": "CN", "slug": "beijing",       "timezone": "Asia/Shanghai"},
    {"name": "Chongqing",     "name_cn": "重庆",            "icao": "ZUCK", "country": "CN", "slug": "chongqing",     "timezone": "Asia/Shanghai"},
    {"name": "Wuhan",         "name_cn": "武汉",            "icao": "ZHHH", "country": "CN", "slug": "wuhan",         "timezone": "Asia/Shanghai"},
    {"name": "Chengdu",       "name_cn": "成都",            "icao": "ZUUU", "country": "CN", "slug": "chengdu",       "timezone": "Asia/Shanghai"},
    {"name": "Taipei",        "name_cn": "台北",            "icao": "RCTP", "country": "TW", "slug": "taipei",        "timezone": "Asia/Taipei"},
    {"name": "Singapore",     "name_cn": "新加坡",          "icao": "WSSS", "country": "SG", "slug": "singapore",     "timezone": "Asia/Singapore"},
    {"name": "Lucknow",       "name_cn": "勒克瑙",          "icao": "VILK", "country": "IN", "slug": "lucknow",       "timezone": "Asia/Kolkata"},
    {"name": "Wellington",    "name_cn": "惠灵顿",          "icao": "NZWN", "country": "NZ", "slug": "wellington",    "timezone": "Pacific/Auckland"},
    {"name": "Toronto",       "name_cn": "多伦多",          "icao": "CYYZ", "country": "CA", "slug": "toronto",       "timezone": "America/Toronto"},
    {"name": "Buenos Aires",  "name_cn": "布宜诺斯艾利斯",   "icao": "SAEZ", "country": "AR", "slug": "buenos-aires",  "timezone": "America/Argentina/Buenos_Aires"},
    {"name": "Sao Paulo",     "name_cn": "圣保罗",          "icao": "SBGR", "country": "BR", "slug": "sao-paulo",     "timezone": "America/Sao_Paulo"},
    {"name": "Mexico City",   "name_cn": "墨西哥城",        "icao": "MMMX", "country": "MX", "slug": "mexico-city",   "timezone": "America/Mexico_City"},
    {"name": "Panama City",   "name_cn": "巴拿马城",        "icao": "MPMG", "country": "PA", "slug": "panama-city",   "timezone": "America/Panama"},
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
