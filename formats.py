"""Bounded, read-only parsing of QQ Music audio containers."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import struct


class FormatError(Exception):
    pass


@dataclass(frozen=True)
class AudioSource:
    path: Path
    kind: str
    audio_size: int
    song_mid: str = ''
    filename: str = ''
    ekey: str = ''
    footer_count: int = 0


def inspect_file(path: Path | str) -> AudioSource:
    path = Path(path)
    size = path.stat().st_size
    if size < 16:
        raise FormatError('文件过小或尚未下载完成。')
    with path.open('rb') as f:
        head = f.read(4)
        if head == b'fLaC':
            return AudioSource(path, 'flac', size)
        if path.suffix.lower() in ('.mp3', '.wav', '.ogg', '.m4a'):
            return AudioSource(path, path.suffix.lower()[1:], size)
        f.seek(-16, 2); tail = f.read(16)
        if tail[-8:] == b'musicex\0':
            audio_size = size; count = 0
            # Some QQ Music downloads contain repeated, conflicting musicex
            # footers. The original, innermost metadata identifies the audio.
            while audio_size >= 16:
                f.seek(audio_size - 16); tail = f.read(16)
                if tail[-8:] != b'musicex\0':
                    break
                count += 1
                if count > 64:
                    raise FormatError('musicex 文件尾重复次数异常。')
                length, version = struct.unpack('<II', tail[:8])
                if version != 1 or length < 156 or length > min(16384, audio_size - 16):
                    raise FormatError('不支持的 musicex 版本或损坏的文件尾。')
                # footer_size includes its 16-byte trailer; subtract only once.
                audio_size -= length
                f.seek(audio_size); block = f.read(length - 16)
                mid = block[12:72].decode('utf-16-le', errors='strict').split('\0')[0]
                filename = block[72:172].decode('utf-16-le', errors='strict').split('\0')[0]
                if not mid or not filename or any(c in filename for c in '/\\'):
                    raise FormatError('musicex 歌曲标识或资源名无效。')
            return AudioSource(path, 'musicex', audio_size, mid, filename, footer_count=count)
        if tail[-4:] == b'QTag':
            length = int.from_bytes(tail[-8:-4], 'big')
            if not 8 < length <= min(16384, size - 8):
                raise FormatError('损坏的 QTag 文件尾。')
            f.seek(size - length - 8); body = f.read(length)
            parts = body.split(b',')
            if len(parts) < 2:
                raise FormatError('QTag 文件缺少内嵌密钥。')
            return AudioSource(path, 'qmc2', size - length - 8,
                               parts[1].decode('ascii'), ekey=parts[0].decode('ascii'))
        if tail[-4:] == b'STag':
            raise FormatError('该 STag 文件没有内嵌密钥；目前只支持 musicex 自动取密钥。')
        length = int.from_bytes(tail[-4:], 'little')
        if 8 < length <= min(16384, size - 16):
            f.seek(size - 4 - length)
            try:
                key = f.read(length).decode('ascii')
            except UnicodeDecodeError:
                raise FormatError('无法识别内嵌密钥。') from None
            return AudioSource(path, 'qmc2', size - 4 - length, ekey=key)
    raise FormatError('不支持的音频格式，或文件尚未下载完成。')
