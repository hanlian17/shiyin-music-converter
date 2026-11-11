"""Optional QQ Music session access; no credentials are persisted or logged.

Protocol reference: ownlight6/qmc-decoder (GPL-3.0), src/ekey_fetch.rs.
Only the QQ Music client's authst field is inspected, after opt-in.
"""
from __future__ import annotations

import configparser
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import re
import time
import urllib.request
import urllib.error


class SessionError(Exception):
    pass


def _tokens(data: bytes):
    pattern = rb'"authst"\s*:\s*"([A-Za-z0-9_+/=\-]{20,4096})"'
    yield from (m.group(1).decode('ascii') for m in re.finditer(pattern, data))
    if b'a\x00u\x00t\x00h\x00s\x00t\x00' in data:
        for shift in (0, 1):
            text = data[shift:].decode('utf-16-le', errors='ignore')
            yield from (m.group(1) for m in re.finditer(pattern.decode(), text))


def read_session_candidates(cancel=None) -> tuple[str, list[str]]:
    if os.name != 'nt':
        raise SessionError('自动获取密钥目前仅支持 Windows。')
    base = Path(os.environ.get('APPDATA', '')) / 'Tencent' / 'QQMusic'
    cfg = configparser.ConfigParser()
    cfg.read(base / 'QQMusicServiceConfig.ini', encoding='utf-8-sig')
    uin = next((cfg.get(s, 'uin', fallback='') for s in cfg.sections()
                if cfg.get(s, 'uin', fallback='').isdigit()), '')
    if not uin or int(uin) == 0:
        raise SessionError('未找到 QQ 音乐账号。请打开并登录下载歌曲的账号。')
    tokens: list[str] = []
    for name in ('SetCookie.dat', '_SetCookie.dat'):
        p = base / name
        if p.is_file() and p.stat().st_size < 2_000_000:
            tokens.extend(_tokens(p.read_bytes()))

    k = C.WinDLL('kernel32', use_last_error=True)
    class Entry(C.Structure):
        _fields_ = [('dwSize', W.DWORD), ('cntUsage', W.DWORD),
                    ('th32ProcessID', W.DWORD), ('th32DefaultHeapID', C.c_size_t),
                    ('th32ModuleID', W.DWORD), ('cntThreads', W.DWORD),
                    ('th32ParentProcessID', W.DWORD), ('pcPriClassBase', W.LONG),
                    ('dwFlags', W.DWORD), ('szExeFile', W.WCHAR * 260)]
    class Region(C.Structure):
        _fields_ = [('base', C.c_void_p), ('allocation', C.c_void_p),
                    ('allocation_protect', W.DWORD), ('partition', W.WORD),
                    ('size', C.c_size_t), ('state', W.DWORD),
                    ('protect', W.DWORD), ('kind', W.DWORD)]
    k.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    k.CreateToolhelp32Snapshot.restype = W.HANDLE
    k.Process32FirstW.argtypes = [W.HANDLE, C.POINTER(Entry)]
    k.Process32NextW.argtypes = [W.HANDLE, C.POINTER(Entry)]
    k.CloseHandle.argtypes = [W.HANDLE]
    k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    k.OpenProcess.restype = W.HANDLE
    k.VirtualQueryEx.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(Region), C.c_size_t]
    k.VirtualQueryEx.restype = C.c_size_t
    k.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p,
                                    C.c_size_t, C.POINTER(C.c_size_t)]
    k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR,
                                           C.POINTER(W.DWORD)]
    snapshot = k.CreateToolhelp32Snapshot(2, 0)
    if snapshot == C.c_void_p(-1).value:
        raise SessionError('无法枚举 QQ 音乐进程。')
    entries = []
    try:
        e = Entry(); e.dwSize = C.sizeof(e)
        ok = k.Process32FirstW(snapshot, C.byref(e))
        while ok:
            if e.szExeFile.lower() in ('qqmusic.exe', 'qmbrowser.exe'):
                entries.append((e.szExeFile.lower(), e.th32ProcessID))
            ok = k.Process32NextW(snapshot, C.byref(e))
    finally:
        k.CloseHandle(snapshot)
    if not entries and not tokens:
        raise SessionError('未发现正在运行的 QQ 音乐，请打开并登录后重试。')
    deadline = time.monotonic() + 60
    for name, pid in sorted(entries):
        h = k.OpenProcess(0x410, False, pid)  # QUERY_INFORMATION | VM_READ only
        if not h:
            continue
        try:
            image = C.create_unicode_buffer(32768); length = W.DWORD(len(image))
            if not k.QueryFullProcessImageNameW(h, 0, image, C.byref(length)):
                continue
            if 'qqmusic' not in image.value.lower():
                continue
            address = 0; scanned = 0
            while scanned < 512 * 1024 * 1024 and time.monotonic() < deadline:
                if cancel is not None and cancel.is_set():
                    raise SessionError('已取消')
                r = Region()
                if not k.VirtualQueryEx(h, C.c_void_p(address), C.byref(r), C.sizeof(r)):
                    break
                start = r.base or 0; end = start + r.size
                if end <= address:
                    break
                if (r.state == 0x1000 and r.kind in (0x20000, 0x1000000)
                        and not r.protect & 0x101
                        and r.protect & 0xFF in (2, 4, 8, 0x20, 0x40, 0x80)):
                    cursor = start; overlap = b''
                    while cursor < end and time.monotonic() < deadline:
                        if cancel is not None and cancel.is_set():
                            raise SessionError('已取消')
                        amount = min(1024 * 1024, end - cursor)
                        buf = C.create_string_buffer(amount); count = C.c_size_t()
                        k.ReadProcessMemory(h, C.c_void_p(cursor), buf, amount, C.byref(count))
                        if count.value:
                            data = overlap + buf.raw[:count.value]
                            for token in _tokens(data):
                                if token not in tokens:
                                    tokens.append(token)
                            overlap = data[-8192:]
                        cursor += amount; scanned += amount
                        if len(tokens) >= 8:
                            break
                address = end
                if len(tokens) >= 8:
                    break
        finally:
            k.CloseHandle(h)
    tokens = list(dict.fromkeys(tokens))[:8]
    if not tokens:
        raise SessionError('读取不到 QQ 音乐有效登录态。请重新登录；本工具与 QQ 音乐须以相同权限运行。')
    return uin, tokens


