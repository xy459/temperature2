"""
多钱包轮询管理模块。
从 wallets.key 加载加密私钥，严格轮询（无论成功或失败均切换到下一个）。
轮询索引持久化到 SQLite settings 表，重启后从上次位置继续。
"""
import logging
import threading
from dataclasses import dataclass
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class Wallet:
    index:       int    # 从 1 开始的序号
    private_key: str    # 解密后的私钥明文
    funder:      str    # Polymarket 代理钱包地址（POLY_FUNDER）

    @property
    def label(self) -> str:
        return f"钱包 #{self.index}"

    @property
    def funder_display(self) -> str:
        """用于日志显示的掩码地址"""
        f = self.funder
        return f"{f[:6]}...{f[-4:]}" if len(f) > 10 else f


class WalletManager:
    """线程安全的多钱包轮询管理器"""

    def __init__(self):
        self._wallets: List[Wallet] = []
        self._current_index: int = 0
        self._lock = threading.Lock()

    def load(self, wallets: List[Wallet]) -> None:
        if not wallets:
            raise ValueError("钱包列表不能为空")
        self._wallets = wallets

        try:
            import database as db
            idx = int(db.get_setting("wallet_current_index", "0"))
        except Exception as e:
            logger.warning("读取钱包轮询索引失败，从第1个开始: %s", e)
            idx = 0

        self._current_index = idx % len(wallets)
        logger.info("钱包轮询索引已恢复：%d（%s）", self._current_index, self._wallets[self._current_index].label)

    def get_next_wallet(self) -> Wallet:
        """返回当前轮到的钱包，并推进到下一个（严格轮询，线程安全）。"""
        with self._lock:
            if not self._wallets:
                raise RuntimeError("钱包列表为空，请检查 wallets.key 配置文件")
            wallet = self._wallets[self._current_index]
            self._current_index = (self._current_index + 1) % len(self._wallets)
            try:
                import database as db
                db.set_setting("wallet_current_index", str(self._current_index))
            except Exception as e:
                logger.warning("保存钱包轮询索引失败: %s", e)
            return wallet

    def current_label(self) -> str:
        if not self._wallets:
            return "（未加载）"
        return self._wallets[self._current_index].label

    def count(self) -> int:
        return len(self._wallets)

    def is_loaded(self) -> bool:
        return len(self._wallets) > 0


wallet_manager = WalletManager()
