# Neo Resource Unpack Tool

Windows / macOS 自动解压桌面工具。一次处理一个压缩包或一组分卷，自动完成真实格式识别、前置载体剥离、ZIP 伪加密修复、常用密码尝试、递归解压及最终目录整理。

## 使用方式

1. 打开 Windows 的 `NeoResourceUnpackTool.exe` 或 macOS 的 `NeoResourceUnpackTool.app`。发行包内置 Python、Qt 和完整 7-Zip，无需另行安装。
2. 配好解压位置、递归方式和产物保留方式；在“管理密码”中添加密码或导入 UTF-8 TXT（每行一个密码）。
3. 拖入一个文件，或**一次拖入同一组的全部分卷**。不支持多个独立压缩包组成的批量任务，也不会从源目录自动补选未拖入的分卷。
4. 点击“开始解压”。密码均未匹配时，在当前窗口补充密码，再点击“继续当前任务”，已完成的层会被复用。
5. 完成后点击“打开结果目录”。原始文件始终保留，所有还原和修复只操作任务副本。

### 输入支持

- ZIP、RAR3/RAR5、7z；加密内容、RAR/7z 加密文件名。
- 仅改后缀的压缩包，以及在 ZIP/RAR/7z 前拼接图片等内容的文件。按二进制结构识别，不依赖扩展名。暂不支持把数据编码到图片像素中的隐写。
- `.7z.001`、`.zip.001` 数字分卷，`.part1.rar` / `.part01.rar` 等 RAR 分卷，`.rar + .r00` 老式 RAR 分卷，`.z01 + .zip` PKZIP 分卷。
- 分卷须保留可识别的同组编号。首卷可以带前置载体；各卷都被分别改名或独立加入载体的特殊样本，暂不保证兼容。
- 常见 ZIP 伪加密：本地文件头、中央目录或两者的 bit 0 被伪造。只在去掉标记后通过完整数据及 CRC 校验的条目上保留修复；真实加密条目不强行修改。RAR/7z 特殊伪加密暂不修复。

### 递归规则

| 本层内容 | 默认策略 |
| --- | --- |
| 单个正常或隐写压缩包 | 继续 |
| 多个压缩包（分卷按一个包计算） | 分别继续 |
| 压缩包 + TXT | 保留 TXT，继续解压压缩包 |
| 只有 TXT 或包含其他类型内容 | 停止当前分支 |

只有一个子文件夹的包装目录自动展开；沿途 TXT 保留。最终内容已有的目录结构保留，如 `app.exe + assets/`。忽略 `.DS_Store`、`__MACOSX` 等系统附加文件。选择“仅解压 1 层”时，还原、分卷合并和修复不额外计层数。

最终文件重名会自动改名，不覆盖已有文件。若包含资源文件夹的整组内容出现重名，会放入独立结果文件夹，避免只重命名 `assets` 等目录导致引用失效。

有对应文件后缀且结构明确的 DOCX/XLSX/PPTX、EPUB、APK、JAR 等内容文件，在递归时按最终文件保留，不继续拆开其 ZIP 容器。

### 输出配置

- 源目录直接解压。
- 源目录新建同名文件夹（默认）。
- 固定目录直接解压。
- 固定目录新建同名文件夹。
- 仅保留最终结果（默认），或同时保留 `_中间产物/` 中的还原包、修复副本和各层解压结果。

失败或取消时保留任务缓存；窗口里的“打开任务缓存”可以查看位置。当前版本的自动续接在**本窗口会话内**有效，关闭应用后缓存可手动取回，但不会自动恢复任务。成功后自动清理临时任务缓存。

### 配置与边界

- Windows 配置：`%APPDATA%/NeoResourceUnpackTool/settings.json`。
- macOS 配置：`~/Library/Application Support/NeoResourceUnpackTool/settings.json`。
- 密码保存在本机 JSON，不上传；macOS 配置文件使用当前用户读写权限。过程日志和任务记录不保存密码。
- 密码错误与加密数据损坏有时无法由引擎可靠区分，界面会明确说明；缺卷、非压缩文件、不支持的算法分别报错。
- 拒绝越界路径、符号链接、硬链接和 Windows 保留设备名。内置 64 层 / 10,000 包的异常递归保护。不会执行解出的程序。
- 解压和整理需要额外临时磁盘空间。正式签名/公证不包含在开发构建中，首次打开下载的应用可能受到系统验证限制。
- 首版构建目标：Windows x64，macOS 14+ 的 Apple Silicon 和 Intel（分别构建）。

## 开发

推荐 Python 3.13。

```sh
python -m venv .venv
# macOS
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-build.txt -e ".[dev]"
python scripts/fetch_engine.py
python -m neo_unpack
```

下载脚本只下载、校验和提取官方引擎，不进行系统级安装。代理不可用时可加 `--no-proxy`，或通过 `--archive` 使用已下载的官方包；仍会核对固定 SHA-256。

可用命令行验证：

```sh
neo-unpack-cli /path/to/archive.jpg --password-file /path/to/passwords.txt
neo-unpack-cli /path/to/a.7z.001 /path/to/a.7z.002 --output /path/to/results
```

## 测试与打包

```sh
python -m pytest -q
python scripts/build.py
python scripts/smoke_build.py
```

macOS 输出 `dist/NeoResourceUnpackTool.app` 及对应架构 ZIP；Windows 输出单文件 `dist/NeoResourceUnpackTool.exe`。必须在对应系统构建。打包测试会实际启动 Qt、定位内置引擎，并解压测试包；截图和结果保存在 `artifacts/`。

GitHub Actions 的 `Desktop build` 工作流在 `dev` 推送和 PR 时分别测试、构建 Windows x64、macOS Apple Silicon、macOS Intel，并上传可下载产物；也支持手动触发。具体执行结果以 Actions 为准。

测试包含真实 RAR3/RAR5、加密与分卷样本、真实 PKZIP 分卷、动态生成的加密 ZIP/7z、隐写与伪加密组合、递归停止、密码续接、文件重名、原文件保护及桌面实际任务。测试用 RAR 来源与许可证在 `tests/fixtures/rar/`。

## 代码结构

- `detect.py`：分块扫描与结构校验。
- `volumes.py`：单个任务 / 同组分卷验证。
- `ziprestore.py` / `ziprepair.py`：PKZIP 分卷标准化、ZIP 伪加密校验修复。
- `engine.py`：完整 7-Zip 引擎、路径检查、错误分类、取消。
- `workflow.py`：还原、密码尝试、递归、缓存、结果整理。
- `gui.py` / `config.py`：桌面界面与本机配置。

本项目 MIT 许可证仅覆盖项目自身代码。内置组件许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