class QQKeyProvider:
    def __init__(self):
        self._session = None

    def clear(self):
        self._session = None

    def fetch(self, meta, cancel=None) -> str:
        if self._session is None:
            self._session = read_session_candidates(cancel)
        uin, tokens = self._session
        last_code = None
        for token in tokens:
            for login_type in ('3', '1'):
                if cancel is not None and cancel.is_set():
                    raise SessionError('已取消')
                payload = {
                    'comm': {'authst': token, 'ct': '19', 'cv': '1859',
                             'uin': uin, 'tmeLoginType': login_type},
                    'req_1': {'module': 'music.vkey.GetEVkey', 'method': 'CgiGetEVkey',
                              'param': {'filename': [meta.filename], 'guid': '10000',
                                        'songmid': [meta.song_mid], 'songtype': [1],
                                        'uin': uin, 'loginflag': 1, 'platform': '27', 'ctx': 1}}}
                req = urllib.request.Request('https://u.y.qq.com/cgi-bin/musicu.fcg',
                    data=json.dumps(payload).encode(), method='POST',
                    headers={'Content-Type': 'application/json', 'Referer': 'https://y.qq.com/',
                             'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36'})
                # No redirects: session credentials must only go to the intended QQ host.
                class NoRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, *args, **kwargs):
                        return None
                try:
                    with urllib.request.build_opener(NoRedirect).open(req, timeout=18) as response:
                        result = json.loads(response.read(1024 * 1024))
                except (urllib.error.URLError, OSError, ValueError):
                    raise SessionError('连接腾讯歌曲密钥接口失败，请检查网络后重试。') from None
                node = result.get('req_1', {})
                rows = node.get('data', {}).get('midurlinfo', [])
                if rows and rows[0].get('ekey'):
                    self._session = (uin, [token])
                    return rows[0]['ekey']
                last_code = rows[0].get('result') if rows else node.get('code', result.get('code'))
        raise SessionError(f'腾讯未返回歌曲密钥（代码 {last_code}）。请检查账号登录状态、会员或歌曲购买权限。')
