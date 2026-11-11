"""QMC2 decoding, ported from ownlight6/qmc-decoder (GPL-3.0).

The optional native implementation accelerates the same per-segment cipher.
"""
from __future__ import annotations
import base64
import ctypes
from pathlib import Path
import struct
import sys


class CryptoError(Exception):
    pass


def _tea_block(block: bytes, key: bytes) -> bytes:
    v0, v1 = struct.unpack('>2I', block)
    a, b, c, d = struct.unpack('>4I', key)
    total = 0xE3779B90
    for _ in range(16):
        v1 = (v1 - (((v0 << 4) + c) ^ (v0 + total) ^ ((v0 >> 5) + d))) & 0xFFFFFFFF
        v0 = (v0 - (((v1 << 4) + a) ^ (v1 + total) ^ ((v1 >> 5) + b))) & 0xFFFFFFFF
        total = (total - 0x9E3779B9) & 0xFFFFFFFF
    return struct.pack('>2I', v0, v1)


def tc_tea_decrypt(data: bytes, key: bytes) -> bytes:
    if len(data) < 16 or len(data) % 8:
        raise CryptoError('密钥 TEA 数据长度无效。')
    previous_x = bytes(8); previous_c = bytes(8); plain = bytearray()
    for offset in range(0, len(data), 8):
        current = data[offset:offset+8]
        x = _tea_block(bytes(a ^ b for a, b in zip(current, previous_x)), key)
        plain.extend(a ^ b for a, b in zip(x, previous_c))
        previous_x = x; previous_c = current
    start = (plain[0] & 7) + 3
    if start + 7 > len(plain) or plain[-7:] != bytes(7):
        raise CryptoError('歌曲密钥校验失败。')
    return bytes(plain[start:-7])


def derive_key(ekey: str) -> bytes:
    try:
        raw = base64.b64decode(ekey.strip().strip('\0'), validate=True)
        if raw.startswith(b'QQMusic EncV2,Key:'):
            raw = tc_tea_decrypt(raw[18:], b'386ZJY!@#*$%^&)(')
            raw = tc_tea_decrypt(raw, b'**#!(#$%&^a1cZ,T')
            raw = base64.b64decode(raw, validate=True)
        if len(raw) < 8:
            raise CryptoError('歌曲密钥过短。')
        header = raw[:8]
        tea_key = bytes(v for pair in zip(bytes.fromhex('695646382b20150b'), header)
                        for v in pair)
        return header + tc_tea_decrypt(raw[8:], tea_key)
    except CryptoError:
        raise
    except (ValueError, TypeError):
        raise CryptoError('歌曲密钥格式无效。') from None


def _native_library():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    p = root / 'qmc_native.dll'
    if not p.is_file():
        return None
    try:
        lib = ctypes.CDLL(str(p))
        lib.qmc_decrypt.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint64,
                                    ctypes.c_void_p, ctypes.c_size_t]
        lib.qmc_decrypt.restype = ctypes.c_int
        return lib
    except OSError:
        return None


class QMC2:
    def __init__(self, key: bytes, native=True):
        if not 8 <= len(key) <= 4096:
            raise CryptoError('不支持的歌曲密钥长度。')
        self.key = key
        self.lib = _native_library() if native else None
        self.hash = 1
        for value in key:
            if value:
                new = (self.hash * value) & 0xFFFFFFFF
                if new <= self.hash:
                    break
                self.hash = new
        n = len(key); s = [i & 255 for i in range(n)]; j = 0
        for i in range(n):
            j = (j + s[i] + key[i]) % n
            s[i], s[j] = s[j], s[i]
        self.box = s

    def segment_key(self, index, seed):
        if seed == 0:
            return 0
        return int(self.hash / ((index + 1) * seed) * 100.0)

    def decrypt(self, data: bytes, offset=0) -> bytes:
        if self.lib:
            b = ctypes.create_string_buffer(data, len(data))
            if self.lib.qmc_decrypt(b, len(data), offset, self.key, len(self.key)):
                raise CryptoError('原生解码器失败。')
            return b.raw
        out = bytearray(data); key = self.key; n = len(key)
        if n <= 300:
            for i in range(len(out)):
                pos = offset + i
                if pos > 0x7FFF:
                    pos %= 0x7FFF
                index = (pos * pos + 71214) % n
                rotate = (index + 4) & 7
                out[i] ^= ((key[index] << rotate) | (key[index] >> rotate)) & 255
            return bytes(out)
        i = 0
        while i < len(out) and offset + i < 128:
            pos = offset + i
            out[i] ^= key[self.segment_key(pos, key[pos % n]) % n]
            i += 1
        while i < len(out):
            pos = offset + i; segment = pos // 5120
            discard = (self.segment_key(segment, key[segment % n]) & 511) + pos % 5120
            amount = min(len(out) - i, 5120 - pos % 5120)
            s = self.box.copy(); j = k = 0
            for step in range(discard + amount):
                j = (j + 1) % n; k = (k + s[j]) % n
                s[j], s[k] = s[k], s[j]
                if step >= discard:
                    out[i + step - discard] ^= s[(s[j] + s[k]) % n]
            i += amount
        return bytes(out)
