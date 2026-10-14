# 开发、测试与打包

推荐 Python 3.13。项目元数据允许 Python 3.11+，但现有三平台构建验证使用 3.13；不要把声明的最低版本当作已测试版本。固定依赖见 [requirements-build.txt](../requirements-build.txt)。

## 本地准备

macOS：

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-build.txt -e ".[dev]"
python scripts/fetch_engine.py
python -m neo_unpack
```

Windows PowerShell：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt -e ".[dev]"
.\.venv\Scripts\python.exe scripts/fetch_engine.py
.\.venv\Scripts\python.exe -m neo_unpack
```

下文 `python` 均指该虚拟环境解释器；Windows 可以直接使用 `.\.venv\Scripts\python.exe`。

官方引擎由 `scripts/fetch_engine.py` 固定版本与 SHA-256，目前为 26.04。Windows 提取 MSI 中的引擎，不做系统级安装；macOS 提取官方 TAR 包。

下载故障时可用 `python scripts/fetch_engine.py --no-proxy`，或 `--archive /path/to/official-package` 使用离线包，仍须通过校验。不要固化个人代理地址或绕过证书、散列检查。`--platform` 只选择下载目标，不提供跨平台编译能力。

## 测试

```sh
python -m pytest -q
python -m pytest tests/test_core.py -k 'password or retry' -q
python -m pytest tests/test_rar.py tests/test_zip_split.py -q
python -m pytest tests/test_gui.py -q
git diff --check
```

测试需要可用的完整 7-Zip。GUI 测试自行设置离屏环境；配置和样本使用临时目录，不要让测试读写真实密码库。

| 测试文件 | 主要覆盖 |
| --- | --- |
| `test_core.py` | 二进制识别、扫描边界、ZIP 伪加密、密码/UTF-8、续接、递归、冲突、四种输出、取消、路径保护 |
| `test_rar.py` | 真实 RAR3/RAR5、固实、内容/文件名加密、新旧分卷、末卷缺失 |
| `test_zip_split.py` | 真实 `.z01 + .zip`，含伪加密组合 |
| `test_gui.py` | 窗口运行真实任务、任务创建失败后的状态、密码明文和空格、增删改/导入即时保存、保存失败、单选配置与进度状态 |
| `test_delete_sources.py` | 成功后删除、默认保留、分卷范围、失败/取消、文件替换、删除失败与结果保留、旧配置及 CLI 覆盖 |
| `test_progress.py` | 子进程退出前的进度、分块及回车/退格解析、取消、真实递归密码续接、成功密码日志、UTF-8 回退字节进度 |
| `test_password_screening.py` | ZIP 加密头初筛不读取内容、错误密码跳过、误筛后原流程恢复、不确定结果/UTF-8 回退、完整性与路径检查、失败保留原件及取消 |
| `test_password_frequency.py` | 次数配置兼容/校验、稳定降序、逐层重排、中间层/分支/分卷计数、失败后续接不重复、保存失败、CLI 覆盖隔离及临时密码 |
| `test_build_scripts.py` | 旧 Windows 控制台编码下脚本帮助输出 |
| `test_filename_encoding.py` | 手动代码页、自动检测歧义、Unicode 优先、加密回退、数字/PKZIP 分卷、ZIP64、原件保护、编码变更重建缓存 |

pytest 单项超时当前为 45 秒，CI 测试步骤为 5 分钟。新增大样本应区分“确需长时测试”与“交互等待/死锁”，不能仅通过无限延长超时掩盖问题。

二进制参数使用短 `ids`，否则 `pytest -v` 可能把兆字节的字节串展开进日志。真实 RAR fixture 的来源与许可位于 `tests/fixtures/rar/`，加入外部样本时保留来源及许可。

## 构建与成品验证

在对应系统及目标架构上运行：

```sh
python scripts/build.py
python scripts/smoke_build.py
```

| 系统 | 自动生成的产物 |
| --- | --- |
| Windows x64 | `dist/NeoResourceUnpackTool.exe` |
| macOS Apple Silicon | `dist/NeoResourceUnpackTool.app`、`dist/NeoResourceUnpackTool-macos-arm64.zip` |
| macOS Intel | `dist/NeoResourceUnpackTool.app`、`dist/NeoResourceUnpackTool-macos-x86_64.zip` |

