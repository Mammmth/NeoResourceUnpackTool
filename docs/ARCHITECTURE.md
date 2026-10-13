# 架构与实现约定

以 `src/neo_unpack/` 当前实现为依据。产品规则见 [REQUIREMENTS.md](REQUIREMENTS.md)，构建步骤见 [DEVELOPMENT.md](DEVELOPMENT.md)。

## 技术选择

- Python 编排流程，PySide6 Essentials 提供跨平台 GUI，PyInstaller 打包运行环境。
- 完整官方 7-Zip 作为独立子进程，统一处理 ZIP、RAR、7z、分卷和加密文件名。不要替换为缺少 RAR 等能力的精简引擎。
- Python `zipfile`、`struct` 等处理结构验证和受约束的 ZIP 修复；`pyzipper` 处理非 ASCII ZIP 密码的 UTF-8 兼容回退。
- GUI 与 CLI 共用 `UnpackTask`；格式、递归规则不绑定 Qt。
- 使用临时副本而非原地修改，便于失败排查和同一会话继续。

## 模块责任

| 模块 | 责任与边界 |
| --- | --- |
| `model.py` | `Settings`、`ArchiveInfo`、`VolumeGroup`、`Result` 及面向用户的异常类型 |
| `volumes.py` | 验证顶层选择、排序分卷、只对传入文件集合分组；不做源目录扫描 |
| `detect.py` | 分块扫描 ZIP/RAR/7z 签名并校验结构；`OffsetReader` 提供载体偏移后的 ZIP 视图 |
| `ziprestore.py` | PKZIP 分卷标准化，重建跨盘中央目录偏移及相关 ZIP64 元数据 |
| `ziprepair.py` | 修改副本条目的 bit 0，读至 EOF 检查数据和 CRC；失败回滚对应字段 |
| `engine.py` | 寻找引擎、子进程取消、目录预检查、解压、错误分类和 UTF-8 ZIP 回退 |
| `workflow.py` | `UnpackTask` 组织还原、密码尝试、递归、缓存、冲突处理和最终发布 |
| `config.py` | 本机设置加载、校验、临时文件写入后替换；非 Windows 设置文件权限为 0600 |
| `gui.py` | 文件选择、密码管理、配置、`Worker(QThread)`、状态及目录入口 |
| `cli.py` | 命令行适配；读取本机设置与可选密码 TXT，调用同一工作流 |
| `selftest.py` | 成品内实际加载 Qt、字体、内置引擎，验证普通 ZIP 和 UTF-8 密码 ZIP，输出报告与截图 |

## 一次任务的调用顺序

1. GUI `start_task()` 保存并验证配置，创建 `Worker`；CLI 直接构造 `UnpackTask`。
2. `UnpackTask.__init__()` 调用 `make_group()`，建立缓存目录并记录输入大小、修改时间。
3. `run()` 检查输入是否变化，从深度 1 调用 `_walk()`。
4. `prepare()` 识别格式，复制并剥离前缀；数字分卷合并、RAR 分卷规范副本名称、PKZIP 分卷重建；ZIP 再检查伪加密。
5. `unpack()` 先探测是否需要密码，再顺序尝试配置密码；成功后缓存解压节点。
6. `_walk()` 展开包装目录，按需求规则收集最终文件或进入下一层分支。
7. `_publish()` 在目标所在目录中暂存整理结果，处理冲突后逐项移入目标。它不是跨所有文件的一次原子事务，不应对崩溃恢复作额外承诺。
8. 成功写任务记录后删除缓存，返回 `Result`；异常写记录后向调用方抛出，缓存保留。

## 数据契约

`Settings` 字段及默认值：

| 字段 | 默认值 | 取值/含义 |
| --- | --- | --- |
| `output_mode` | `folder` | `direct` / `folder` / `fixed` / `fixed_folder` |
| `fixed_directory` | 空字符串 | 固定目录模式必须填写 |
| `depth` | `auto` | `one` / `auto` |
| `keep_intermediates` | `False` | 是否复制过程目录至最终输出 |
| `passwords` | 空列表 | 用户配置字符串；实际尝试过滤空值并保持顺序去重 |

- `ArchiveInfo(kind, offset)`：真实格式和压缩结构起点；不表示内容一定完整或密码一定正确。
- `VolumeGroup(paths, style, name)`：已排序的一个逻辑包。`zip_split` 的 `entry` 是末尾 `.zip`，其他组的入口为首卷。
- `Result(output, layers, archives, session)`：结果目录、最大深度、实际成功解包数量；成功后 `session` 为 `None`。
- 配置增加字段应同时更新模型、校验、GUI、序列化与相应测试。当前加载器忽略未知字段；没有显式配置版本迁移框架。

## 缓存与续接

任务目录由系统临时目录下的 `neo-unpack-*` 创建，可通过构造参数 `cache_root` 指定根目录。主要内容：

```text
neo-unpack-*/
  task.json
  process.log
  过程/
    001_<名称>/
      还原包/
      合并包.zip    # PKZIP 场景
      修复包.zip    # 有验证通过的修复时
      解压结果/
```

`prepared`、`extracted`、`detected` 字典和 `preferred_passwords` 在内存中维护。重试复用同一 `UnpackTask`；输入大小或修改时间变化会要求重新选择。输入完整散列不是当前续接校验机制。

`task.json` 记录状态、输入、成功节点路径和不含密码的配置；`process.log` 保存过程消息。它们用于排查，不是可加载的恢复协议。关闭窗口后没有自动恢复路径。若任务构造前即失败，不一定已有缓存。

输出选择保留中间产物时复制 `过程/`，不是把整个会话目录及任务日志都发布出去。完成后任务临时目录仍会清理。

## 引擎、密码与取消

引擎搜索顺序为应用内置路径、`NEO_7ZIP` 指定路径、系统 `PATH`。发行包需包含 `7z.exe + 7z.dll` 或 `7zz`，不能依赖用户系统安装。

子进程使用参数列表，不经过 shell；关闭标准输入，输出到临时文件，避免输出管道填满。Windows 使用 `CREATE_NO_WINDOW`。不把包含密码的完整命令记录到日志。

无密码探测也传入显式随机探测值，不能使用裸 `-p`：裸参数可能使引擎等待交互输入。探测值不是新增候选密码库。

Windows 7-Zip 的 ZIP 密码编码使用系统 ANSI 代码页；失败且用户密码含非 ASCII 字符时，使用 `pyzipper` 再尝试同一密码的 UTF-8 字节。此回退也执行路径/链接检查和分块取消；不能绕过原有安全检查。

`threading.Event` 贯穿识别、复制、合卷、修复及解压。取消引擎时先终止，必要时强制结束；GUI 在线程结束后恢复控件状态。不要在主线程执行大文件处理。

## 错误及扩展原则

- `InvalidInput`、`MissingVolume`、`UnsafeArchive`、`PasswordRequired`、`Cancelled` 均继承 `UnpackError`。
- GUI 区分等待密码、取消、一般错误；未知异常显示通用提示，不展示可能包含密码的子进程参数或 traceback。
- 同一窗口一般失败/取消后也可尝试继续，但只有保留下来的成功节点可以复用；不能承诺所有错误可恢复。
- 新格式先补结构识别、适配及样本测试，再接入工作流。不要只增加后缀名单。
- 修改递归时同时检查 TXT 伪装、ZIP 文档容器、资源目录和缺卷混合内容，避免改变已确认的停止规则。
