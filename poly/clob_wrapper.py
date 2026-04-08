"""
Polymarket CLOB 交易封装。
仅包含本项目所需的接口：FAK 限价买入 NO、USDC 余额查询。
"""
import json
import math
import logging
from typing import Dict, Any

import requests

from config import CLOB_HOST, POLYGON_CHAIN_ID, SIGNATURE_TYPE

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "PolyTempBot/1.0"})


def _get_clob_client(private_key: str, funder: str):
    if not private_key:
        raise RuntimeError("private_key 为空，无法执行交易")
    if not funder:
        raise RuntimeError("funder 为空，请检查 wallets.key")

    try:
        from py_clob_client.client import ClobClient
    except ImportError:
        raise RuntimeError("py-clob-client 未安装，请运行: pip install py-clob-client")

    temp_client = ClobClient(host=CLOB_HOST, key=private_key, chain_id=POLYGON_CHAIN_ID)
    creds = temp_client.create_or_derive_api_creds()

    client = ClobClient(
        host=CLOB_HOST,
        key=private_key,
        chain_id=POLYGON_CHAIN_ID,
        creds=creds,
        signature_type=SIGNATURE_TYPE,
        funder=funder,
    )
    return client


def get_usdc_balance(private_key: str, funder: str) -> float:
    """
    查询钱包的 USDC 余额（链上）。
    失败时抛出异常。
    """
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
    except ImportError:
        raise RuntimeError("py-clob-client 未安装")

    client = _get_clob_client(private_key, funder)
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    result = client.get_balance_allowance(params)
    result_dict = result if isinstance(result, dict) else vars(result)
    raw = result_dict.get("balance", "0") or "0"
    try:
        return float(raw) / 1_000_000
    except (ValueError, TypeError):
        return 0.0


def place_limit_buy_no(
    no_token_id: str,
    limit_price: float,
    size: float,
    private_key: str,
    funder: str,
) -> Dict[str, Any]:
    """
    对 No token 下 FAK（Fill-And-Kill）限价买单。

    FAK 行为：立即吃掉订单簿上所有 ≤limit_price 的卖单，剩余未成交部分自动取消。
    返回 {"success": True, "order_id": "...", "shares_filled": float, "status": str, "raw": "..."}
    """
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY
    except ImportError:
        raise RuntimeError("py-clob-client 未安装")

    size_int = math.floor(size)
    if size_int < 1:
        raise ValueError(f"买入份额不足1（size={size:.4f}），跳过下单")

    client = _get_clob_client(private_key, funder)

    order_args = OrderArgs(
        token_id=no_token_id,
        price=round(limit_price, 4),
        size=float(size_int),
        side=BUY,
    )
    signed_order = client.create_order(order_args)
    resp = client.post_order(signed_order, OrderType.FAK)

    resp_dict = resp if isinstance(resp, dict) else vars(resp)
    order_id  = (
        resp_dict.get("orderID")
        or resp_dict.get("order_id")
        or resp_dict.get("id")
        or ""
    )

    taking_raw = resp_dict.get("takingAmount", "0") or "0"
    try:
        shares_filled = float(taking_raw)
    except (ValueError, TypeError):
        shares_filled = 0.0

    status = resp_dict.get("status", "unknown")

    logger.info(
        "下单成功: token=%s... size=%s price=%s order_id=%s status=%s filled=%s",
        no_token_id[:16], size_int, limit_price, order_id, status, shares_filled,
    )

    return {
        "success":       True,
        "order_id":      str(order_id),
        "shares_filled": shares_filled,
        "status":        status,
        "raw":           json.dumps(resp_dict, default=str),
    }
