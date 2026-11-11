# 第三方组件与源码来源

本应用对应源代码随项目及便携包 `source/` 目录提供。基于 GPL-3.0 参考实现移植的部分及其组合程序按 GNU GPL v3 提供，详见 `LICENSE`。

- `qmc_crypto.py`、`qmc_native.c` 的 QMC2 算法及 TC-TEA 密钥派生参照并移植自 [ownlight6/qmc-decoder](https://github.com/ownlight6/qmc-decoder)，GPL-3.0；上游注明算法来源为 jixunmoe/qmc2-rust 与 bczhc/qmc-decrypt。保留来源署名，原 Rust 源码可通过上述仓库获取。
- `formats.py` 与 `qq_session.py` 的文件结构和腾讯请求协议参照同仓库 `src/ekey_fetch.rs` 及 [MGG_DECRYPTION_FLOW.md](https://github.com/ownlight6/qmc-decoder/blob/main/MGG_DECRYPTION_FLOW.md)。本项目修正了文件尾边界，并增加重复文件尾处理、定向登录态读取、无重定向请求与本地缓存。
- `mutagen` 1.48.1：音频标签和信息读取，GPL-2.0-or-later。项目：[mutagen](https://github.com/quodlibet/mutagen)。
- `imageio-ffmpeg` 0.8.1：FFmpeg 路径管理与分发，BSD-2-Clause。项目：[imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg)。
- 随附 FFmpeg 二进制用于 FLAC 解码验证与 LAME MP3 编码，其配置包含 GPL 组件；二进制版本及构建选项可运行该文件 `-version` 查看，条款可运行 `-L` 查看。对应项目：[FFmpeg](https://ffmpeg.org/)，Windows 构建来源：[Gyan FFmpeg builds](https://www.gyan.dev/ffmpeg/builds/)。再分发时应保留许可及对应构建源码义务。
- Python 3.14 与 Tcl/Tk：PSF、Tcl/Tk 许可，打包运行时包含相应组件。
- PyInstaller 6.22.3：GPL-2.0-or-later with bootloader exception；用于生成便携程序。[PyInstaller license](https://pyinstaller.org/en/stable/license.html)。

依赖包许可文本随便携包 `licenses/` 提供。QQ 音乐、QQMusic 是腾讯的商标；本应用与腾讯无关联。
