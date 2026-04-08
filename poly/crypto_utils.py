"""
AES-256-CBC 加密解密，使用 PBKDF2 密钥派生。
与前端 JavaScript 加密算法保持一致：salt(16) + iv(16) + ciphertext，Base64 编码。
"""

import base64
import os
from typing import Optional

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.padding import PKCS7


class AESEncryption:
    SALT_SIZE  = 16
    IV_SIZE    = 16
    KEY_SIZE   = 32
    ITERATIONS = 100000

    def __init__(self, password: str):
        if not password:
            raise ValueError("密码不能为空")
        self.password = password.encode("utf-8")

    def _derive_key(self, password: bytes, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=self.KEY_SIZE,
            salt=salt,
            iterations=self.ITERATIONS,
            backend=default_backend(),
        )
        return kdf.derive(password)

    def encrypt(self, plaintext: str) -> str:
        salt = os.urandom(self.SALT_SIZE)
        iv   = os.urandom(self.IV_SIZE)
        key  = self._derive_key(self.password, salt)

        padder = PKCS7(algorithms.AES.block_size).padder()
        padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()

        cipher    = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(padded) + encryptor.finalize()

        return base64.b64encode(salt + iv + ciphertext).decode("utf-8")

    def decrypt(self, encrypted_text: str) -> str:
        try:
            combined   = base64.b64decode(encrypted_text)
            salt       = combined[: self.SALT_SIZE]
            iv         = combined[self.SALT_SIZE : self.SALT_SIZE + self.IV_SIZE]
            ciphertext = combined[self.SALT_SIZE + self.IV_SIZE :]

            key = self._derive_key(self.password, salt)

            cipher    = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
            decryptor = cipher.decryptor()
            padded    = decryptor.update(ciphertext) + decryptor.finalize()

            unpadder  = PKCS7(algorithms.AES.block_size).unpadder()
            plaintext = unpadder.update(padded) + unpadder.finalize()
            return plaintext.decode("utf-8")
        except Exception as e:
            raise ValueError(f"解密失败：数据格式不正确或密码错误 ({e})")
