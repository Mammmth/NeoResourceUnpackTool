# 项目与第三方许可证

Neo Resource Unpack Tool — Copyright (c) 2026 Mammmth。

本版本项目自身代码采用 **GNU General Public License version 3 only（GPL-3.0-only）**。
允许依协议使用、修改及再分发，不提供任何担保；完整条款见 [LICENSE](LICENSE)。
这次许可变更不撤销此前已按 MIT 发布版本的授权。第三方代码、测试样本及独立引擎
继续适用原有许可，不被本项目的 GPL 声明重新授权。

对应源码及重新构建方法见 [SOURCE_CODE.md](SOURCE_CODE.md)。成品中的
[BUILD_INFO.json](BUILD_INFO.json) 记录源码提交、平台、实际依赖版本、源码下载地址及可用的校验值。
下载成品的用户和后续分发者均可获取、修改并重新构建程序及所用的开源库。

## 7-Zip 26.04

Copyright (C) 1999–2026 Igor Pavlov；其他权利人详见其许可文件。

本程序将官方未修改的完整 7-Zip 作为独立子进程运行。Windows 使用 `7z.exe` 和
`7z.dll`，macOS 使用 `7zz`；它们是随包分发的独立组件，不是本项目重新授权的代码。
主要代码采用 LGPL-2.1-or-later，部分代码采用 BSD-2-Clause、BSD-3-Clause，RAR 解压
部分还适用 unRAR 限制（不得用相关代码重新实现 RAR 压缩算法）。

- [所分发平台的官方许可、版权及 unRAR 声明](third_party/7zip-License.txt)
- [LGPL 2.1 全文](third_party/LGPL-2.1.txt)
- [官网与源码入口](https://www.7-zip.org/)
- [26.04 完整源码](https://www.7-zip.org/a/7z2604-src.tar.xz)

构建会从经 SHA-256 校验的官方引擎包复制对应平台的 `License.txt`，而非在 Windows 包内沿用 Mac 文本。

## Qt / PySide6 Essentials / Shiboken6 6.12.0

Copyright (C) The Qt Company Ltd. and other contributors，具体年份和其他权利人见下面的上游声明。
本程序使用这些组件的 **LGPLv3 许可选项**，未修改其源码；项目自身使用 GPL-3.0-only。
用户享有 LGPL 规定的库修改、替换、重新链接，以及为调试这些修改进行逆向工程的权利。

- [LGPLv3 全文](third_party/Qt-LGPL-3.0.txt)；[GPLv3 全文](third_party/Qt-GPL-3.0.txt)
- [Qt Base 及其第三方声明](third_party/qt/qtbase-NOTICES.txt)
- [Qt SVG 及其第三方声明](third_party/qt/qtsvg-NOTICES.txt)
- [PySide / Shiboken 及其第三方声明](third_party/qt/pyside-NOTICES.txt)
- [固定版本完整源码下载地址及 SHA-256](third_party/qt-sources.json)
- [Qt 官方许可说明](https://www.qt.io/development/open-source-lgpl-obligations)

源码快照的声明包含可选、开发或测试组件的信息，不表示其中每一项都装入了成品；
具体使用范围应结合实际二进制与构建记录判断。附带商业许可等备选条款的文本不授予商业许可。
Qt 自身及其内部第三方依赖的版权与许可一并保留，不能只保留 LGPL 通用文本。

## Python 运行时与 OpenSSL

成品内含 Python 3.13 运行时及构建环境中相应的 OpenSSL 3 库；实际补丁版本以
[BUILD_INFO.json](BUILD_INFO.json) 为准。

- Python：Python Software Foundation 及其他贡献者，适用 PSF 等许可。
  [该版本完整许可与历史版权声明](third_party/Python-LICENSE.txt)。
  [该版本第三方许可与致谢](third_party/Python-NOTICES.txt) 另保留 expat、libffi、zlib、libmpdec 等上游声明；其中也可能包含未打包模块。
- OpenSSL：The OpenSSL Project Authors，OpenSSL 3 适用 Apache-2.0。
  [该版本许可](third_party/OpenSSL-LICENSE.txt)。

以上文件在构建时从对应版本的官方源码收集；仅克隆项目、尚未构建时不存在。
程序不修改这些组件的源码；上游发行包包含的第三方声明应随对应运行时保留。

## Python 包

下列包的原始许可文件由构建脚本从实际安装的发行包收集到
`third_party/package-licenses/`，精确版本和对应源码下载链接写入 `BUILD_INFO.json`。

| 组件 | 当前固定版本 | 许可与用途 |
| --- | --- | --- |
| pyzipper | 0.4.0 | MIT；继承的 Python 代码另有 PSF 声明；ZIP 密码兼容 |
| pycryptodomex / PyCryptodome | 3.24.1 | BSD 与公有领域部分；加密算法 |
| charset-normalizer | 3.5.1 | MIT；旧 ZIP 文件名编码推测 |
| PyInstaller | 6.22.3 | GPLv2-or-later 带打包例外，部分 Apache-2.0；成品启动与打包 |
| pyinstaller-hooks-contrib | 2026.8 | 按包内 LICENSE 的 GPL/Apache 划分；打包及运行时 hooks |

PyInstaller 的打包例外允许成品采用与依赖兼容的许可证，本项目选择 GPL-3.0-only。
pytest、pytest-timeout、certifi 等构建或测试工具不因列入构建依赖就视为应用运行时；
若改变打包范围使其进入成品，须同步补齐相关声明。

## 测试样本

RAR 回归样本来自 Marko Kreen 的 rarfile 项目，适用 ISC，声明位于源码的
`tests/fixtures/rar/LICENSE`；这些样本不装入桌面应用。

## 再分发

再分发成品时请保留整个 `legal/` 目录及源码获取信息，并按各组件许可证满足对应源码提供义务。
单文件 EXE 中的材料运行时解包到临时目录，可在“帮助 → 项目与第三方许可证”中阅读或复制。
仅给第三方项目名称或首页，不能替代完整许可文本与实际版本的对应源码获取安排。
