# 拾音 Shiyin

Windows 本地音频转换器，将 QQ 音乐 MFLAC 批量转换为 FLAC 或 MP3。

支持中文路径、批量队列、取消任务、同名文件保护，以及完整解码与音频 MD5 校验。Windows 便携版内置 Python 运行环境和 FFmpeg。

## 下载与使用

在 [Releases](https://github.com/hanlian17/shiyin-music-converter/releases/latest) 下载 `Shiyin-1.2.1-Windows-x64.zip`。

1. 完整解压 ZIP，双击 `Shiyin.exe`。
2. 选择歌曲文件夹及输出目录。
3. 选择 FLAC、MP3，或同时输出两种格式。MP3 默认 320 kbps。
4. 对需要歌曲密钥的新版 MFLAC，打开并登录 QQ 音乐，勾选“允许联网获取歌曲密钥”，或导入已有密钥 JSON。
5. 点击歌曲列表上方的“开始转换”。转换结果和 JSON 报告保存在输出目录。

适用系统为 Windows 10/11 x64。移动程序时请复制整个解压文件夹，`_internal` 文件夹包含运行组件。

## 文件格式与音频校验

- 支持 musicex v1 MFLAC、内嵌密钥的 QMC2 MFLAC，以及普通 FLAC、MP3、WAV、OGG、M4A 输入。
- 对重复且相互冲突的 musicex 文件尾，使用最内层原始资源信息。
- FLAC 输出会进行完整解码。含非标准数据的源音频会先校验完整 PCM MD5，再无损整理为标准 FLAC，随后复核 MD5、采样数、采样率和声道。
- MP3 支持 128 / 192 / 256 / 320 kbps。高于 48 kHz 或多于双声道的输入会转换为 MP3 支持的参数。
- 同名输出自动添加序号，取消或失败时清理临时文件。

MGG 和未知新加密版本暂未纳入支持范围。首次发布已用 175 个本地 MFLAC 样本验证 FLAC 转换及 PCM MD5，MP3 已验证实际样本和生成样本。其他实体电脑的兼容性仍需使用反馈。

## 歌曲密钥与登录态

新版 musicex 文件没有内嵌密钥，首次转换需要对应歌曲的 EKey。勾选联网选项后，拾音读取本机 QQ 音乐客户端的账号标识和明确的 `authst` 字段，通过 HTTPS 向腾讯 `u.y.qq.com` 请求歌曲密钥。音频在本机处理，登录态只驻留内存。

歌曲密钥保存在程序同级 `data/keys.json`，已有密钥可用于离线转换。JSON 格式为资源文件名到 Base64 EKey 的映射，可通过“导入密钥 JSON”选择。

自动获取密钥需要 QQ 音乐正在运行、登录账号具备相应歌曲权限，并与拾音以相同权限运行。QQ 音乐客户端或内部接口更新可能影响兼容性。

## 从源码运行

推荐 Python 3.14 x64。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

仓库提供 Windows x64 原生解码 DLL 及其 C 源码。模块加载失败时使用 Python 实现。

命令行批量转换也可以使用相同核心。

```powershell
.\.venv\Scripts\python.exe converter.py "歌曲目录" --format flac -o output
.\.venv\Scripts\python.exe converter.py "歌曲.mflac" --format both --bitrate 320 --allow-online -o output
.\.venv\Scripts\python.exe converter.py "歌曲.mflac" --key-cache "keys.json" -o output
```

## 测试与构建

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
powershell -ExecutionPolicy Bypass -File build.ps1
```

构建脚本安装固定版本依赖，生成 `dist/Shiyin/Shiyin.exe`，并将便携 ZIP 与 SHA-256 校验文件写入 `release/`。安装 MinGW x64 后可用下面的命令重新编译原生模块。

```powershell
gcc -shared -O3 -static-libgcc -o qmc_native.dll qmc_native.c
```

## 许可与来源

项目按 GNU GPL v3 或后续版本提供。QMC2 算法移植自 ownlight6/qmc-decoder，第三方依赖的来源、署名与许可见 [THIRD_PARTY.md](THIRD_PARTY.md) 和 `licenses/`。
