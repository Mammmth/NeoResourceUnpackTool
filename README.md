# Neo Resource Unpack Tool

Windows / macOS 自动解压桌面工具。一次处理一个压缩包或一组分卷，自动完成真实格式识别、前置载体剥离、ZIP 伪加密修复、常用密码尝试、递归解压及最终目录整理。

## 项目文档

- [AGENTS.md](AGENTS.md)：后续 agent 的工作入口、产品约束和验证约定。
- [需求与验收](docs/REQUIREMENTS.md)：已确认范围、递归规则、输出策略及验收追踪。
- [架构说明](docs/ARCHITECTURE.md)：模块责任、任务流程、配置/缓存契约和扩展边界。
- [开发与验证](docs/DEVELOPMENT.md)：环境准备、测试、三平台打包、CI 与兼容问题。
- [交接状态](docs/STATUS.md)：已验证基线、现有限制及后续建议。

## 使用方式

1. 打开 Windows 的 `NeoResourceUnpackTool.exe` 或 macOS 的 `NeoResourceUnpackTool.app`。发行包内置 Python、Qt 和完整 7-Zip，无需另行安装。
2. 配好解压位置、递归方式和产物保留方式；在“管理密码”中添加密码或导入 UTF-8 TXT（每行一个密码）。密码明文显示，添加、编辑、删除、导入后自动保存。
3. 拖入一个文件，或**一次拖入同一组的全部分卷**。不支持多个独立压缩包组成的批量任务，也不会从源目录自动补选未拖入的分卷。
4. 点击“开始解压”。密码均未匹配时，在当前窗口补充密码，再点击“继续当前任务”，已完成的层会被复用。
5. 完成后点击“打开结果目录”。原始文件默认保留；左侧“原文件”可选择“解压完成后删除”。所有还原和修复仍只操作任务副本。

界面左侧为文件拖拽和配置，右侧为运行状态与日志，中间分隔条可调整宽度。输出位置、层数、产物保留及原文件处理使用平铺单选项，文件名编码保留单选下拉框。第一条进度条表示整体运行状态及阶段，第二条显示当前压缩包的解压百分比；递归切换包或重试密码时重新计量。

### 输入支持

- ZIP、RAR3/RAR5、7z；加密内容、RAR/7z 加密文件名。
- ZIP 中文等非 ASCII 密码在引擎尝试失败后，自动补充 UTF-8 编码尝试，兼容跨系统生成的加密 ZIP。
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
- 原文件默认保留；选择“解压完成后删除”后，只在结果成功写入后直接删除本次选中的原文件（不移入废纸篓），分卷一并删除，不扫描其他文件。原文件已变化或删除失败会记录提示，解压结果仍可打开；可能存在部分分卷已删、部分未删的情况。

失败或取消时保留任务缓存；窗口里的“打开任务缓存”可以查看位置。当前版本的自动续接在**本窗口会话内**有效，关闭应用后缓存可手动取回，但不会自动恢复任务。成功后自动清理临时任务缓存；原文件清理未完成时保留缓存及日志用于查看。

### 文件名编码（代码页）

左侧配置区的“文件名编码”用于解决旧 ZIP 的文件名乱码，保存后会应用于递归的各层 ZIP：

- **默认**：保持引擎原来的处理方式，兼容旧配置。
- **自动检测**：优先识别 UTF-8，再对旧编码进行推测；短文件名或多种编码都合理时保留默认，并在日志中提示手动选择。
- **手动选择**：日语、简体中文、繁体中文、韩语、西欧、中欧、西里尔、土耳其、希腊、阿拉伯、希伯来、波罗的、泰语和 UTF-8。例如日文旧 ZIP 可选“日语（Shift-JIS / CP932）”。

压缩包中已有明确 UTF-8 标记或有效 Unicode 文件名信息时优先保留。此设置只解释 ZIP 文件名，不转换文件内容或密码，也不强制改写 RAR/7z 名称。自动检测不能保证猜对；手动编码无法完整解读名称时会提示更换编码。失败后更改编码再继续，会从原始输入重新处理，旧过程缓存保留。

### 配置与边界

- Windows 配置：`%APPDATA%/NeoResourceUnpackTool/settings.json`。
- macOS 配置：`~/Library/Application Support/NeoResourceUnpackTool/settings.json`。
- 密码保存在本机 JSON，不上传；macOS 配置文件使用当前用户读写权限。界面日志和磁盘 `process.log` 会显示验证成功的密码；失败候选值和任务清单不记录密码。成功后仍按原规则清理任务缓存，不额外归档日志。
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
neo-unpack-cli /path/to/japanese.zip --filename-encoding cp932
neo-unpack-cli /path/to/archive.zip --delete-sources
neo-unpack-cli /path/to/archive.zip --no-delete-sources
```

CLI 默认沿用本机“原文件”配置；可用 `--delete-sources` 或 `--no-delete-sources` 覆盖当前调用，命令行覆盖不会保存到配置。

## 测试与打包

```sh
python -m pytest -q
python scripts/build.py
python scripts/smoke_build.py
```

macOS 输出 `dist/NeoResourceUnpackTool.app` 及对应架构 ZIP；Windows 输出单文件 `dist/NeoResourceUnpackTool.exe`。必须在对应系统构建。打包测试会实际启动 Qt、定位内置引擎，并解压测试包；截图和结果保存在 `artifacts/`。

GitHub Actions 的 `Desktop build` 工作流在 `dev` / `main` 推送和 PR 时分别测试、构建 Windows x64、macOS Apple Silicon、macOS Intel，并上传可下载产物；也支持手动触发。`main` 每次直接提交或合并触发的推送，在全部测试和成品验证通过后自动创建 [Release](https://github.com/Mammmth/NeoResourceUnpackTool/releases)，附上本次推送的提交备注、三平台成品及 SHA-256 校验文件。`dev`、PR 和手动构建不发布 Release。自动标签采用提交 UTC 时间和短提交号，具体结果以 Actions 为准。

测试包含真实 RAR3/RAR5、加密与分卷样本、真实 PKZIP 分卷、动态生成的加密 ZIP/7z、隐写与伪加密组合、递归停止、密码续接、文件重名、原文件保护及桌面实际任务。测试用 RAR 来源与许可证在 `tests/fixtures/rar/`。

## 代码结构

- `detect.py`：分块扫描与结构校验。
- `volumes.py`：单个任务 / 同组分卷验证。
- `ziprestore.py` / `ziprepair.py`：PKZIP 分卷标准化、ZIP 伪加密校验修复。
- `engine.py`：完整 7-Zip 引擎、路径检查、错误分类、取消。
- `workflow.py`：还原、密码尝试、递归、缓存、结果整理。
- `gui.py` / `config.py`：桌面界面与本机配置。

本项目 MIT 许可证仅覆盖项目自身代码。内置组件许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