`dist/releases/` 是首次交付时手工汇总的本地产物目录，构建脚本不会自动维护它；可能不存在或过期。

`smoke_build.py` 启动实际打包程序的 `--smoke-test` 入口，验证 Qt 字体、内置引擎、普通 ZIP、中文密码 ZIP、CP932 文件名及自动检测，以及密码初筛/误筛后兼容复试、密码自动保存/成功次数持久化与展示、Worker 进度和成功密码日志，输出 `artifacts/bundled-smoke.json`、主窗口 `.png`、`-encoding.png` 和 `-passwords.png`。检查报告 `ok`、`utf8_zip_password`、`password_screening`、`password_frequency`、`filename_encoding`、`gui_progress`、`password_autosave`、`delete_sources`、`font_families`，并实际查看截图；这不替代拖放和密码编辑等人工交互验收。

macOS 可用原生平台插件补验成品（此入口属于打包程序，不能改为 `python -m neo_unpack --smoke-test`）：

```sh
QT_QPA_PLATFORM=cocoa dist/NeoResourceUnpackTool.app/Contents/MacOS/NeoResourceUnpackTool --smoke-test artifacts/native-smoke.json
```

Windows 成品测试必须使用原生 `windows` Qt 平台插件，脚本已覆盖 CI 的离屏变量。此前 Windows 离屏截图所有文字显示方框，原生模式显示正常；不能因为只看到程序退出码为 0 就忽略截图。

macOS 可补充检查：

```sh
codesign --verify --deep --strict dist/NeoResourceUnpackTool.app
```

这只检查当前包签名结构，不能证明具有 Developer ID、Apple 公证或通过下载隔离检查。正式签名和公证尚未配置。不要用全局关闭系统安全验证的方式作为交付方案。

## GitHub Actions

[Desktop build](../.github/workflows/build.yml) 在 `dev` / `main` 推送、PR 和手动触发时执行：安装依赖 → 下载官方引擎 → 源码测试 → 打包 → 成品验证 → 上传产物与验证资料。

| Runner | 架构 | 产物名称 |
| --- | --- | --- |
| `windows-2022` | x64 | `NeoResourceUnpackTool-Windows-x64` |
| `macos-15` | Apple Silicon | `NeoResourceUnpackTool-macOS-Apple-Silicon` |
| `macos-15-intel` | Intel | `NeoResourceUnpackTool-macOS-Intel` |

另有 `Verification-*` 产物保存报告和截图。构建作业超时 30 分钟、矩阵不提前中止其他平台；`dev` / PR 同一 ref 的更新会取消同组旧流程，`main` 的每次运行均保留。当前没有文档路径过滤，所以仅文档推送也会触发完整构建。

### main 自动 Release

只有 `push` 到 `main` 才执行 `release` 作业，直接提交、合并、squash / rebase merge 均由最终推送触发。它依赖三平台 `build` 和独立的 `release-tests` 全部成功；`dev`、PR、手动运行均不会发布。配置需合入 `main` 后生效，不会自动合并分支。

[scripts/release.cjs](../scripts/release.cjs) 将 head（通常为合并提交）的标题和正文作为 Release 主描述，按 Markdown 展示；从完整 Git 历史读取 `before..head` 新增提交的标题及正文，放入折叠区；首次建立分支（before 全零）只取 head。长备注会截断并提供完整变更链接。提交备注作为数据处理，不插入 shell 命令或 Actions 表达式。

