import os
from dotenv import load_dotenv

load_dotenv()

# ── WU API ──────────────────────────────────────────────────────────
WU_API_KEY               = os.getenv("WU_API_KEY", "")
WU_POLL_INTERVAL_SECONDS = int(os.getenv("WU_POLL_INTERVAL_SECONDS", "60"))
WU_API_BASE              = "https://api.weather.com/v3/wx/observations/current"

# ── obs 数据质量（严格模式）──────────────────────────────────────────
# 较老那条 obs 距现在最大允许时间（分钟）
OBS_MAX_SECOND_AGE_MINUTES = int(os.getenv("OBS_MAX_SECOND_AGE_MINUTES", "23"))
# 两条 obs 之间必须超过此时间间隔（分钟）
OBS_MIN_GAP_MINUTES        = int(os.getenv("OBS_MIN_GAP_MINUTES", "9"))

# ── 交易参数 ─────────────────────────────────────────────────────────
OFFSET_MINUS_2_PRICE = float(os.getenv("OFFSET_MINUS_2_PRICE", "0.98"))
OFFSET_MINUS_2_SIZE  = float(os.getenv("OFFSET_MINUS_2_SIZE",  "200"))
OFFSET_MINUS_1_PRICE = float(os.getenv("OFFSET_MINUS_1_PRICE", "0.90"))
OFFSET_MINUS_1_SIZE  = float(os.getenv("OFFSET_MINUS_1_SIZE",  "200"))

# ── 特殊档口规则 ─────────────────────────────────────────────────────
BRACKET_SKIP_KEYWORD  = "or higher"   # 包含此词的档口完全跳过
BRACKET_LOWER_KEYWORD = "or below"    # 包含此词的档口视为以下温度
BRACKET_LOWER_TEMP    = 17

# ── 交易线程检查间隔 ─────────────────────────────────────────────────
TRADE_CHECK_INTERVAL_SECONDS = int(os.getenv("TRADE_CHECK_INTERVAL_SECONDS", "60"))

# ── Polymarket CLOB ──────────────────────────────────────────────────
CLOB_HOST        = "https://clob.polymarket.com"
POLYGON_CHAIN_ID = 137
# 2=GNOSIS_SAFE（MetaMask/Rabby）, 1=POLY_PROXY（邮箱登录）, 0=EOA
SIGNATURE_TYPE   = int(os.getenv("SIGNATURE_TYPE", "2"))

# ── Gamma API ────────────────────────────────────────────────────────
GAMMA_API_BASE = "https://gamma-api.polymarket.com"

# ── 多渠道气象 API ───────────────────────────────────────────────────
WEATHERAPI_KEY = os.getenv("WEATHERAPI_KEY", "")
AVWX_TOKEN     = os.getenv("AVWX_TOKEN", "")
