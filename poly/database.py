import sqlite3
import os
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "poly.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            city_icao        TEXT    NOT NULL,
            obs_time         DATETIME NOT NULL,
            poll_time        DATETIME NOT NULL,
            temperature      REAL    NOT NULL,
            temp_max_since_7am REAL,
            UNIQUE(city_icao, obs_time)
        )
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_obs_city_time
        ON observations(city_icao, obs_time DESC)
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS trade_state (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            city_icao    TEXT    NOT NULL,
            event_date   DATE    NOT NULL,
            bracket_temp INTEGER NOT NULL,
            offset       INTEGER NOT NULL,
            triggered_at DATETIME NOT NULL,
            UNIQUE(city_icao, event_date, bracket_temp, offset)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            city_icao    TEXT    NOT NULL,
            event_date   DATE    NOT NULL,
            bracket_temp INTEGER NOT NULL,
            offset       INTEGER NOT NULL,
            token_id     TEXT    NOT NULL,
            price        REAL    NOT NULL,
            size         REAL    NOT NULL,
            order_id     TEXT,
            status       TEXT    NOT NULL,
            wallet       TEXT,
            raw_response TEXT,
            created_at   DATETIME NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS metar_observations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            city_icao    TEXT     NOT NULL,
            obs_time     DATETIME NOT NULL,
            temperature  REAL,
            fetched_at   DATETIME NOT NULL,
            UNIQUE(city_icao, obs_time)
        )
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_metar_city_time
        ON metar_observations(city_icao, obs_time DESC)
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    c.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        ("wallet_current_index", "0"),
    )

    conn.commit()
    conn.close()
    logger.info("数据库初始化完成：%s", DB_PATH)


# ── observations ─────────────────────────────────────────────────────

def insert_observation(
    city_icao: str,
    obs_time: str,
    poll_time: str,
    temperature: float,
    temp_max_since_7am: Optional[float],
) -> bool:
    """插入一条观测记录，若 (city_icao, obs_time) 已存在则跳过。返回是否为新数据。"""
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO observations
              (city_icao, obs_time, poll_time, temperature, temp_max_since_7am)
            VALUES (?, ?, ?, ?, ?)
            """,
            (city_icao, obs_time, poll_time, temperature, temp_max_since_7am),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def get_latest_observations(city_icao: str, limit: int = 2) -> List[Dict[str, Any]]:
    """返回指定城市最新的 N 条观测记录，按 obs_time 降序。"""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT obs_time, temperature, temp_max_since_7am, poll_time
        FROM observations
        WHERE city_icao = ?
        ORDER BY obs_time DESC
        LIMIT ?
        """,
        (city_icao, limit),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ── trade_state ──────────────────────────────────────────────────────

def is_triggered(
    city_icao: str,
    event_date: str,
    bracket_temp: int,
    offset: int,
) -> bool:
    """检查某档口某偏移是否已触发过下单。"""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT 1 FROM trade_state
        WHERE city_icao=? AND event_date=? AND bracket_temp=? AND offset=?
        """,
        (city_icao, event_date, bracket_temp, offset),
    )
    row = c.fetchone()
    conn.close()
    return row is not None


def mark_triggered(
    city_icao: str,
    event_date: str,
    bracket_temp: int,
    offset: int,
) -> bool:
    """标记档口已触发，唯一约束冲突时返回 False。"""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO trade_state
              (city_icao, event_date, bracket_temp, offset, triggered_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (city_icao, event_date, bracket_temp, offset, now),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


# ── orders ───────────────────────────────────────────────────────────

def insert_order(
    city_icao: str,
    event_date: str,
    bracket_temp: int,
    offset: int,
    token_id: str,
    price: float,
    size: float,
    order_id: str,
    status: str,
    wallet: str,
    raw_response: str,
) -> int:
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO orders
          (city_icao, event_date, bracket_temp, offset, token_id,
           price, size, order_id, status, wallet, raw_response, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            city_icao, event_date, bracket_temp, offset, token_id,
            price, size, order_id, status, wallet, raw_response, now,
        ),
    )
    conn.commit()
    row_id = c.lastrowid
    conn.close()
    return row_id


# ── settings ─────────────────────────────────────────────────────────

def get_setting(key: str, default: str = "") -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else default


def set_setting(key: str, value: str):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()
    conn.close()


# ── metar_observations ───────────────────────────────────────────────

def insert_metar_observations(city_icao: str, obs_list: list) -> int:
    """
    批量写入 METAR 观测记录，已存在的自动跳过（INSERT OR IGNORE）。
    obs_list 每项：{"obs_time": "YYYY-MM-DD HH:MM:SS", "temperature": float|None}
    返回实际新写入的条数。
    """
    if not obs_list:
        return 0
    fetched_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    before = conn.total_changes
    conn.executemany(
        """
        INSERT OR IGNORE INTO metar_observations
          (city_icao, obs_time, temperature, fetched_at)
        VALUES (?, ?, ?, ?)
        """,
        [
            (city_icao, o["obs_time"], o.get("temperature"), fetched_at)
            for o in obs_list
        ],
    )
    conn.commit()
    inserted = conn.total_changes - before
    conn.close()
    return inserted


def get_metar_observations(city_icao: str, date_str: str) -> List[Dict[str, Any]]:
    """返回指定城市指定日期的 METAR 记录，按 obs_time 降序。"""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT obs_time, temperature
        FROM metar_observations
        WHERE city_icao = ?
          AND date(obs_time) = ?
        ORDER BY obs_time DESC
        """,
        (city_icao, date_str),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def has_metar_data(city_icao: str, date_str: str) -> bool:
    """检查指定城市指定日期是否已有 METAR 数据。"""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT 1 FROM metar_observations WHERE city_icao=? AND date(obs_time)=? LIMIT 1",
        (city_icao, date_str),
    )
    row = c.fetchone()
    conn.close()
    return row is not None
