"""Local conversion pipeline. Network/session use is explicitly opt-in."""
from __future__ import annotations
import argparse
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from formats import AudioSource, FormatError, inspect_file
from qmc_crypto import CryptoError, QMC2, derive_key
from qq_session import QQKeyProvider, SessionError

ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
DEFAULT_SOURCE = Path.home() / 'Music'
SUPPORTED = {'.mflac', '.mflac0', '.qmcflac', '.flac', '.mp3', '.wav', '.ogg', '.m4a'}


class ConversionError(Exception):
    pass


class Cancelled(ConversionError):
    pass


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled('已取消')


def collect_files(paths, excluded=None) -> list[Path]:
    found = set()
    exclude = Path(excluded).resolve() if excluded else None
    for item in paths:
        item = Path(item)
        candidates = item.rglob('*') if item.is_dir() else [item]
        for p in candidates:
            if p.is_file() and p.suffix.lower() in SUPPORTED:
                p = p.resolve()
                if not exclude or not p.is_relative_to(exclude):
                    found.add(p)
    return sorted(found, key=lambda p: str(p).casefold())


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args, cancel=None, progress=None, duration=None, strict=True):
    check_cancel(cancel)
    command = [ffmpeg_exe(), '-hide_banner', '-nostdin', '-v', 'error']
    if strict:
        command.append('-xerror')
    command.extend(args)
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        updates = queue.Queue(); output_lines = deque(maxlen=256)
        def read():
            try:
                for line in proc.stdout:
                    line = line.decode('utf-8', errors='replace').strip()
                    output_lines.append(line); updates.put(line)
            finally:
                updates.put(None)
        reader = threading.Thread(target=read, daemon=True); reader.start()
        try:
            while proc.poll() is None:
                if cancel is not None and cancel.is_set():
                    raise Cancelled('已取消')
                try:
                    line = updates.get(timeout=0.08)
                    if line and line.startswith('out_time_us=') and duration and progress:
                        try:
                            progress(min(1, int(line.split('=')[1]) / 1e6 / duration))
                        except ValueError:
                            pass
                except queue.Empty:
                    pass
            reader.join(timeout=2)
            if proc.returncode:
                errors.seek(0)
                detail = errors.read(8000).decode('utf-8', errors='replace')
                # FFmpeg sees audio file paths, never account/session credentials.
                raise ConversionError('音频校验或转码失败：' + (detail.strip()[-1200:] or str(proc.returncode)))
            if progress:
                progress(1)
            return '\n'.join(output_lines)
        finally:
            if proc.poll() is None:
                proc.kill(); proc.wait()
            proc.stdout.close()
            reader.join(timeout=2)


def pcm_md5(path, bits, cancel=None, strict=True):
    codec = {8: 's8', 16: 's16le', 24: 's24le', 32: 's32le'}.get(bits)
    if codec is None:
        raise ConversionError('暂不支持该位深的音频完整性校验。')
    output = run_ffmpeg(['-i', str(path), '-map', '0:a:0', '-c:a', 'pcm_'+codec,
                         '-f', 'md5', '-'], cancel, strict=strict)
    match = re.search(r'^MD5=([0-9a-f]{32})$', output, re.M)
    if not match:
        raise ConversionError('无法取得完整音频 MD5。')
    return match.group(1)


