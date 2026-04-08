"""
程序入口。
1. 交互式输入解密密码（或从环境变量 POLY_MASTER_PASSWORD 读取）
2. 解密 wallets.key，加载多钱包
3. 初始化 SQLite 数据库
4. 启动 obs 拉取线程和交易线程
5. 主线程等待，Ctrl+C 优雅退出
"""
import getpass
import logging
import os
import signal
import sys
import time
from pathlib import Path

import database as db
import obs_poller
import trader
from crypto_utils import AESEncryption
from wallet_manager import wallet_manager, Wallet

WALLETS_KEY_FILE = Path(__file__).parent / "wallets.key"
LOG_DIR          = Path(__file__).parent / "logs"


def _setup_logging():
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8"),
        ],
    )


def _load_wallets(password: str) -> list:
    """
    读取 wallets.key，解密所有私钥，返回 Wallet 列表。

    文件格式（每行）：<POLY_FUNDER地址> <AES加密私钥密文>
    - 空行、# 开头行跳过
    - 重复密文跳过
    - 任意行解密失败则抛出异常
    """
    if not WALLETS_KEY_FILE.exists():
        raise FileNotFoundError(f"未找到 {WALLETS_KEY_FILE}，请先创建私钥配置文件")

    aes     = AESEncryption(password)
    wallets = []
    seen: set = set()

    with open(WALLETS_KEY_FILE, encoding="utf-8") as f:
        lines = f.readlines()

    for line_num, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split()
        if len(parts) < 2:
            raise ValueError(
                f"wallets.key 第 {line_num} 行格式错误\n"
                f"  期望：<POLY_FUNDER地址> <加密私钥密文>\n"
                f"  实际：{line[:60]}"
            )

        funder        = parts[0]
        encrypted_key = parts[1]

        if encrypted_key in seen:
            logging.getLogger(__name__).warning("wallets.key 第 %d 行密文重复，已跳过", line_num)
            continue
        seen.add(encrypted_key)

        try:
            private_key = aes.decrypt(encrypted_key)
        except ValueError:
            raise ValueError(f"wallets.key 第 {line_num} 行解密失败：密码错误或数据已损坏")

        wallets.append(Wallet(index=len(wallets) + 1, private_key=private_key, funder=funder))

    return wallets


def main():
    _setup_logging()
    logger = logging.getLogger(__name__)

    print("=" * 52)
    print("  Polymarket 温度事件自动交易机器人")
    print("=" * 52)

    # ── 步骤一：读取解密密码 ──────────────────────────────────────────
    password = os.environ.pop("POLY_MASTER_PASSWORD", None)
    if password:
        print("已从环境变量 POLY_MASTER_PASSWORD 读取解密密码")
    else:
        try:
            password = getpass.getpass("请输入钱包私钥解密密码：")
        except (KeyboardInterrupt, EOFError):
            print("\n已取消")
            sys.exit(0)

    if not password:
        print("错误：密码不能为空")
        sys.exit(1)

    # ── 步骤二：初始化数据库（先于钱包加载，以便恢复轮询索引）────────
    db.init_db()

    # ── 步骤三：加载并解密钱包 ───────────────────────────────────────
    print("正在解密钱包私钥...")
    try:
        wallets = _load_wallets(password)
    except (FileNotFoundError, ValueError) as e:
        print(f"\n错误：{e}")
        sys.exit(1)

    if not wallets:
        print("\n错误：wallets.key 中没有有效的钱包，请检查文件内容")
        sys.exit(1)

    del password  # 尽早清除明文密码

    wallet_manager.load(wallets)
    print(f"✓ 已加载 {wallet_manager.count()} 个钱包，轮询起始：{wallet_manager.current_label()}")
    for w in wallets:
        print(f"  {w.label}  FUNDER: {w.funder_display}")

    logger.info("=" * 52)
    logger.info("Polymarket 温度事件自动交易机器人 启动")
    logger.info("已加载 %d 个钱包，轮询起始：%s", wallet_manager.count(), wallet_manager.current_label())
    logger.info("=" * 52)

    # ── 步骤四：启动后台线程 ─────────────────────────────────────────
    obs_thread   = obs_poller.start()
    trade_thread = trader.start()

    logger.info("obs 拉取线程已启动：%s", obs_thread.name)
    logger.info("交易线程已启动：%s",   trade_thread.name)

    # ── 步骤五：主线程等待，Ctrl+C 优雅退出 ──────────────────────────
    def _shutdown(sig, frame):
        print("\n收到退出信号，正在关闭...")
        logger.info("收到信号 %s，程序退出", sig)
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print("程序已启动，按 Ctrl+C 退出\n")
    while True:
        # 检查子线程是否意外退出
        if not obs_thread.is_alive():
            logger.error("obs 拉取线程意外退出，重新启动...")
            obs_thread = obs_poller.start()
        if not trade_thread.is_alive():
            logger.error("交易线程意外退出，重新启动...")
            trade_thread = trader.start()
        time.sleep(30)


if __name__ == "__main__":
    main()
