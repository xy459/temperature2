"""
交易逻辑线程。
每 60 秒遍历所有城市，检查 obs 数据质量，计算均温，
对满足条件的 Polymarket 档口执行 FAK 限价买 NO 单。
"""
import logging
import math
import threading
import time
from datetime import datetime, timezone

import database as db
import clob_wrapper
import gamma_client
from cities import CITIES, get_today_event_slug, get_today_local_date
from config import (
    BRACKET_LOWER_KEYWORD,
    BRACKET_LOWER_TEMP,
    BRACKET_SKIP_KEYWORD,
    OFFSET_MINUS_1_PRICE,
    OFFSET_MINUS_1_SIZE,
    OFFSET_MINUS_2_PRICE,
    OFFSET_MINUS_2_SIZE,
    OBS_MAX_SECOND_AGE_MINUTES,
    OBS_MIN_GAP_MINUTES,
    TRADE_CHECK_INTERVAL_SECONDS,
)
from wallet_manager import wallet_manager

logger = logging.getLogger(__name__)

# offset → (price, size)
_OFFSET_PARAMS = {
    -2: (OFFSET_MINUS_2_PRICE, OFFSET_MINUS_2_SIZE),
    -1: (OFFSET_MINUS_1_PRICE, OFFSET_MINUS_1_SIZE),
}