def validate_flac(path, cancel=None, log=None):
    """Accept nonstandard input only after a complete PCM checksum match.

    Some QQ downloads decode to all correct audio samples but contain invalid
    non-audio packets. Normalize those through the lossless FLAC encoder, then
    require both strict decoding and the original STREAMINFO PCM MD5.
    """
    log = log or (lambda message: None)
    try:
        run_ffmpeg(['-i', str(path), '-map', '0:a:0', '-f', 'null', '-'], cancel)
        return False
    except Cancelled:
        raise
    except ConversionError as original_error:
        from mutagen.flac import FLAC
        info = FLAC(path).info
        expected = f'{info.md5_signature:032x}'
        if expected == '0'*32:
            raise ConversionError('解密后的音频含非标准数据，且缺少原始音频 MD5，无法安全整理。') from original_error
        if pcm_md5(path, info.bits_per_sample, cancel, strict=False) != expected:
            raise ConversionError('完整音频 MD5 不匹配，文件可能损坏或密钥不兼容；未生成输出。') from original_error
        log('完整音频 MD5 一致；正在整理非标准数据，生成标准 FLAC…')
        cleaned = Path(path).with_name('normalized.flac')
        args = ['-i', str(path), '-map', '0:a:0', '-map', '0:v:0?',
                '-map_metadata', '0', '-c:a', 'flac', '-compression_level', '5',
                '-c:v', 'copy', '-disposition:v:0', 'attached_pic']
        if info.bits_per_sample <= 16:
            args += ['-sample_fmt', 's16']
        elif info.bits_per_sample == 24:
            args += ['-sample_fmt', 's32', '-bits_per_raw_sample', '24']
        elif info.bits_per_sample == 32:
            args += ['-sample_fmt', 's32', '-bits_per_raw_sample', '32']
        args += ['-y', str(cleaned)]
        run_ffmpeg(args, cancel, strict=False)
        # Hashing with strict decoding ensures the cleaned output contains no
        # invalid packets, while matching PCM proves no audible samples changed.
        if pcm_md5(cleaned, info.bits_per_sample, cancel, strict=True) != expected:
            raise ConversionError('整理后的 FLAC 音频 MD5 不匹配，已停止保存。')
        clean_info = FLAC(cleaned).info
        if (clean_info.total_samples != info.total_samples
                or clean_info.sample_rate != info.sample_rate
                or clean_info.channels != info.channels):
            raise ConversionError('整理后的 FLAC 音频参数不匹配，已停止保存。')
        check_cancel(cancel)
        os.replace(cleaned, path)
        return True


def audio_info(path):
    from mutagen import File
    f = File(str(path))
    if f is None or not getattr(f, 'info', None):
        raise ConversionError('无法读取输出音频信息。')
    return {'duration_seconds': round(f.info.length, 5),
            'sample_rate': f.info.sample_rate, 'channels': f.info.channels,
            'bits_per_sample': getattr(f.info, 'bits_per_sample', None),
            'bitrate': getattr(f.info, 'bitrate', None)}


def _tag_flac(path, stem):
    from mutagen.flac import FLAC
    tags = FLAC(str(path))
    clean = re.sub(r'_(?:EG|HQ|SQ|HiRes)$', '', stem, flags=re.I)
    if ' - ' in clean:
        artist, title = clean.split(' - ', 1)
        if 'artist' not in tags:
            tags['artist'] = artist
        if 'title' not in tags:
            tags['title'] = title
    elif 'title' not in tags:
        tags['title'] = clean
    tags.save()


class KeyCache:
    def __init__(self, path=None):
        self.path = Path(path) if path else ROOT / 'data' / 'keys.json'
        self.entries = {}
        if self.path.is_file():
            try:
                self.entries = json.loads(self.path.read_text('utf-8'))
                if not isinstance(self.entries, dict):
                    raise ValueError
            except (OSError, ValueError):
                raise ConversionError('歌曲密钥缓存损坏，请备份后移走 data/keys.json，或选择有效的密钥 JSON。') from None

    def get(self, meta):
        # Resource filename differentiates downloads of the same song at different quality.
        return self.entries.get(meta.filename, '') if meta.filename else ''

    def put(self, meta, key):
        self.entries[meta.filename] = key
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix='keys-', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self.entries, f, ensure_ascii=False, indent=2)
                f.flush(); os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            Path(tmp).unlink(missing_ok=True)


@dataclass
class ConversionResult:
    source: str
    status: str
    outputs: list
    seconds: float
    error: str = ''


