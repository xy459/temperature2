from datetime import datetime
from zoneinfo import ZoneInfo

# 摄氏度城市；web_obs 多渠道：WU 历史 METAR、NOAA、IEM、WeatherAPI、AVWX（各渠道按 ICAO）
# Moscow / Tel Aviv / Istanbul：已纳入；香港（VHHH）暂不纳入
# 排除：所有美国城市（WU 华氏度）
CITIES = [
    {"name": "Madrid",        "name_cn": "马德里",          "icao": "LEMD", "country": "ES", "slug": "madrid",        "timezone": "Europe/Madrid"},
    {"name": "London",        "name_cn": "伦敦",            "icao": "EGLC", "country": "GB", "slug": "london",        "timezone": "Europe/London"},
    {"name": "Paris",         "name_cn": "巴黎",            "icao": "LFPG", "country": "FR", "slug": "paris",         "timezone": "Europe/Paris"},
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
    {"name": "Taipei",        "name_cn": "台北",            "icao": "RCSS", "country": "TW", "slug": "taipei",        "timezone": "Asia/Taipei"},
    {"name": "Singapore",     "name_cn": "新加坡",          "icao": "WSSS", "country": "SG", "slug": "singapore",     "timezone": "Asia/Singapore"},
    {"name": "Kuala Lumpur",  "name_cn": "吉隆坡",          "icao": "WMKK", "country": "MY", "slug": "kuala-lumpur",  "timezone": "Asia/Kuala_Lumpur"},
    {"name": "Jakarta",       "name_cn": "雅加达",          "icao": "WIHH", "country": "ID", "slug": "jakarta",       "timezone": "Asia/Jakarta"},
    {"name": "Busan",         "name_cn": "釜山",            "icao": "RKPK", "country": "KR", "slug": "busan",         "timezone": "Asia/Seoul"},
    {"name": "Lucknow",       "name_cn": "勒克瑙",          "icao": "VILK", "country": "IN", "slug": "lucknow",       "timezone": "Asia/Kolkata"},
    {"name": "Wellington",    "name_cn": "惠灵顿",          "icao": "NZWN", "country": "NZ", "slug": "wellington",    "timezone": "Pacific/Auckland"},
    {"name": "Toronto",       "name_cn": "多伦多",          "icao": "CYYZ", "country": "CA", "slug": "toronto",       "timezone": "America/Toronto"},
    {"name": "Buenos Aires",  "name_cn": "布宜诺斯艾利斯",   "icao": "SAEZ", "country": "AR", "slug": "buenos-aires",  "timezone": "America/Argentina/Buenos_Aires"},
    {"name": "Sao Paulo",     "name_cn": "圣保罗",          "icao": "SBGR", "country": "BR", "slug": "sao-paulo",     "timezone": "America/Sao_Paulo"},
    {"name": "Mexico City",   "name_cn": "墨西哥城",        "icao": "MMMX", "country": "MX", "slug": "mexico-city",   "timezone": "America/Mexico_City"},
    {"name": "Panama City",   "name_cn": "巴拿马城",        "icao": "MPMG", "country": "PA", "slug": "panama-city",   "timezone": "America/Panama"},
    {"name": "Moscow",        "name_cn": "莫斯科",          "icao": "UUWW", "country": "RU", "slug": "moscow",        "timezone": "Europe/Moscow"},
    {"name": "Tel Aviv",      "name_cn": "特拉维夫",        "icao": "LLBG", "country": "IL", "slug": "tel-aviv",      "timezone": "Asia/Jerusalem"},
    {"name": "Istanbul",      "name_cn": "伊斯坦布尔",      "icao": "LTFM", "country": "TR", "slug": "istanbul",      "timezone": "Europe/Istanbul"},
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