- 标签格式：`vMAJOR.MINOR.PATCH`，初始 `v0.1.0`。脚本分页读取远端 Tag 和 Release（含草稿），忽略历史时间戳及预发布后缀，取最高正式版本递增 PATCH；先查找当前提交是否已有版本，重跑优先复用，Tag 存在但 Release 尚未创建时也可继续。
- 手动升级：修改根目录 [VERSION](../VERSION) 为 `0.2.0`、`1.0.0` 等不带 `v` 的三段版本号并合入 `main`。该文件是最低目标版本：高于远端已占用版本时采用它，否则自动递增最高版本的 PATCH，不会降级或覆盖旧标签。文件保留该值即可，不必每次发布提交新版本号；Python 包元数据不自动改写。
- `release` 作业通过 `main-release` 并发组串行发布，`queue: max` 保留最多 100 个待发布作业，避免默认队列替换等待项。分配顺序按进入发布队列而非推送时间；构建失败不会运行该作业。上传失败留下带版本号的草稿，重跑复用；其他提交跳过被草稿占用的版本，可能出现尚未公开的版本空位。
- 旧草稿恢复后不把 Latest 从较新的正式版本退回；同名标签指向其他提交、同名 Release 源码不匹配、同一提交有多个正式版本标签时停止，人工核对后再重跑。
- 附件：`NeoResourceUnpackTool.exe`、`NeoResourceUnpackTool-macos-arm64.zip`、`NeoResourceUnpackTool-macos-x86_64.zip`、`SHA256SUMS.txt`。发布前核对三个包均存在且非空，校验文件针对内部成品，不是 Actions 外层 ZIP。
- 先创建草稿、上传全部附件、再公开。上传失败不公开；重跑同一次 Actions 可替换草稿附件并继续。已公开版本直接返回，不改写附件；同名标签或 Release 指向其他提交时停止。
- 工作流默认只读权限，仅发布作业获得 `contents: write`，使用内置 `GITHUB_TOKEN`，无需另设个人令牌。仓库/组织策略或标签规则若拒绝写入，会在发布作业报错；缺失历史也会停止备注生成。
- `release-tests` 在 Ubuntu / Node.js 24 上运行，可本地执行 `node --test tests/release.test.cjs`。测试覆盖真实临时 Git 合并备注、附件缺失/空文件、校验值、长备注、首次版本/补丁递增、手动次版本/主版本升级、草稿占号与恢复、Latest 保护、已发布重跑和权限/标签冲突；通过模拟 API 验证发布流程，不创建真实 Release。

失败时在 Actions 中重跑失败作业。语义版本新规则的真实发布仍需合入后的首次 `main` 推送验证；签名、公证和最低系统版本验收仍沿用现有限制。

交付前核对构建对应提交、每个平台结果及成品验证，下载时校验 GitHub 提供的 artifact digest。Actions 下载的外层 ZIP 内，Windows 是 EXE，Mac 是包含 APP 的第二层 ZIP。不能把外层 ZIP 的散列误写成内部成品散列。

## 已知开发陷阱

- **Windows 日志编码**：CI 设置 `PYTHONUTF8=1`，脚本/CLI 对可用输出流显式设置 UTF-8；无控制台打包程序的 `sys.stdout` 可能不可用。
- **引擎交互等待**：无密码探测不能用裸 `-p`；保留显式探测值和关闭输入逻辑。
- **ZIP 中文密码**：保留 UTF-8 回退以及成品内的实际加密 ZIP 验证。仅验证源码依赖可导入不足以证明打包完整。
- **ZIP 旧文件名代码页**：不能只传 `-mcp` 就宣称跨平台支持；保留 Unicode 元数据副本和真实 CP932/GBK 等文件名测试。`charset-normalizer` 升级后复查自动检测歧义、成品依赖及许可证。
- **路径与资源目录**：Windows 保留名、跨平台大小写冲突、空格和中文路径需同时考虑；不要为解决碰撞而拆散资源目录。
- **内置组件许可**：`scripts/build.py` 收集包许可证，其他文本位于 `third_party/` 和 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。升级时一起核查。
- **Linux**：核心部分有回退路径，不等于已支持 Linux 发行；构建脚本只接受 Windows/macOS。

## 手工验收及文档维护

GUI 或输入流程变更时，用临时测试目录核对拖放一组分卷、拒绝多个独立包、添加/编辑/删除/导入密码、错误密码后补充继续、取消、四种输出和打开结果。默认配置下确认源文件散列未变；启用删除时确认仅成功发布后删除选中原件/分卷，失败与取消保留，删除失败有提示；确认结果目录符合需求、日志仅显示验证成功的密码，任务清单和失败候选值不包含密码；检查双进度条、最小窗口布局和编码下拉菜单。

纯文档改动只需检查相对链接、代码路径/命令、需求一致性和 `git diff --check`。验证状态应写明对应提交，避免把旧的 58 项通过报告当作后续版本的证据。