class Converter:
    def __init__(self, cache=None, allow_online=False):
        self.cache = cache if cache is not None else KeyCache()
        self.allow_online = allow_online
        self.provider = QQKeyProvider()

    def close(self):
        self.provider.clear()

    def _cipher(self, meta, cancel, log):
        ekey = meta.ekey or self.cache.get(meta)
        if not ekey:
            if not self.allow_online:
                raise ConversionError('新版 musicex 文件未内嵌密钥；请导入歌曲密钥，或勾选“允许联网获取歌曲密钥”并登录 QQ 音乐。')
            log('正在向腾讯请求歌曲密钥（音频不上传）…')
            ekey = self.provider.fetch(meta, cancel)
        def valid(key):
            cipher = QMC2(derive_key(key))
            with meta.path.open('rb') as f:
                head = cipher.decrypt(f.read(min(64, meta.audio_size)))
            if not head.startswith(b'fLaC'):
                raise CryptoError('解密后不是 FLAC，歌曲密钥不匹配或文件损坏。')
            return cipher
        try:
            cipher = valid(ekey)
        except CryptoError:
            if not self.allow_online or meta.ekey:
                raise
            log('缓存密钥未通过校验，重新向腾讯请求…')
            ekey = self.provider.fetch(meta, cancel)
            cipher = valid(ekey)
        if meta.kind == 'musicex' and self.cache.get(meta) != ekey:
            self.cache.put(meta, ekey)
        return cipher

    def convert(self, source, output_dir, target='flac', bitrate=320,
                cancel=None, progress=None, log=None) -> ConversionResult:
        started = time.monotonic(); source = Path(source); destination = Path(output_dir)
        progress = progress or (lambda value: None); log = log or (lambda message: None)
        if target not in ('flac', 'mp3', 'both') or bitrate not in (128, 192, 256, 320):
            raise ConversionError('输出格式或 MP3 码率无效。')
        check_cancel(cancel)
        meta = inspect_file(source)
        cipher = self._cipher(meta, cancel, log) if meta.kind in ('musicex', 'qmc2') else None
        destination.mkdir(parents=True, exist_ok=True)
        published = []
        try:
            with tempfile.TemporaryDirectory(prefix='.conversion-', dir=destination) as tmp:
                tmp = Path(tmp); decoded = tmp / 'decoded.flac'
                if cipher:
                    log('正在本地解密…')
                    with source.open('rb') as reader, decoded.open('wb') as writer:
                        offset = 0
                        while offset < meta.audio_size:
                            check_cancel(cancel)
                            chunk = reader.read(min(1024 * 1024, meta.audio_size - offset))
                            if not chunk:
                                raise ConversionError('原文件在转换期间被截断。')
                            writer.write(cipher.decrypt(chunk, offset))
                            offset += len(chunk)
                            progress(0.45 * offset / meta.audio_size)
                elif meta.kind == 'flac':
                    shutil.copyfile(source, decoded)
                    progress(0.45)
                else:
                    log('正在转换为 FLAC…')
                    run_ffmpeg(['-i', str(source), '-map', '0:a:0', '-vn', '-c:a', 'flac',
                                '-y', str(decoded)], cancel)
                _tag_flac(decoded, source.stem)
                log('正在完整解码校验 FLAC…')
                validate_flac(decoded, cancel, log)
                flac_info = audio_info(decoded); progress(0.60)
                staged = []
                if target in ('flac', 'both'):
                    staged.append((decoded, '.flac', flac_info))
                if target in ('mp3', 'both'):
                    mp3 = tmp / 'encoded.mp3'; log(f'正在编码 MP3 {bitrate} kbps…')
                    # LAME supports at most 48 kHz and 2 channels; conversion is explicit.
                    args = ['-i', str(decoded), '-map', '0:a:0', '-map_metadata', '0',
                            '-c:a', 'libmp3lame', '-b:a', f'{bitrate}k',
                            '-id3v2_version', '3']
                    if flac_info['sample_rate'] > 48000:
                        args += ['-ar', '48000']
                    if flac_info['channels'] > 2:
                        args += ['-ac', '2']
                    # Preserve the common attached cover image when FFmpeg can map it.
                    args += ['-map', '0:v:0?', '-c:v', 'copy', '-disposition:v:0', 'attached_pic', '-progress', 'pipe:1',
                             '-y', str(mp3)]
                    run_ffmpeg(args, cancel, lambda x: progress(.60 + .30*x),
                               flac_info['duration_seconds'])
                    log('正在完整解码校验 MP3…')
                    run_ffmpeg(['-i', str(mp3), '-map', '0:a:0', '-f', 'null', '-'], cancel)
                    mp3_info = audio_info(mp3)
                    if abs(mp3_info['duration_seconds'] - flac_info['duration_seconds']) > 0.15:
                        raise ConversionError('MP3 时长校验失败。')
                    staged.append((mp3, '.mp3', mp3_info))
                check_cancel(cancel)
                for p, suffix, info in staged:
                    # Exclusive reservation protects existing files even against races.
                    index = 0
                    while True:
                        name = source.stem + (f' ({index})' if index else '') + suffix
                        out = destination / name
                        try:
                            with out.open('xb'):
                                pass
                            break
                        except FileExistsError:
                            index += 1
                    try:
                        os.replace(p, out)
                    except BaseException:
                        out.unlink(missing_ok=True)
                        raise
                    published.append({'path': str(out.resolve()), 'size': out.stat().st_size, **info})
                progress(1)
            return ConversionResult(str(source), 'success', published, round(time.monotonic()-started, 3))
        except BaseException:
            for entry in published:
                Path(entry['path']).unlink(missing_ok=True)
            raise


