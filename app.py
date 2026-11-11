"""Windows desktop interface for the local converter."""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from converter import (ROOT, DEFAULT_SOURCE, Converter, KeyCache, ConversionResult,
                       collect_files, Cancelled)
from formats import inspect_file


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('拾音 Shiyin v1.2.1 · MFLAC → FLAC / MP3')
        width = min(1120, max(940, self.winfo_screenwidth() - 80))
        height = min(800, max(620, self.winfo_screenheight() - 100))
        self.geometry(f'{width}x{height}'); self.minsize(940, 620)
        self.configure(bg='#f3f6f5')
        self.option_add('*Font', ('Microsoft YaHei UI', 10))
        self.events = queue.Queue(); self.cancel = threading.Event()
        self.busy = False; self.paths = {}; self.controls = []; self.results = []
        config = self.load_config()
        self.source = tk.StringVar(value=config.get('source', str(DEFAULT_SOURCE)))
        self.output = tk.StringVar(value=config.get('output', str(ROOT / 'output')))
        self.target = tk.StringVar(value=config.get('target', 'FLAC（无损）'))
        self.bitrate = tk.StringVar(value=str(config.get('bitrate', 320)))
        self.online = tk.BooleanVar(value=False)
        self.selected_only = tk.BooleanVar(value=False)
        self.cache_path = tk.StringVar(value=str(ROOT / 'data' / 'keys.json'))
        self.status = tk.StringVar(value='准备就绪')
        self.summary = tk.StringVar(value='选择文件夹，或添加歌曲开始。')
        self.current = tk.StringVar(value='尚未开始转换')
        self._build()
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.after(100, self.poll)
        self.after(300, self.scan)

    def load_config(self):
        try:
            d = json.loads((ROOT / 'data' / 'settings.json').read_text('utf-8'))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_config(self):
        try:
            p = ROOT / 'data'; p.mkdir(exist_ok=True)
            (p / 'settings.json').write_text(json.dumps({
                'source': self.source.get(), 'output': self.output.get(),
                'target': self.target.get(), 'bitrate': int(self.bitrate.get())},
                ensure_ascii=False, indent=2), 'utf-8')
        except (OSError, ValueError):
            pass

    def button(self, parent, text, command, **kw):
        b = ttk.Button(parent, text=text, command=command, **kw)
        self.controls.append(b)
        return b

    def _build(self):
        style = ttk.Style(self); style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('TFrame', background='#f3f6f5')
        style.configure('TLabel', background='#f3f6f5', foreground='#203b35')
        style.configure('TCheckbutton', background='#f3f6f5')
        style.configure('TButton', padding=(13, 7))
        style.configure('Accent.TButton', background='#087f65', foreground='white', padding=(22, 9))
        style.map('Accent.TButton', background=[('active', '#086c57'), ('disabled', '#99b6ae')])
        style.configure('Treeview', rowheight=31, background='white', fieldbackground='white')
        style.configure('Treeview.Heading', font=('Microsoft YaHei UI', 10, 'bold'), padding=7)
        style.configure('Horizontal.TProgressbar', background='#0c9877', troughcolor='#dfe9e5')
        outer = ttk.Frame(self, padding=24); outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='拾音 Shiyin', font=('Microsoft YaHei UI', 23, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='MFLAC  →  FLAC / MP3     ·     本地处理     ·     保留原文件',
                  foreground='#61796f').pack(anchor='w', pady=(5, 18))
        settings = ttk.Frame(outer); settings.pack(fill='x')
        settings.columnconfigure(1, weight=1)
        for row, (label, var, command) in enumerate([
            ('歌曲文件夹', self.source, self.choose_source),
            ('输出文件夹', self.output, self.choose_output)]):
            ttk.Label(settings, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=5)
            e = ttk.Entry(settings, textvariable=var)
            e.grid(row=row, column=1, sticky='ew', pady=5); self.controls.append(e)
            self.button(settings, '选择文件夹', command).grid(row=row, column=2, padx=(10, 0), pady=5)
        options = ttk.Frame(outer); options.pack(fill='x', pady=(12, 4))
        ttk.Label(options, text='输出格式').pack(side='left')
        combo = ttk.Combobox(options, textvariable=self.target, state='readonly', width=23,
                             values=['FLAC（无损）', 'MP3（有损）', '同时输出 FLAC 和 MP3'])
        combo.pack(side='left', padx=(10, 22)); self.controls.append(combo)
        ttk.Label(options, text='MP3 码率').pack(side='left')
        b = ttk.Combobox(options, textvariable=self.bitrate, state='readonly', width=6,
                         values=['128', '192', '256', '320'])
        b.pack(side='left', padx=(10, 5)); self.controls.append(b)
        ttk.Label(options, text='kbps').pack(side='left')
        self.button(options, '导入密钥 JSON', self.import_keys).pack(side='right')
        privacy = ttk.Frame(outer); privacy.pack(fill='x', pady=(5, 12))
        check = ttk.Checkbutton(privacy, variable=self.online, text='允许联网获取歌曲密钥（需登录 QQ 音乐）')
        check.pack(anchor='w'); self.controls.append(check)
        ttk.Label(privacy, text='勾选后读取 QQ 音乐登录态，仅向腾讯请求歌曲密钥；音频不上传。已有密钥可离线转换。',
                  foreground='#61796f', font=('Microsoft YaHei UI', 9)).pack(anchor='w', pady=(3, 0))
        actions = ttk.Frame(outer); actions.pack(fill='x', pady=(0, 10))
        self.start_button = self.button(actions, '开始转换', self.start, style='Accent.TButton')
        self.start_button.pack(side='left')
        self.cancel_button = ttk.Button(actions, text='取消', command=self.cancel.set, state='disabled')
        self.cancel_button.pack(side='left', padx=10)
        self.open_button = ttk.Button(actions, text='打开输出目录', command=self.open_output)
        self.open_button.pack(side='right')
        ttk.Label(actions, textvariable=self.status).pack(side='left', padx=8)
        bar = ttk.Frame(outer); bar.pack(fill='x', pady=(0, 8))
        self.button(bar, '扫描文件夹', self.scan).pack(side='left')
        self.button(bar, '添加歌曲', self.add_files).pack(side='left', padx=8)
        self.button(bar, '清空列表', self.clear_files).pack(side='left')
        check = ttk.Checkbutton(bar, variable=self.selected_only, text='只转换选中歌曲')
        check.pack(side='left', padx=14); self.controls.append(check)
        ttk.Label(bar, textvariable=self.summary, foreground='#61796f').pack(side='right')
        # Reserve progress/log space before the expandable song list. This keeps
        # the primary actions visible at high DPI and on smaller displays.
        footer = ttk.Frame(outer); footer.pack(side='bottom', fill='x')
        ttk.Label(footer, textvariable=self.current).pack(anchor='w', pady=(10, 5))
        self.file_progress = ttk.Progressbar(footer, maximum=100)
        self.file_progress.pack(fill='x')
        self.log = tk.Text(footer, height=2, relief='flat', bg='#e8eeeb', fg='#425c52',
                           font=('Microsoft YaHei UI', 9), padx=10, pady=7, state='disabled')
        self.log.pack(fill='x', pady=(10, 0))
        container = ttk.Frame(outer); container.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(container, columns=('song', 'size', 'format', 'status'),
                                  show='headings', selectmode='extended')
        for name, label, width in [('song', '歌曲', 440), ('size', '大小', 90),
                                   ('format', '文件类型', 100), ('status', '转换状态', 260)]:
            self.tree.heading(name, text=label)
            self.tree.column(name, width=width, minwidth=70, stretch=name in ('song', 'status'))
        self.tree.tag_configure('success', foreground='#087f65')
        self.tree.tag_configure('failed', foreground='#b33835')
        scrollbar = ttk.Scrollbar(container, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side='left', fill='both', expand=True); scrollbar.pack(side='right', fill='y')

    def log_line(self, text):
        self.log.configure(state='normal'); self.log.insert('end', f'{datetime.now():%H:%M:%S}  {text}\n')
        self.log.see('end'); self.log.configure(state='disabled')

    def choose_source(self):
        p = filedialog.askdirectory(initialdir=self.source.get(), title='选择 QQ 音乐下载目录')
        if p:
            self.source.set(p); self.scan()

    def choose_output(self):
        p = filedialog.askdirectory(initialdir=self.output.get() if Path(self.output.get()).is_dir() else ROOT,
                                    title='选择转换结果保存目录')
        if p:
            self.output.set(p)

    def import_keys(self):
        p = filedialog.askopenfilename(title='选择歌曲密钥缓存', filetypes=[('歌曲密钥 JSON', '*.json')])
        if p:
            try:
                cache = KeyCache(p)
                self.cache_path.set(p)
                self.log_line(f'已选择歌曲密钥缓存，共 {len(cache.entries)} 项。')
                self.refresh_statuses(cache)
            except Exception as e:
                messagebox.showerror('导入失败', str(e))

    def refresh_statuses(self, cache):
        for iid, p in self.paths.items():
            try:
                m = inspect_file(p)
                status = '已有密钥，可离线转换' if m.ekey or cache.get(m) or m.kind not in ('musicex', 'qmc2') else '需要获取歌曲密钥'
            except Exception as e:
                status = str(e)
            self.tree.set(iid, 'status', status)

    def clear_files(self):
        self.tree.delete(*self.tree.get_children()); self.paths.clear()
        self.summary.set('0 首歌曲')

    def add_paths(self, paths):
        existing = set(self.paths.values())
        for p in paths:
            if p not in existing:
                iid = self.tree.insert('', 'end', values=(p.name, f'{p.stat().st_size/1024/1024:.1f} MB',
                                                          p.suffix[1:].upper(), '等待转换'))
                self.paths[iid] = p; existing.add(p)
        total = sum(p.stat().st_size for p in self.paths.values() if p.exists())
        self.summary.set(f'{len(self.paths)} 首 · {total/1024**3:.2f} GB')
        try:
            self.refresh_statuses(KeyCache(self.cache_path.get()))
        except Exception as e:
            self.log_line(str(e))

    def scan(self):
        if self.busy:
            return
        if not Path(self.source.get()).is_dir():
            self.status.set('请先选择有效的歌曲目录。'); return
        try:
            paths = collect_files([self.source.get()], self.output.get())
            self.clear_files(); self.add_paths(paths)
            self.status.set('扫描完成'); self.log_line(f'找到 {len(paths)} 首歌曲，原文件保持不变。')
        except Exception as e:
            self.log_line('扫描失败：'+str(e))

    def add_files(self):
        paths = filedialog.askopenfilenames(title='添加歌曲', initialdir=self.source.get(),
            filetypes=[('支持的音频', '*.mflac *.mflac0 *.qmcflac *.flac *.mp3 *.wav *.ogg *.m4a'), ('全部文件', '*.*')])
        self.add_paths(collect_files(paths))

    def set_busy(self, busy):
        self.busy = busy
        for w in self.controls:
            if isinstance(w, ttk.Combobox):
                w.configure(state='disabled' if busy else 'readonly')
            else:
                w.configure(state='disabled' if busy else 'normal')
        self.cancel_button.configure(state='normal' if busy else 'disabled')

    def start(self):
        if self.busy:
            return
        ids = self.tree.selection() if self.selected_only.get() else self.tree.get_children()
        jobs = [(iid, self.paths[iid]) for iid in ids]
        if not jobs:
            messagebox.showinfo('没有歌曲', '请添加歌曲，或先选中需要转换的歌曲。'); return
        output = self.output.get().strip()
        if not output:
            messagebox.showerror('输出目录无效', '请选择保存转换结果的目录。'); return
        try:
            cache = KeyCache(self.cache_path.get())
        except Exception as e:
            messagebox.showerror('密钥缓存错误', str(e)); return
        target = 'both' if self.target.get().startswith('同时') else ('mp3' if self.target.get().startswith('MP3') else 'flac')
        bitrate = int(self.bitrate.get()); online = self.online.get()
        self.save_config(); self.cancel.clear(); self.results = []
        self.set_busy(True); self.file_progress['value'] = 0; self.status.set(f'0 / {len(jobs)}')
        self.log_line('开始转换。'+('允许向腾讯获取歌曲密钥。' if online else '离线模式。'))
        threading.Thread(target=self.worker, args=(jobs, output, target, bitrate, online, cache), daemon=True).start()

    def worker(self, jobs, output, target, bitrate, online, cache):
        engine = Converter(cache, online); results = []
        try:
            for index, (iid, path) in enumerate(jobs, 1):
                if self.cancel.is_set():
                    break
                self.events.put(('begin', iid, index, len(jobs), path.name))
                start = time.monotonic()
                try:
                    result = engine.convert(path, output, target, bitrate, self.cancel,
                        progress=lambda v: self.events.put(('progress', v)),
                        log=lambda s: self.events.put(('log', s)))
                except Cancelled:
                    result = ConversionResult(str(path), 'cancelled', [], 0, '已取消')
                except Exception as e:
                    result = ConversionResult(str(path), 'failed', [], round(time.monotonic()-start,3), str(e))
                results.append(asdict(result)); self.events.put(('result', iid, result))
                if result.status == 'cancelled':
                    break
        finally:
            engine.close()
            report = {'created_at': datetime.now().astimezone().isoformat(), 'format': target,
                      'bitrate_kbps': bitrate, 'total': len(jobs), 'processed': len(results),
                      'success': sum(r['status']=='success' for r in results),
                      'failed': sum(r['status']=='failed' for r in results),
                      'cancelled': self.cancel.is_set(), 'results': results}
            report_path = None
            try:
                report_path = Path(output) / f'转换报告-{datetime.now():%Y%m%d-%H%M%S-%f}.json'
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
            except OSError as e:
                self.events.put(('log', '报告保存失败：'+str(e)))
            self.events.put(('done', report, report_path))

    def poll(self):
        for _ in range(500):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind, *data = event
            if kind == 'begin':
                iid, index, count, name = data
                self.tree.set(iid, 'status', '正在转换…'); self.tree.see(iid)
                self.current.set(f'[{index}/{count}]  {name}'); self.status.set(f'{index-1} / {count}')
                self.file_progress['value'] = 0
            elif kind == 'progress':
                self.file_progress['value'] = data[0] * 100
            elif kind == 'log':
                self.log_line(data[0])
            elif kind == 'result':
                iid, r = data
                label = '完成，已校验' if r.status == 'success' else r.error
                self.tree.set(iid, 'status', label); self.tree.item(iid, tags=(r.status,))
                self.log_line(Path(r.source).name+'：'+label)
            elif kind == 'done':
                report, path = data; self.results = report['results']; self.set_busy(False)
                text = f'成功 {report["success"]} 首，失败 {report["failed"]} 首'
                if report['cancelled']:
                    text += '（已取消剩余任务）'
                self.status.set(text); self.current.set(text)
                if not report['cancelled']:
                    self.file_progress['value'] = 100
                self.log_line(text)
                if path:
                    self.log_line('详细报告：'+str(path))
        self.after(80, self.poll)

    def open_output(self):
        try:
            p = Path(self.output.get()); p.mkdir(parents=True, exist_ok=True)
            os.startfile(p)
        except OSError as e:
            messagebox.showerror('无法打开目录', str(e))

    def on_close(self):
        if self.busy:
            self.cancel.set(); self.status.set('正在取消任务，清理临时文件…')
            self.after(150, self.wait_close)
        else:
            self.save_config(); self.destroy()

    def wait_close(self):
        if self.busy:
            self.after(150, self.wait_close)
        else:
            self.save_config(); self.destroy()


if __name__ == '__main__':
    App().mainloop()
