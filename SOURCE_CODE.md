# 对应源码、修改与重新构建

本项目自身代码采用 GPL-3.0-only。用户可以获取、修改、重新构建和再分发本程序，
无需向原作者购买额外授权；再分发时须遵守 GPL 及各第三方组件的原有许可。

## 获取与成品对应的源码

1. 打开“帮助 → 项目与第三方许可证 → 构建版本”，查看 `commit`。
2. 使用同一记录中的 `application_source` 下载该提交的完整项目源码，内含构建脚本、依赖配置及测试。
   自动 Release 页面也提供同一提交的源码 ZIP 与构建说明链接，不要用不断变化的 `dev` 或 `main` 代替该提交。
3. `packages`、`runtime`、`qt` 和 `7zip_source` 给出实际使用版本的第三方完整源码地址。
   包含 SHA-256 的项目应先核对散列；Qt/PySide 源码覆盖对应库、上游构建脚本和第三方源码。
4. `working_tree_modified: true` 表示本地开发预览含未提交改动，上述提交链接不能完整代表它。
   不应直接再分发该预览；先提交全部相关源码并从干净检出重新构建。

第三方库未在本项目中修改；不以安装包、wheel 或二进制引擎冒充对应源码。
Python/OpenSSL 补丁版本因平台构建环境可能不同，请使用成品记录中的版本。
下载服务由 GitHub、Python、Qt、7-Zip 及 PyPI 等上游提供；分发者仍负责确保下载长期可用。
镜像失效时应补充镜像或随成品提供源码，不能把责任转给最终用户。

## 重建应用与替换库

完整环境和平台命令见源码中的 `docs/DEVELOPMENT.md`。

```sh
python3.13 -m venv .venv
# macOS
source .venv/bin/activate
# Windows PowerShell 对应命令：.venv\Scripts\Activate.ps1
python -m pip install -r requirements-build.txt -e ".[dev]"
python scripts/fetch_engine.py
python scripts/build.py
python scripts/smoke_build.py
```

使用 `BUILD_INFO.json` 记录的 Python 补丁版本与依赖版本，可以更接近原构建环境。
这些步骤用于重建可运行程序，不承诺压缩包、时间戳或签名逐字节可复现。
`scripts/fetch_engine.py` 始终校验官方引擎包的固定 SHA-256。

要修改 Qt / PySide / Shiboken：下载 `third_party/qt-sources.json` 中的源码，依上游随源
构建说明编译 Qt Base、Qt SVG，再对该 Qt 构建 PySide 与 Shiboken，将生成的库和 wheel
安装到项目虚拟环境后重新执行 `scripts/build.py`。运行环境中这些库必须彼此 ABI 兼容。
修改或升级后同时更新许可快照、版本记录，并保留修改说明及修改后对应源码。

Windows 的单文件 EXE 会在每次启动时释放内置库，直接编辑临时目录不是持久替换办法；
使用上述项目源码和构建脚本，把替换后的库重新打包即可。macOS 也可通过重新构建 APP
运行修改后的库，无需取得原作者签名密钥；脚本沿用本地构建签名，不要求正式 Developer ID。
程序不校验或锁定 Qt 库散列，也不禁止为调试库修改而进行逆向工程。

要替换 7-Zip，可替换项目 `vendor/7zip/<平台>/` 中的构建副本后重新打包；
Windows 同时提供匹配的 `7z.dll`。仅在没有内置引擎时，程序才读取 `NEO_7ZIP` 指定的命令行程序。
替换时同时更新许可声明、构建脚本中的版本记录和修改说明，提供对应源码；不要更改官方包校验值来冒充官方引擎。

## 发布者检查

- 从干净且已公开的提交构建，确保源码链接对应实际成品；历史 MIT 授权不因新版 GPL 而撤销。
- 保留许可文本、版权、第三方归属、构建脚本及对应版本源码获取信息。
- 保证接收者实际可以安装和运行使用修改后库重建的程序；额外分发条款不得取消这些权利。
- 非官方下载的 Python/Qt/OpenSSL、定制 wheel、其他平台或新增依赖，需要重新核对源码与二进制对应关系。