def main(argv=None):
    p = argparse.ArgumentParser(description='拾音 Shiyin 本地音频转换器')
    p.add_argument('sources', nargs='*', default=[str(DEFAULT_SOURCE)])
    p.add_argument('-o', '--output', default=str(ROOT / 'output'))
    p.add_argument('--format', choices=['flac', 'mp3', 'both'], default='flac')
    p.add_argument('--bitrate', choices=[128, 192, 256, 320], type=int, default=320)
    p.add_argument('--allow-online', action='store_true', help='允许读取 QQ 音乐登录态向腾讯获取歌曲密钥')
    p.add_argument('--key-cache', type=Path)
    p.add_argument('--inspect', action='store_true')
    p.add_argument('--report', type=Path)
    args = p.parse_args(argv)
    files = collect_files(args.sources, args.output)
    if not files:
        p.error('没有找到支持的音频文件。')
    if args.inspect:
        rows = []
        for path in files:
            try:
                m = inspect_file(path)
                rows.append({'file': str(path), 'format': m.kind, 'resource': m.filename,
                             'audio_bytes': m.audio_size, 'embedded_key': bool(m.ekey)})
            except (FormatError, OSError, UnicodeError) as e:
                rows.append({'file': str(path), 'error': str(e)})
        print(json.dumps(rows, ensure_ascii=False, indent=2)); return 0
    engine = Converter(KeyCache(args.key_cache), args.allow_online)
    results = []; cancel = threading.Event()
    try:
        for i, path in enumerate(files, 1):
            print(f'[{i}/{len(files)}] {path.name}', flush=True)
            start = time.monotonic()
            try:
                r = engine.convert(path, args.output, args.format, args.bitrate, cancel,
                                   log=lambda s: print('  '+s, flush=True))
            except KeyboardInterrupt:
                cancel.set(); r = ConversionResult(str(path), 'cancelled', [], 0, '已取消')
            except (ConversionError, FormatError, CryptoError, SessionError, OSError, ValueError) as e:
                r = ConversionResult(str(path), 'failed', [], round(time.monotonic()-start,3), str(e))
            results.append(asdict(r))
            print('  '+r.status+(': '+r.error if r.error else ''), flush=True)
            if r.status == 'cancelled':
                break
    finally:
        engine.close()
    report = {'created_at': datetime.now().astimezone().isoformat(),
              'format': args.format, 'bitrate_kbps': args.bitrate,
              'success': sum(r['status']=='success' for r in results),
              'failed': sum(r['status']=='failed' for r in results), 'results': results}
    report_path = args.report or Path(args.output) / f'转换报告-{datetime.now():%Y%m%d-%H%M%S-%f}.json'
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    print(f'完成：成功 {report["success"]}，失败 {report["failed"]}。报告：{report_path}', flush=True)
    return 0 if len(results)==len(files) and report['success']==len(files) else 1


if __name__ == '__main__':
    raise SystemExit(main())
