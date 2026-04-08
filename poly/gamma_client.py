"""
Polymarket Gamma API 封装。
用于通过事件 slug 获取温度档口列表及各档口的 No token ID。
"""
import json
import re
import logging
from typing import List, Dict, Any, Optional

import requests

from config import GAMMA_API_BASE

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "PolyTempBot/1.0"})

# 从 question 中提取温度整数，如 "... be 20°C?" → 20，"-5°C" → -5
_TEMP_RE = re.compile(r"(-?\d+)\s*°[CF]")


def get_event_markets(event_slug: str) -> List[Dict[str, Any]]:
    """
    通过事件 slug 获取所有温度档位市场。
    返回市场列表，每项包含 question / outcomePrices / clobTokenIds 等字段。
    """
    try:
        resp = _SESSION.get(
            f"{GAMMA_API_BASE}/events",
            params={"slug": event_slug, "limit": 1},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data:
            logger.debug("事件不存在或未开放: %s", event_slug)
            return []

        event   = data[0] if isinstance(data, list) else data
        markets = event.get("markets", [])
        logger.debug("事件 %s 共 %d 个档位", event_slug, len(markets))
        return markets

    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        if code == 404:
            logger.debug("事件未找到: %s", event_slug)
        else:
            logger.warning("Gamma API 请求失败 [%s] HTTP %s: %s", event_slug, code, e)
        return []
    except Exception as e:
        logger.warning("Gamma API 异常 [%s]: %s", event_slug, e)
        return []


def parse_market_no_info(market: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    从市场数据中解析 No 侧信息。
    返回 {question, no_token_id, no_price, active}，失败返回 None。
    """
    try:
        question = market.get("question", "")
        if not question:
            return None

        active = market.get("active", True) and not market.get("closed", False)

        raw_outcomes  = market.get("outcomes", [])
        raw_prices    = market.get("outcomePrices", [])
        raw_token_ids = market.get("clobTokenIds", [])

        if isinstance(raw_outcomes,  str): raw_outcomes  = json.loads(raw_outcomes)
        if isinstance(raw_prices,    str): raw_prices    = json.loads(raw_prices)
        if isinstance(raw_token_ids, str): raw_token_ids = json.loads(raw_token_ids)

        if not raw_outcomes or not raw_token_ids:
            return None

        no_price    = None
        no_token_id = None

        for i, outcome in enumerate(raw_outcomes):
            if outcome.lower() == "no":
                no_price    = float(raw_prices[i]) if i < len(raw_prices) else 0.0
                no_token_id = raw_token_ids[i] if i < len(raw_token_ids) else ""

        if not no_token_id:
            return None

        return {
            "question":    question,
            "no_token_id": no_token_id,
            "no_price":    no_price,
            "active":      active,
        }

    except Exception as e:
        logger.warning("解析市场数据失败: %s — %s", market.get("question"), e)
        return None


def parse_bracket_temp(question: str, skip_keyword: str, lower_keyword: str, lower_temp: int):
    """
    从市场 question 中解析档口温度。

    返回 (temp: int | None, skip: bool)：
    - skip=True        → 完全跳过此档口（"or higher"）
    - temp=lower_temp  → "or below" 档口，视为 lower_temp
    - temp=整数        → 正常档口
    - temp=None        → 解析失败，跳过
    """
    q_lower = question.lower()

    if skip_keyword.lower() in q_lower:
        return None, True

    if lower_keyword.lower() in q_lower:
        return lower_temp, False

    m = _TEMP_RE.search(question)
    if not m:
        logger.debug("无法解析档口温度: %s", question)
        return None, True

    return int(m.group(1)), False