def _parse_obs_time(obs_time_str: str) -> datetime:
    """将 'YYYY-MM-DD HH:MM:SS' 字符串解析为 UTC aware datetime。"""
    dt = datetime.strptime(obs_time_str, "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=timezone.utc)


def _check_obs_quality(obs: list, city_name: str) -> bool:
    """
    严格校验两条 obs 数据质量。
    规则一：较老那条（obs[1]）距现在 ≤ OBS_MAX_SECOND_AGE_MINUTES 分钟
    规则二：两条时间间隔 > OBS_MIN_GAP_MINUTES 分钟
    返回 True 表示通过，False 表示不符合。
    """
    now = datetime.now(timezone.utc)

    t1 = _parse_obs_time(obs[0]["obs_time"])
    t2 = _parse_obs_time(obs[1]["obs_time"])

    t2_age_minutes = (now - t2).total_seconds() / 60
    if t2_age_minutes > OBS_MAX_SECOND_AGE_MINUTES:
        logger.error(
            "[trader] %s 数据过期：较老 obs 已 %.1f 分钟前（阈值 %d 分钟），跳过本轮",
            city_name, t2_age_minutes, OBS_MAX_SECOND_AGE_MINUTES,
        )
        return False

    gap_minutes = (t1 - t2).total_seconds() / 60
    if gap_minutes <= OBS_MIN_GAP_MINUTES:
        logger.error(
            "[trader] %s 数据间隔不足：两条 obs 仅相差 %.1f 分钟（需 > %d 分钟），跳过本轮",
            city_name, gap_minutes, OBS_MIN_GAP_MINUTES,
        )
        return False

    return True


def _process_city(city: dict) -> None:
    icao      = city["icao"]
    name      = city["name"]
    name_cn   = city["name_cn"]

    # ── STEP 1: 读取最近 2 条 obs ────────────────────────────────────
    obs = db.get_latest_observations(icao, limit=2)
    if len(obs) < 2:
        logger.warning("[trader] %s (%s) obs 不足 2 条，跳过", name, icao)
        return

    # ── STEP 2: 数据质量校验 ─────────────────────────────────────────
    if not _check_obs_quality(obs, name):
        return

    # ── STEP 3: 计算均温（向下取整）──────────────────────────────────
    t1_temp = obs[0]["temperature"]
    t2_temp = obs[1]["temperature"]
    avg_temp = math.floor((t1_temp + t2_temp) / 2)
    logger.debug("[trader] %s avg=floor((%s+%s)/2)=%d°C", name, t1_temp, t2_temp, avg_temp)

    # ── STEP 4: 获取当日市场档口 ──────────────────────────────────────
    event_date = get_today_local_date(city)
    event_slug = get_today_event_slug(city)
    markets    = gamma_client.get_event_markets(event_slug)

    if not markets:
        logger.warning("[trader] %s 未找到当日市场: %s", name, event_slug)
        return

    # ── STEP 5 & 6: 遍历档口，判断并执行 ─────────────────────────────
    for market in markets:
        info = gamma_client.parse_market_no_info(market)
        if info is None:
            continue

        if not info["active"]:
            logger.debug("[trader] %s 档口已关闭: %s", name, info["question"])
            continue

        bracket_temp, skip = gamma_client.parse_bracket_temp(
            info["question"],
            BRACKET_SKIP_KEYWORD,
            BRACKET_LOWER_KEYWORD,
            BRACKET_LOWER_TEMP,
        )
        if skip:
            logger.debug("[trader] %s 跳过档口: %s", name, info["question"])
            continue

        no_token_id = info["no_token_id"]

        for offset, (price, size) in _OFFSET_PARAMS.items():
            target_temp = avg_temp + offset
            if bracket_temp != target_temp:
                continue

            # 检查是否已触发
            if db.is_triggered(icao, event_date, bracket_temp, offset):
                logger.debug(
                    "[trader] %s bracket=%d offset=%d 已触发，跳过",
                    name, bracket_temp, offset,
                )
                continue

            logger.info(
                "[trader] %s 触发条件：avg=%d offset=%d → bracket=%d  price=%.2f size=%.0f",
                name, avg_temp, offset, bracket_temp, price, size,
            )

            _execute_order(
                city       = city,
                event_date = event_date,
                bracket_temp = bracket_temp,
                offset     = offset,
                no_token_id = no_token_id,
                price      = price,
                size       = size,
            )


def _execute_order(
    city: dict,
    event_date: str,
    bracket_temp: int,
    offset: int,
    no_token_id: str,
    price: float,
    size: float,
) -> None:
    icao    = city["icao"]
    name    = city["name"]
    wallet  = wallet_manager.get_next_wallet()

    # 检查 USDC 余额
    try:
        balance = clob_wrapper.get_usdc_balance(wallet.private_key, wallet.funder)
        required = price * size
        if balance < required:
            logger.error(
                "[trader] %s USDC 余额不足：需要 %.2f，当前 %.2f（%s），跳过",
                name, required, balance, wallet.funder_display,
            )
            return
    except Exception as e:
        logger.error("[trader] %s 查询 USDC 余额失败 (%s): %s，跳过", name, wallet.funder_display, e)
        return

    # 执行 FAK 买单
    try:
        result = clob_wrapper.place_limit_buy_no(
            no_token_id = no_token_id,
            limit_price = price,
            size        = size,
            private_key = wallet.private_key,
            funder      = wallet.funder,
        )
    except Exception as e:
        logger.error(
            "[trader] %s bracket=%d offset=%d 下单失败 (%s): %s",
            name, bracket_temp, offset, wallet.funder_display, e,
        )
        db.insert_order(
            city_icao    = icao,
            event_date   = event_date,
            bracket_temp = bracket_temp,
            offset       = offset,
            token_id     = no_token_id,
            price        = price,
            size         = size,
            order_id     = "",
            status       = "error",
            wallet       = wallet.funder_display,
            raw_response = str(e),
        )
        return

    # 下单成功：写 trade_state + orders
    db.mark_triggered(icao, event_date, bracket_temp, offset)
    db.insert_order(
        city_icao    = icao,
        event_date   = event_date,
        bracket_temp = bracket_temp,
        offset       = offset,
        token_id     = no_token_id,
        price        = price,
        size         = size,
        order_id     = result["order_id"],
        status       = result["status"],
        wallet       = wallet.funder_display,
        raw_response = result["raw"],
    )

    logger.info(
        "[trader] ✓ 下单完成 %s bracket=%d offset=%d  order_id=%s status=%s filled=%.0f",
        name, bracket_temp, offset,
        result["order_id"], result["status"], result["shares_filled"],
    )


def _trade_loop() -> None:
    """交易主循环，顺序处理所有城市。"""
    logger.info("[trader] 交易线程启动，共 %d 个城市，间隔 %ds",
                len(CITIES), TRADE_CHECK_INTERVAL_SECONDS)
    while True:
        start = time.monotonic()
        for city in CITIES:
            try:
                _process_city(city)
            except Exception as e:
                logger.exception("[trader] %s 处理异常: %s", city["name"], e)

        elapsed    = time.monotonic() - start
        sleep_time = max(0, TRADE_CHECK_INTERVAL_SECONDS - elapsed)
        logger.debug("[trader] 本轮完成，耗时 %.1fs，等待 %.1fs", elapsed, sleep_time)
        time.sleep(sleep_time)


def start() -> threading.Thread:
    """启动交易后台线程，返回线程对象。"""
    t = threading.Thread(target=_trade_loop, daemon=True, name="trader")
    t.start()
    return t
